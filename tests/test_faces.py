import numpy as np
import pytest
from PIL import Image, ImageDraw

from photosort import faces
from photosort.ai import models
from photosort.faces import FaceEngine, align, decode_scrfd, face_crop, similarity
from photosort.imageinfo import UnreadableImage
from photosort.thresholds import FACE_DET_SIZE, FACE_MIN_PX

TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)
STRIDES = (8, 16, 32)

needs_models = pytest.mark.skipif(
    bool(models.missing(["face_detection", "face_recognition"])),
    reason="face models not installed")


def blank_outputs(det_size: int = FACE_DET_SIZE) -> list[np.ndarray]:
    """SCRFD outputs with every score 0: scores, box distances, landmark offsets."""
    counts = [(det_size // s) ** 2 * 2 for s in STRIDES]
    return ([np.zeros((n, 1), np.float32) for n in counts]
            + [np.zeros((n, 4), np.float32) for n in counts]
            + [np.zeros((n, 10), np.float32) for n in counts])


def put(outputs, stride: int, x: int, y: int, score: float, dist=(1, 1, 1, 1),
        offsets=None, anchor: int = 0, det_size: int = FACE_DET_SIZE) -> None:
    """Set one anchor's score, box distances and landmark offsets (in strides)."""
    level = STRIDES.index(stride)
    row = (y * (det_size // stride) + x) * 2 + anchor
    outputs[level][row, 0] = score
    outputs[level + 3][row] = dist
    if offsets is not None:
        outputs[level + 6][row] = np.asarray(offsets, np.float32).ravel()


def grey_jpeg(path, size, orientation=None):
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    Image.new("RGB", size, (128, 128, 128)).save(path, "JPEG", exif=exif)
    return path


# --- pure numpy -------------------------------------------------------------

def test_decode_scrfd_single_anchor():
    out = blank_outputs()
    offsets = [[0, 0], [1, 0], [0, 1], [-1, 0], [0, -1]]
    put(out, 8, 3, 2, 0.9, offsets=offsets)
    boxes, landmarks, scores = decode_scrfd(out, 0.5, FACE_DET_SIZE)
    np.testing.assert_allclose(boxes, [[32, 16, 64, 48]])
    centre = np.array([24, 16])
    expected = (centre + np.array(offsets) * 8) / 0.5
    np.testing.assert_allclose(landmarks, [expected])
    np.testing.assert_allclose(scores, [0.9], rtol=1e-6)


def test_decode_scrfd_second_anchor_and_coarser_stride():
    out = blank_outputs()
    put(out, 32, 5, 7, 0.8, dist=(2, 1, 1, 2), anchor=1)
    boxes, _, _ = decode_scrfd(out, 1.0, FACE_DET_SIZE)
    np.testing.assert_allclose(boxes, [[160 - 64, 224 - 32, 160 + 32, 224 + 64]])


def test_decode_scrfd_nms_drops_overlap():
    out = blank_outputs()
    put(out, 8, 10, 10, 0.7, dist=(4, 4, 4, 4))
    put(out, 8, 11, 10, 0.9, dist=(4, 4, 4, 4))  # 8 px over: IoU 0.78
    boxes, _, scores = decode_scrfd(out, 1.0, FACE_DET_SIZE)
    np.testing.assert_allclose(scores, [0.9], rtol=1e-6)
    np.testing.assert_allclose(boxes, [[56, 48, 120, 112]])


def test_decode_scrfd_nms_keeps_far_apart():
    out = blank_outputs()
    put(out, 8, 10, 10, 0.7, dist=(4, 4, 4, 4))
    put(out, 8, 60, 60, 0.9, dist=(4, 4, 4, 4))
    _, _, scores = decode_scrfd(out, 1.0, FACE_DET_SIZE)
    np.testing.assert_allclose(scores, [0.9, 0.7], rtol=1e-6)


def test_decode_scrfd_threshold():
    out = blank_outputs()
    put(out, 16, 4, 4, 0.49)
    boxes, landmarks, scores = decode_scrfd(out, 1.0, FACE_DET_SIZE)
    assert boxes.shape == (0, 4)
    assert landmarks.shape == (0, 5, 2)
    assert scores.shape == (0,)


def _dot_centres(img: Image.Image, points: np.ndarray, window: int = 6) -> np.ndarray:
    """The brightness-weighted centre of the dot near each point."""
    grey = np.asarray(img.convert("L"), np.float64)
    found = []
    for px, py in points:
        x0, y0 = int(px) - window, int(py) - window
        patch = grey[y0:y0 + 2 * window + 1, x0:x0 + 2 * window + 1]
        ys, xs = np.mgrid[:patch.shape[0], :patch.shape[1]]
        total = patch.sum()
        found.append([x0 + (xs * patch).sum() / total, y0 + (ys * patch).sum() / total])
    return np.array(found)


def test_align_maps_template_to_itself():
    img = Image.new("RGB", (224, 224), "black")
    draw = ImageDraw.Draw(img)
    points = TEMPLATE * 2
    for x, y in points:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="white")
    crop = align(img, points)
    assert crop.size == (112, 112)
    np.testing.assert_allclose(_dot_centres(crop, TEMPLATE), TEMPLATE, atol=1.0)


def test_similarity_unit_vectors():
    a = np.array([1.0, 0.0, 0.0], np.float32)
    b = np.array([0.6, 0.8, 0.0], np.float32)
    assert similarity(a, a) == pytest.approx(1.0)
    assert similarity(a, b) == pytest.approx(0.6)
    assert similarity(a, -a) == pytest.approx(-1.0)
    assert isinstance(similarity(a, b), float)


def test_face_crop_clamped(tmp_path):
    path = grey_jpeg(tmp_path / "a.jpg", (400, 300))
    crop = face_crop(path, (0, 0, 60, 80))  # touches the top-left corner
    assert crop.size == (160, 160)
    assert crop.mode == "RGB"
    big = face_crop(path, (-50, -50, 450, 350), size=96)  # wider than the photo
    assert big.size == (96, 96)


def test_face_crop_uses_decoded_coordinates(tmp_path):
    """Boxes from analyse are in the decoded photo's pixels; face_crop agrees."""
    path = tmp_path / "a.jpg"
    img = Image.new("RGB", (3200, 2400), "black")
    ImageDraw.Draw(img).rectangle((1600, 1200, 1799, 1399), fill="white")
    img.save(path, "JPEG")
    assert faces._load(path).size == (1600, 1200)
    crop = np.asarray(face_crop(path, (800, 600, 900, 700), size=100).convert("L"))
    assert crop[50, 50] > 200  # the white square is in the middle
    assert crop[2, 2] < 50  # the margin around it is black


def test_load_applies_orientation(tmp_path):
    path = grey_jpeg(tmp_path / "turned.jpg", (600, 800), orientation=6)
    img = faces._load(path)
    assert img.size == (800, 600)
    assert img.mode == "RGB"


def test_load_unreadable_raises(tmp_path):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not a photo")
    with pytest.raises(UnreadableImage):
        faces._load(path)


class FakeDetector:
    """Stands in for the SCRFD session: records the input, returns set outputs."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.inputs = []

    def get_inputs(self):
        class Input:
            name = "input.1"
        return [Input()]

    def run(self, names, feed):
        self.inputs.append(feed["input.1"])
        return self.outputs


def test_detect_letterboxes_and_scales_back():
    out = blank_outputs()
    put(out, 8, 20, 10, 0.9, dist=(4, 4, 4, 4))  # canvas box (128, 48, 192, 112)
    engine = FaceEngine(use_gpu=False)
    engine._det = FakeDetector(out)
    img = Image.new("RGB", (1280, 960), (128, 128, 128))
    found = engine.detect(img)
    (box, landmarks, score), = found
    np.testing.assert_allclose(box, (256, 96, 384, 224))
    assert landmarks.shape == (5, 2)
    assert score == pytest.approx(0.9)
    x = engine._det.inputs[0]
    assert x.shape == (1, 3, FACE_DET_SIZE, FACE_DET_SIZE)
    assert x.dtype == np.float32
    # The photo fills the top 480 rows; the rest is black padding.
    assert x[0, :, 0, 0] == pytest.approx([(128 - 127.5) / 128] * 3)
    assert x[0, :, 600, 0] == pytest.approx([-127.5 / 128] * 3)


def test_detect_ignores_small_faces():
    out = blank_outputs()
    half = FACE_MIN_PX / 2 / 8  # in stride-8 units
    put(out, 8, 10, 10, 0.9, dist=(4, half - 0.1, 4, half - 0.1))  # too short
    put(out, 8, 40, 40, 0.8, dist=(half, half, half, half))  # exactly FACE_MIN_PX
    put(out, 8, 70, 70, 0.7, dist=(half - 0.1, 4, half - 0.1, 4))  # too narrow
    engine = FaceEngine(use_gpu=False)
    engine._det = FakeDetector(out)
    found = engine.detect(Image.new("RGB", (640, 640)))
    assert [s for _, _, s in found] == [pytest.approx(0.8)]
    np.testing.assert_allclose(found[0][0], (320 - 12, 320 - 12, 320 + 12, 320 + 12))


# --- real models --------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    return FaceEngine(use_gpu=False)


@needs_models
def test_engine_blank_image_has_no_faces(tmp_path, engine):
    path = grey_jpeg(tmp_path / "grey.jpg", (800, 600))
    assert engine.analyse(path) == []


@needs_models
def test_engine_embedding_is_unit_and_stable(engine):
    rng = np.random.default_rng(1)
    crop = Image.fromarray(rng.integers(0, 256, (112, 112, 3), dtype=np.uint8))
    first = engine.embed(crop, TEMPLATE)
    second = engine.embed(crop, TEMPLATE)
    assert first.shape == (512,)
    assert first.dtype == np.float32
    assert np.linalg.norm(first) == pytest.approx(1.0, abs=1e-5)
    np.testing.assert_array_equal(first, second)


@needs_models
def test_engine_unreadable_raises(tmp_path, engine):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not a photo")
    with pytest.raises(UnreadableImage):
        engine.analyse(path)


@needs_models
def test_engine_orientation_applied(tmp_path, engine):
    path = grey_jpeg(tmp_path / "turned.jpg", (600, 800), orientation=6)
    assert faces._load(path).size == (800, 600)
    assert engine.analyse(path) == []


class CountingSession:
    def __init__(self, session):
        self.session = session
        self.batches = []

    def get_inputs(self):
        return self.session.get_inputs()

    def run(self, names, feed):
        self.batches.append(next(iter(feed.values())).shape)
        return self.session.run(names, feed)


@needs_models
def test_engine_analyse_embeds_all_faces_in_one_batch(tmp_path, engine, monkeypatch):
    path = grey_jpeg(tmp_path / "turned.jpg", (600, 800), orientation=6)
    found = [((10.0, 10.0, 122.0, 122.0), TEMPLATE + 10, 0.9),
             ((300.0, 200.0, 412.0, 312.0), TEMPLATE + [300, 200], 0.7)]
    monkeypatch.setattr(engine, "detect", lambda img: found)
    engine._recognition()  # open the real session before wrapping it
    counting = CountingSession(engine._rec)
    monkeypatch.setattr(engine, "_rec", counting)
    result = engine.analyse(path)
    assert counting.batches == [(2, 3, 112, 112)]
    assert [f.score for f in result] == [0.9, 0.7]
    assert [f.box for f in result] == [found[0][0], found[1][0]]
    for face in result:
        assert face.image_size == (800, 600)
        assert face.landmarks.shape == (5, 2)
        assert face.embedding.shape == (512,)
        assert face.embedding.dtype == np.float32
        assert np.linalg.norm(face.embedding) == pytest.approx(1.0, abs=1e-5)
    # The same grey everywhere: the two crops embed alike.
    assert similarity(result[0].embedding, result[1].embedding) == pytest.approx(1.0, abs=1e-4)
