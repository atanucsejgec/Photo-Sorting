import math
import struct

import numpy as np
import pytest
from PIL import Image

from photosort import imageinfo, quality
from photosort.cache import AnalysisCache
from photosort.imageinfo import sharpness
from photosort.quality import QUALITY_KIND, Quality, bad_reason, measure_quality

NORMAL = dict(sharpness=200.0, mean=120.0, p95=200.0, std=40.0, bright_frac=0.01)


def _q(**changes) -> Quality:
    return Quality(**{**NORMAL, **changes})


@pytest.mark.parametrize("q, reason", [
    (Quality(0.0, 0.0, 0.0, 0.0, 0.0), "accidental shot"),  # all black
    (Quality(0.0, 255.0, 255.0, 0.0, 1.0), "accidental shot"),  # all white: flat first
    (_q(std=11.9), "accidental shot"),
    (_q(mean=20.0, p95=80.0, std=15.0), "too dark"),
    (_q(mean=20.0, p95=80.0, std=15.0, sharpness=1.0), "too dark"),  # dark before blurry
    (_q(mean=20.0, p95=120.0), None),  # dark, but with bright parts: a night shot
    (_q(mean=200.0, bright_frac=0.6), "too bright"),
    (_q(mean=240.0, p95=255.0, bright_frac=0.3), "too bright"),
    (_q(mean=200.0, bright_frac=0.6, sharpness=1.0), "too bright"),  # bright before blurry
    (_q(sharpness=10.0), "blurry"),
    (_q(sharpness=25.0), None),
    (_q(), None),
])
def test_bad_reason(q, reason):
    assert bad_reason(q) == reason


def _save(img: Image.Image, path, fmt=None):
    img.save(path, fmt)
    return path


def _finite(q: Quality) -> bool:
    return all(math.isfinite(v) for v in (q.sharpness, q.mean, q.p95, q.std, q.bright_frac))


def test_uniform_grey(tmp_path):
    q = measure_quality(_save(Image.new("RGB", (64, 64), (128, 128, 128)), tmp_path / "g.png"))
    assert q == Quality(0.0, 128.0, 128.0, 0.0, 0.0)
    assert _finite(q)


def test_one_pixel_image(tmp_path):
    q = measure_quality(_save(Image.new("RGB", (1, 1), "white"), tmp_path / "dot.png"))
    assert q is not None and _finite(q)
    assert q.bright_frac == 1.0


def test_panorama(tmp_path, make_pattern_jpeg):
    q = measure_quality(make_pattern_jpeg(tmp_path / "pano.jpg", 1, size=(10000, 500)))
    assert q is not None and _finite(q)
    assert q.std > 0


def test_tall_narrow_strip(tmp_path):
    q = measure_quality(_save(Image.new("RGB", (1, 10000), "grey"), tmp_path / "strip.png"))
    assert q is not None and _finite(q)


def _full_range_blocks(top: float) -> np.ndarray:
    """A 480x640 grid of random blocks with values spread over 0..top (a sharp,
    normally lit photo once scaled to 0-255)."""
    fine = np.random.default_rng(1).random((48, 64)) * top
    return np.kron(fine, np.ones((10, 10)))


def test_16_bit_png_is_measured_on_its_full_range(tmp_path):
    path = _save(Image.fromarray(_full_range_blocks(65535).astype(np.uint16)), tmp_path / "a.png")
    q = measure_quality(path)
    assert q.mean == pytest.approx(128, abs=8)
    assert bad_reason(q) is None


def test_float_tiff_is_measured_on_its_full_range(tmp_path):
    path = _save(Image.fromarray(_full_range_blocks(1.0).astype(np.float32)), tmp_path / "a.tif")
    q = measure_quality(path)
    assert q.mean == pytest.approx(128, abs=8)
    assert bad_reason(q) is None


def test_stats_of_a_half_black_half_white_photo(tmp_path):
    pixels = np.zeros((100, 200), dtype=np.uint8)
    pixels[:, 100:] = 255
    q = measure_quality(_save(Image.fromarray(pixels).convert("RGB"), tmp_path / "half.png"))
    assert q.mean == pytest.approx(127.5, abs=1)
    assert q.p95 == 255.0
    assert q.std == pytest.approx(127.5, abs=1)
    assert q.bright_frac == pytest.approx(0.5, abs=0.01)


def test_sharpness_is_the_imageinfo_measure(tmp_path, make_pattern_jpeg):
    path = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    assert measure_quality(path).sharpness == sharpness(path)


def test_rgba_and_palette_pngs(tmp_path):
    rgba = Image.new("RGBA", (40, 30), (200, 10, 10, 128))
    palette = Image.new("RGB", (40, 30), (10, 200, 10)).convert("P", palette=Image.ADAPTIVE)
    for img, name in ((rgba, "rgba.png"), (palette, "palette.png")):
        q = measure_quality(_save(img, tmp_path / name))
        assert q is not None and _finite(q)
        assert 0 < q.mean < 255


def test_unreadable_is_none_and_not_cached(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not a photo")
    with AnalysisCache(tmp_path) as cache:
        assert measure_quality(bad, cache) is None
        assert cache.get(QUALITY_KIND, bad) is None


def test_cached_quality_is_read_back(tmp_path, make_pattern_jpeg, monkeypatch):
    path = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    with AnalysisCache(tmp_path) as cache:
        first = measure_quality(path, cache)
        assert len(cache.get(QUALITY_KIND, path)) == struct.calcsize("<5d")

    def no_decoding(*args, **kwargs):
        raise AssertionError("decoded again")

    monkeypatch.setattr(imageinfo, "decode", no_decoding)
    monkeypatch.setattr(quality, "decode", no_decoding)
    with AnalysisCache(tmp_path) as cache:
        assert measure_quality(path, cache) == first
