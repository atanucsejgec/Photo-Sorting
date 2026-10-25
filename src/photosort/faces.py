"""Find faces in a photo and describe each one as a 512-number embedding.

Faces are found with SCRFD and described with ArcFace (InsightFace's
buffalo_l models). Two embeddings of the same person point the same way, so
similarity() of them is high; see thresholds.FACE_SURE.

Photos are decoded the right way up (EXIF orientation applied), at most
FACE_DECODE_PX on the long side. All boxes and landmarks are in that decoded
photo's pixels, whose size each Face carries as image_size.
"""

import struct
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from photosort.ai import models, runtime
from photosort.imageinfo import open_image
from photosort.thresholds import (
    FACE_CROP_MARGIN,
    FACE_DECODE_PX,
    FACE_DET_SCORE,
    FACE_DET_SIZE,
    FACE_EMBED_BATCH,
    FACE_MIN_PX,
    FACE_NMS_IOU,
)

Box = tuple[float, float, float, float]  # x1, y1, x2, y2

_STRIDES = (8, 16, 32)
_ANCHORS = 2  # SCRFD has two anchors per grid cell
_ALIGNED_PX = 112
# Where ArcFace expects the eyes, nose tip and mouth corners in its 112² crop.
_TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                      [41.5493, 92.3655], [70.7299, 92.2041]], np.float64)


@dataclass(frozen=True, eq=False)
class Face:
    box: Box
    landmarks: np.ndarray  # (5, 2): eyes, nose tip, mouth corners
    score: float
    embedding: np.ndarray  # (512,) float32, unit length
    image_size: tuple[int, int]  # (width, height) of the decoded photo


# The analysis cache kind of pack_faces' format. Bump the version (faces-v2,
# ...) whenever the faces found would come out differently: a new packing, a
# new model, or a change to thresholds.FACE_DECODE_PX, FACE_DET_SIZE, FACE_DET_SCORE,
# FACE_MIN_PX or FACE_NMS_IOU. Old entries are then never read.
FACES_KIND = "faces-v1"
_COUNT = struct.Struct("<I")
_FACE_HEAD = struct.Struct("<4ddII")  # box, score, image width and height
_FACE_ARRAYS = (5 * 2 + 512) * 4  # landmarks then embedding, float32
_FACE_SIZE = _FACE_HEAD.size + _FACE_ARRAYS


def pack_faces(faces: list[Face]) -> bytes:
    """faces as bytes in the "faces-v1" format; unpack_faces reverses it exactly."""
    parts = [_COUNT.pack(len(faces))]
    for face in faces:
        parts.append(_FACE_HEAD.pack(*face.box, face.score, *face.image_size))
        parts.append(np.asarray(face.landmarks, "<f4").reshape(5, 2).tobytes())
        parts.append(np.asarray(face.embedding, "<f4").reshape(512).tobytes())
    return b"".join(parts)


def unpack_faces(data: bytes) -> list[Face]:
    """The faces packed by pack_faces; raises ValueError if data isn't such."""
    if len(data) < _COUNT.size:
        raise ValueError("not packed faces: too short")
    (count,) = _COUNT.unpack_from(data)
    if len(data) != _COUNT.size + count * _FACE_SIZE:
        raise ValueError("not packed faces: wrong length")
    faces = []
    for n in range(count):
        offset = _COUNT.size + n * _FACE_SIZE
        *box, score, width, height = _FACE_HEAD.unpack_from(data, offset)
        arrays = np.frombuffer(data, "<f4", 5 * 2 + 512, offset + _FACE_HEAD.size)
        landmarks = arrays[:10].reshape(5, 2).astype(np.float32)  # copies
        embedding = arrays[10:].astype(np.float32)
        faces.append(Face(tuple(box), landmarks, score, embedding, (width, height)))
    return faces


def _load(path: Path) -> Image.Image:
    """The photo as RGB, upright, at most FACE_DECODE_PX long; raises UnreadableImage."""
    return open_image(path, FACE_DECODE_PX)


