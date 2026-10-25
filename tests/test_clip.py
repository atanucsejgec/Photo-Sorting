import zlib

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from photosort import clip
from photosort.ai import models
from photosort.clip import ClipEngine, load_for_clip, pack, preprocess, unpack
from photosort.imageinfo import UnreadableImage

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

needs_tokenizer = pytest.mark.skipif(bool(models.missing(["clip_tokenizer"])),
                                     reason="CLIP tokenizer not downloaded")
needs_clip = pytest.mark.skipif(bool(models.missing(models.CLIP_MODELS)),
                                reason="CLIP model not downloaded")


def save(img: Image.Image, path, **kwargs):
    img.save(path, **kwargs)
    return path


# --- preprocess ---------------------------------------------------------------

def test_preprocess_wide_image_is_cropped_to_224():
    x = preprocess(Image.new("RGB", (448, 224), (10, 20, 30)))
    assert x.shape == (3, 224, 224)
    assert x.dtype == np.float32


def test_preprocess_centre_crops():
    img = Image.new("RGB", (672, 224), (255, 0, 0))
    img.paste((0, 0, 255), (224, 0, 448, 224))  # the middle third is blue
    x = preprocess(img)
    blue = (np.array([0, 0, 1], np.float32) - MEAN) / STD
    np.testing.assert_allclose(x[:, 112, 112], blue, rtol=1e-5)
    np.testing.assert_allclose(x[:, 112, 2], blue, rtol=1e-5)


def test_preprocess_small_image_is_upscaled():
    img = Image.new("RGB", (100, 50), (0, 0, 0))
    img.paste((255, 255, 255), (0, 0, 100, 25))  # top half white
    x = preprocess(img)
    assert x.shape == (3, 224, 224)
    white = (1 - MEAN) / STD
    black = -MEAN / STD
    np.testing.assert_allclose(x[:, 10, 112], white, rtol=1e-5)
    np.testing.assert_allclose(x[:, 214, 112], black, rtol=1e-5)


def _spy(monkeypatch, method):
    """Record the arguments of each call to PIL Image.<method>, still running it."""
    calls = []
    real = getattr(Image.Image, method)

    def spy(self, *args, **kwargs):
        calls.append((self.mode, self.size, args))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, method, spy)
    return calls


@pytest.mark.parametrize("size, resized", [
    ((300, 225), (298, 224)),  # 298.67 is cut, not rounded, as OpenCLIP does
    ((225, 300), (224, 298)),
    ((448, 224), (448, 224)),
])
def test_preprocess_resizes_the_long_side_cut_short(monkeypatch, size, resized):
    calls = _spy(monkeypatch, "resize")
    preprocess(Image.new("RGB", size, (10, 20, 30)))
    assert [args[0] for _, _, args in calls] == [resized]


def test_preprocess_converts_only_what_is_not_rgb(monkeypatch):
    calls = _spy(monkeypatch, "convert")
    preprocess(Image.new("RGB", (300, 260), (128, 128, 128)))
    assert calls == []
    x = preprocess(Image.new("L", (300, 260), 128))
    assert [(mode, args) for mode, _, args in calls] == [("L", ("RGB",))]
    np.testing.assert_allclose(x[:, 100, 100], (128 / 255 - MEAN) / STD, rtol=1e-6)


def test_preprocess_normalises_with_clip_mean_and_std():
    x = preprocess(Image.new("RGB", (300, 260), (128, 128, 128)))
    expected = (128 / 255 - MEAN) / STD
    for channel in range(3):
        np.testing.assert_allclose(x[channel], expected[channel], rtol=1e-6)


# --- load_for_clip ------------------------------------------------------------

