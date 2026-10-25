"""Describe photos and short texts as OpenCLIP embeddings, for the Content and
Notes & Pages modes.

The model is OpenCLIP ViT-B/32 (OpenAI weights), as ONNX files from Immich.
A photo's embedding and a text's embedding point the same way when the text
describes the photo, so photos are told apart by comparing them with prompts
like "a photo of food"; see thresholds.CLIP_LOGIT_SCALE.

Both models take one input at a time (a fixed batch of 1): the visual model a
float32 (1, 3, 224, 224) picture, the textual one int32 (1, 77) token ids.
"""

import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from photosort.ai import models, runtime
from photosort.imageinfo import UnreadableImage

_SIZE = 224  # the visual model's square input, in pixels
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
_CONTEXT = 77  # the textual model's token ids per text

# The analysis cache kind of pack's format. Bump the version (clip-v2, ...)
# whenever an embedding would come out differently: a new packing, a new
# model, or a change to load_for_clip or preprocess. Old entries are then
# never read.
CLIP_KIND = "clip-v1"
_DIM = 512
_PACKED = _DIM * 4  # float32


def pack(embedding: np.ndarray) -> bytes:
    """embedding as bytes in the "clip-v1" format; unpack reverses it exactly."""
    return np.asarray(embedding, "<f4").reshape(_DIM).tobytes()


def unpack(data: bytes) -> np.ndarray:
    """The (512,) float32 embedding packed by pack; raises ValueError if data isn't one."""
    if len(data) != _PACKED:
        raise ValueError("not a packed CLIP embedding: wrong length")
    return np.frombuffer(data, "<f4").astype(np.float32)  # copies


def load_for_clip(path: Path) -> Image.Image:
    """The photo as RGB, upright, its shortest side at least 224 (unless the photo
    itself is smaller); raises UnreadableImage.

    A JPEG is decoded at the smallest scale that keeps that size, which is
    much faster than decoding it whole.
    """
    try:
        with Image.open(path) as img:
            img.draft("RGB", (_SIZE, _SIZE))
            return ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:
        raise UnreadableImage(f"{path}: {exc}") from exc


def preprocess(img: Image.Image) -> np.ndarray:
    """img as the visual model's (3, 224, 224) float32 input.

    The shortest side is resized to 224 (bicubic; smaller photos are scaled
    up), the middle 224x224 is cut out, and each channel is normalised with
    CLIP's mean and standard deviation.
    """
    scale = _SIZE / min(img.size)
    size = (max(_SIZE, round(img.width * scale)), max(_SIZE, round(img.height * scale)))
    resized = img.convert("RGB").resize(size, Image.Resampling.BICUBIC)
    left = round((resized.width - _SIZE) / 2)
    top = round((resized.height - _SIZE) / 2)
    square = resized.crop((left, top, left + _SIZE, top + _SIZE))
    pixels = np.asarray(square, np.float32) / 255
    return np.ascontiguousarray(((pixels - _MEAN) / _STD).transpose(2, 0, 1))


def load_tokenizer(path: Path):
    """The tokenizers.Tokenizer in the file at path, importing tokenizers on first use."""
    import tokenizers

    return tokenizers.Tokenizer.from_file(str(path))


def tokenize(tokenizer, text: str) -> np.ndarray:
    """text as the textual model's (1, 77) int32 input.

    The tokenizer's ids already start and end with the start and end tokens.
    They are padded with 0, or cut to 77 keeping the end token last.
    """
    ids = tokenizer.encode(text).ids
    if len(ids) > _CONTEXT:
        ids = ids[:_CONTEXT - 1] + ids[-1:]
    out = np.zeros((1, _CONTEXT), np.int32)
    out[0, :len(ids)] = ids
    return out


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, np.float32).reshape(_DIM)
    return (vector / np.linalg.norm(vector)).astype(np.float32)


class ClipEngine:
    """Embeds photos and texts. The models are opened on first use, on the
    calling thread, so build it anywhere. Use it from one thread at a time,
    any thread."""

    def __init__(self, use_gpu: bool):
        self.use_gpu = use_gpu
        self._visual = None
        self._textual = None
        self._tokenizer = None
        self._open_lock = threading.Lock()  # so each model is opened only once
        self._texts: dict[str, np.ndarray] = {}  # text -> its unit embedding

    def _visual_session(self):
        with self._open_lock:
            if self._visual is None:
                self._visual = runtime.make_session(models.model_path("clip_visual"),
                                                    self.use_gpu)
            return self._visual

    def _textual_session(self):
        with self._open_lock:
            if self._textual is None:
                self._textual = runtime.make_session(models.model_path("clip_textual"),
                                                     self.use_gpu)
            if self._tokenizer is None:
                self._tokenizer = load_tokenizer(models.model_path("clip_tokenizer"))
            return self._textual, self._tokenizer

    def image_embedding(self, path: Path) -> np.ndarray:
        """The photo's (512,) float32 unit-length embedding; raises UnreadableImage."""
        x = preprocess(load_for_clip(path))[None]
        session = self._visual_session()
        return _unit(session.run(None, {session.get_inputs()[0].name: x})[0])

    def text_embeddings(self, texts: list[str]) -> np.ndarray:
        """The (n, 512) float32 unit embeddings of texts, one row per text.

        Each distinct text runs through the model once per engine; later
        calls reuse its embedding.
        """
        for text in texts:
            if text not in self._texts:
                session, tokenizer = self._textual_session()
                ids = tokenize(tokenizer, text)
                out = session.run(None, {session.get_inputs()[0].name: ids})[0]
                self._texts[text] = _unit(out)
        if not texts:
            return np.zeros((0, _DIM), np.float32)
        return np.stack([self._texts[text] for text in texts])