def decode_scrfd(outputs: list[np.ndarray], scale: float,
                 det_size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Turn SCRFD's raw outputs into (boxes (n,4), landmarks (n,5,2), scores (n,)).

    outputs[0..2] are the scores for strides 8, 16 and 32, outputs[3..5] the
    box distances and outputs[6..8] the landmark offsets, all in strides.
    Faces scoring below FACE_DET_SCORE are dropped and overlapping ones merged
    (NMS at FACE_NMS_IOU). Results are divided by scale, the factor the photo
    was resized by onto the det_size canvas, and sorted by score, highest first.
    """
    boxes, landmarks, scores = [], [], []
    for level, stride in enumerate(_STRIDES):
        cells = det_size // stride
        ys, xs = np.mgrid[:cells, :cells]
        centres = np.stack([xs, ys], axis=-1).reshape(-1, 2).astype(np.float32) * stride
        centres = np.repeat(centres, _ANCHORS, axis=0)
        score = outputs[level].reshape(-1)
        keep = score >= FACE_DET_SCORE
        dist = outputs[level + 3].reshape(-1, 4)[keep] * stride
        offsets = outputs[level + 6].reshape(-1, 5, 2)[keep] * stride
        c = centres[keep]
        boxes.append(np.concatenate([c - dist[:, :2], c + dist[:, 2:]], axis=1))
        landmarks.append(c[:, None, :] + offsets)
        scores.append(score[keep])
    boxes_all = np.concatenate(boxes).astype(np.float32)
    landmarks_all = np.concatenate(landmarks).astype(np.float32)
    scores_all = np.concatenate(scores).astype(np.float32)
    kept = _nms(boxes_all, scores_all, FACE_NMS_IOU)
    return boxes_all[kept] / scale, landmarks_all[kept] / scale, scores_all[kept]


def _nms(boxes: np.ndarray, scores: np.ndarray, max_iou: float) -> np.ndarray:
    """Indices of the boxes to keep, highest score first."""
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    order = np.argsort(-scores, kind="stable")
    keep = []
    while order.size:
        best, rest = order[0], order[1:]
        keep.append(best)
        x1 = np.maximum(boxes[best, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[best, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[best, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[best, 3], boxes[rest, 3])
        inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        union = np.maximum(areas[best] + areas[rest] - inter, 1e-9)
        order = rest[inter / union < max_iou]
    return np.array(keep, dtype=np.intp)


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """The 3x3 similarity transform (rotation, uniform scale, shift) taking src onto dst."""
    mu_src, mu_dst = src.mean(axis=0), dst.mean(axis=0)
    src_c, dst_c = src - mu_src, dst - mu_dst
    u, s, vt = np.linalg.svd(dst_c.T @ src_c / len(src))
    d = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1] = -1  # a reflection: turn it into a rotation
    rotation = u @ np.diag(d) @ vt
    scale = (s * d).sum() / src_c.var(axis=0).sum()
    transform = np.eye(3)
    transform[:2, :2] = scale * rotation
    transform[:2, 2] = mu_dst - scale * rotation @ mu_src
    return transform


def align(img: Image.Image, landmarks: np.ndarray) -> Image.Image:
    """The 112x112 crop with the face's landmarks moved onto ArcFace's template."""
    forward = _umeyama(np.asarray(landmarks, np.float64), _TEMPLATE)
    inverse = np.linalg.inv(forward)  # Pillow maps output pixels back to input
    return img.transform((_ALIGNED_PX, _ALIGNED_PX), Image.Transform.AFFINE,
                         tuple(inverse[:2].ravel()), Image.Resampling.BILINEAR)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """How alike two unit-length embeddings are: 1 is identical, near 0 unrelated."""
    return float(np.dot(a, b))


def face_crop(path: Path, box: Box, size: int = 160) -> Image.Image:
    """A size x size picture of the face in box, with a margin, for the UI.

    box is in the decoded photo's pixels, as in Face.box. The square is
    moved (and if need be shrunk) to stay inside the photo.
    """
    img = _load(path)
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1, 1) * (1 + 2 * FACE_CROP_MARGIN)
    side = min(side, img.width, img.height)
    left = min(max((x1 + x2 - side) / 2, 0), img.width - side)
    top = min(max((y1 + y2 - side) / 2, 0), img.height - side)
    square = img.crop((round(left), round(top), round(left + side), round(top + side)))
    return square.resize((size, size), Image.Resampling.LANCZOS)


class FaceEngine:
    """Detects and embeds faces. The models are opened on first use, on the
    calling thread, so build it anywhere. Use it from one thread at a time,
    any thread: the face scan's worker, or the naming dialog's analyses on
    pool threads, one after another."""

    def __init__(self, use_gpu: bool):
        self.use_gpu = use_gpu
        self._det = None
        self._rec = None
        self._open_lock = threading.Lock()  # so each model is opened only once

    def _detection(self):
        with self._open_lock:
            if self._det is None:
                self._det = runtime.make_session(models.model_path("face_detection"),
                                                 self.use_gpu)
            return self._det

    def _recognition(self):
        with self._open_lock:
            if self._rec is None:
                self._rec = runtime.make_session(models.model_path("face_recognition"),
                                                 self.use_gpu)
            return self._rec

    def detect(self, img: Image.Image) -> list[tuple[Box, np.ndarray, float]]:
        """The faces in img as (box, landmarks (5,2), score), highest score first.

        Faces whose box is shorter than FACE_MIN_PX on either side are left out.
        """
        session = self._detection()
        scale = FACE_DET_SIZE / max(img.size)
        small = img.resize((max(1, round(img.width * scale)),
                            max(1, round(img.height * scale))), Image.Resampling.BILINEAR)
        canvas = np.zeros((FACE_DET_SIZE, FACE_DET_SIZE, 3), np.float32)
        pixels = np.asarray(small, np.float32)
        canvas[:pixels.shape[0], :pixels.shape[1]] = pixels  # top-left letterbox
        x = ((canvas - 127.5) / 128.0).transpose(2, 0, 1)[None]
        outputs = session.run(None, {session.get_inputs()[0].name: x})
        boxes, landmarks, scores = decode_scrfd(outputs, scale, FACE_DET_SIZE)
        found = []
        for box, points, score in zip(boxes, landmarks, scores):
            if min(box[2] - box[0], box[3] - box[1]) >= FACE_MIN_PX:
                found.append((tuple(float(v) for v in box), points, float(score)))
        return found

    def embed(self, img: Image.Image, landmarks: np.ndarray) -> np.ndarray:
        """The unit-length 512-number embedding of the face at landmarks in img."""
        return self._embed_crops([align(img, landmarks)])[0]

    def _embed_crops(self, crops: list[Image.Image]) -> np.ndarray:
        """Embed aligned 112x112 crops: (n, 512) float32, unit rows.

        At most FACE_EMBED_BATCH crops go in one run, so a crowd photo never
        runs the GPU out of memory.
        """
        session = self._recognition()
        chunks = []
        for start in range(0, len(crops), FACE_EMBED_BATCH):
            x = np.stack([np.asarray(c, np.float32)
                          for c in crops[start:start + FACE_EMBED_BATCH]])
            x = ((x - 127.5) / 127.5).transpose(0, 3, 1, 2)
            chunks.append(session.run(None, {session.get_inputs()[0].name: x})[0])
        out = np.concatenate(chunks)
        out = out / np.linalg.norm(out, axis=1, keepdims=True)
        return out.astype(np.float32)

    def analyse(self, path: Path) -> list[Face]:
        """Every face in the photo at path; raises UnreadableImage."""
        img = _load(path)
        found = self.detect(img)
        if not found:
            return []
        embeddings = self._embed_crops([align(img, points) for _, points, _ in found])
        return [Face(box, points, score, embedding, img.size)
                for (box, points, score), embedding in zip(found, embeddings)]