def test_load_for_clip_applies_exif_orientation(tmp_path):
    img = Image.new("RGB", (600, 300), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, 300, 300))  # left half red
    exif = Image.Exif()
    exif[0x0112] = 6  # shown turned a quarter clockwise
    path = save(img, tmp_path / "turned.jpg", format="JPEG", exif=exif)
    loaded = load_for_clip(path)
    assert loaded.mode == "RGB"
    assert loaded.size == (300, 600)
    r, g, b = loaded.getpixel((150, 100))  # the left half is now on top
    assert r > 200 and b < 60


def test_load_for_clip_uses_jpeg_draft(tmp_path):
    path = save(Image.new("RGB", (4000, 3000), (90, 90, 90)), tmp_path / "big.jpg",
                format="JPEG")
    loaded = load_for_clip(path)
    assert 224 <= min(loaded.size) < 3000


def test_load_for_clip_draft_keeps_224_just_above_a_step(tmp_path):
    # 1799 is just above 8 x 224: the eighth-size decode is 225 x 225, not smaller.
    path = save(Image.new("RGB", (1800, 1799), (90, 90, 90)), tmp_path / "step.jpg",
                format="JPEG")
    loaded = load_for_clip(path)
    assert 224 <= min(loaded.size) < 1799


@pytest.mark.parametrize("name, img, kwargs", [
    ("pano.jpg", Image.new("RGB", (10000, 500), (40, 80, 120)), {"format": "JPEG"}),
    ("alpha.png", Image.new("RGBA", (300, 300), (40, 80, 120, 100)), {"format": "PNG"}),
    ("dot.png", Image.new("RGB", (1, 1), (40, 80, 120)), {"format": "PNG"}),
])
def test_load_for_clip_odd_photos_work(tmp_path, name, img, kwargs):
    loaded = load_for_clip(save(img, tmp_path / name, **kwargs))
    assert loaded.mode == "RGB"
    assert min(loaded.size) >= min(224, min(img.size))
    assert preprocess(loaded).shape == (3, 224, 224)


def test_load_for_clip_unreadable_raises(tmp_path):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not a photo")
    with pytest.raises(UnreadableImage):
        load_for_clip(path)


# --- tokenize -----------------------------------------------------------------

@pytest.fixture(scope="module")
def tokenizer():
    return clip.load_tokenizer(models.model_path("clip_tokenizer"))


@needs_tokenizer
def test_tokenize_pads_to_77(tokenizer):
    ids = clip.tokenize(tokenizer, "a photo of food")
    assert ids.shape == (1, 77)
    assert ids.dtype == np.int32
    assert ids[0].tolist() == [49406, 320, 1125, 539, 1559, 49407] + [0] * 71


@needs_tokenizer
def test_tokenize_cuts_long_text_keeping_the_end_token(tokenizer):
    ids = clip.tokenize(tokenizer, " ".join(["word"] * 200))
    assert ids.shape == (1, 77)
    assert ids[0, 0] == 49406
    assert ids[0, -1] == 49407
    assert 0 not in ids[0].tolist()


# --- pack / unpack ------------------------------------------------------------

def test_pack_round_trips():
    e = np.random.default_rng(3).standard_normal(512).astype(np.float32)
    data = pack(e)
    assert len(data) == 512 * 4
    back = unpack(data)
    assert back.dtype == np.float32
    assert back.shape == (512,)
    np.testing.assert_array_equal(back, e)
    back[0] = 99  # a writable copy, not a view of the bytes


def test_unpack_wrong_length_raises():
    with pytest.raises(ValueError):
        unpack(b"\0" * (511 * 4))
    with pytest.raises(ValueError):
        unpack(b"")


def test_clip_kind():
    assert clip.CLIP_KIND == "clip-v1"


# --- the engine with fake sessions --------------------------------------------

class FakeSession:
    """Stands in for a CLIP session: batch of 1, a fixed input name and dtype."""

    def __init__(self, input_name, shape, dtype):
        self.input_name, self.shape, self.dtype = input_name, shape, dtype
        self.inputs = []

    def get_inputs(self):
        class Input:
            name = self.input_name
        return [Input()]

    def run(self, names, feed):
        x = feed[self.input_name]
        assert x.shape == self.shape and x.dtype == self.dtype
        self.inputs.append(x)
        return [3 * np.random.default_rng(zlib.crc32(x.tobytes())).standard_normal((1, 512))]


class FakeTokenizer:
    def encode(self, text):
        class Encoding:
            ids = [49406, *(len(word) for word in text.split()), 49407]
        return Encoding()


@pytest.fixture
def fake_clip(monkeypatch):
    sessions = {
        "clip_visual": FakeSession("image", (1, 3, 224, 224), np.float32),
        "clip_textual": FakeSession("text", (1, 77), np.int32),
    }
    opened = []

    def make_session(path, use_gpu):
        for key, session in sessions.items():
            if path == models.model_path(key):
                opened.append(key)
                return session
        raise AssertionError(f"unexpected model {path}")

    monkeypatch.setattr(clip.runtime, "make_session", make_session)
    monkeypatch.setattr(clip, "load_tokenizer", lambda path: FakeTokenizer())
    return sessions, opened


def test_engine_opens_nothing_until_used(fake_clip):
    _, opened = fake_clip
    ClipEngine(use_gpu=False)
    assert opened == []


def test_image_embedding_is_unit_length(tmp_path, fake_clip):
    sessions, opened = fake_clip
    engine = ClipEngine(use_gpu=False)
    path = save(Image.new("RGB", (640, 480), (200, 100, 50)), tmp_path / "p.jpg",
                format="JPEG")
    e = engine.image_embedding(path)
    assert e.shape == (512,)
    assert e.dtype == np.float32
    assert np.linalg.norm(e) == pytest.approx(1.0, abs=1e-5)
    engine.image_embedding(path)
    assert opened == ["clip_visual"]  # opened once, on first use
    assert len(sessions["clip_visual"].inputs) == 2


def test_image_embedding_unreadable_raises(tmp_path, fake_clip):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not a photo")
    with pytest.raises(UnreadableImage):
        ClipEngine(use_gpu=False).image_embedding(path)


def test_text_embeddings_run_once_per_distinct_text(fake_clip):
    sessions, opened = fake_clip
    engine = ClipEngine(use_gpu=False)
    out = engine.text_embeddings(["a photo of food", "a photo of a cat", "a photo of food"])
    assert out.shape == (3, 512)
    assert out.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
    np.testing.assert_array_equal(out[0], out[2])
    assert not np.allclose(out[0], out[1])
    again = engine.text_embeddings(["a photo of a cat"])
    np.testing.assert_array_equal(again[0], out[1])
    assert len(sessions["clip_textual"].inputs) == 2
    assert opened == ["clip_textual"]
    out[1] = 0  # the caller's rows are copies: the cache is untouched
    np.testing.assert_array_equal(engine.text_embeddings(["a photo of a cat"])[0], again[0])


def test_text_embeddings_of_nothing(fake_clip):
    out = ClipEngine(use_gpu=False).text_embeddings([])
    assert out.shape == (0, 512)
    assert out.dtype == np.float32


# --- the real model -----------------------------------------------------------

@needs_clip
def test_real_model_reads_a_printed_page(tmp_path):
    page = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=40)
    lines = ["INVOICE No. 2026-104", "Date: 25 October 2026", "",
             "Item            Qty    Price", "Paper, A4        10    45.00",
             "Ink cartridge     2    60.00", "Stapler           1    12.50", "",
             "Total                 117.50", "", "Thank you for your business."]
    for n, line in enumerate(lines):
        draw.text((60, 60 + n * 70), line, fill="black", font=font)
    path = save(page, tmp_path / "page.png", format="PNG")

    engine = ClipEngine(use_gpu=False)
    image = engine.image_embedding(path)
    document, food = engine.text_embeddings(["a photo of a printed document",
                                             "a photo of food"])
    assert np.linalg.norm(image) == pytest.approx(1.0, abs=1e-5)
    assert float(image @ document) > float(image @ food)
