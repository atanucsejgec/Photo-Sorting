import numpy as np
import pytest
from PIL import Image

from photosort.imageinfo import (
    UnreadableImage,
    analyse,
    image_size,
    open_image,
    perceptual_hash,
    sharpness,
    thumbnail,
)
from photosort.thresholds import DUP_HASH_MAX, REPEAT_HASH_MAX, SHARPNESS_WIDTH, THUMB_PX


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def test_image_size_respects_orientation(tmp_path, make_pattern_jpeg):
    upright = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    turned = make_pattern_jpeg(tmp_path / "b.jpg", 1, orientation=6)
    assert image_size(upright) == (800, 600)
    assert image_size(turned) == (600, 800)


def test_open_image_applies_orientation_and_bounds(tmp_path, make_pattern_jpeg):
    turned = make_pattern_jpeg(tmp_path / "b.jpg", 1, orientation=6)
    full = open_image(turned)
    assert full.size == (600, 800)
    assert full.mode == "RGB"
    small = open_image(turned, 100)
    assert max(small.size) <= 100
    assert small.width < small.height


def test_resized_copy_hashes_close(tmp_path, make_pattern_jpeg):
    original = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    copy = make_pattern_jpeg(tmp_path / "b.jpg", 1, size=(400, 300), quality=60)
    assert distance(perceptual_hash(original), perceptual_hash(copy)) <= DUP_HASH_MAX


def test_hash_follows_orientation(tmp_path, make_pattern_jpeg):
    # Same pixels, but one is tagged as rotated: it must look different.
    original = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    turned = make_pattern_jpeg(tmp_path / "b.jpg", 1, orientation=3)
    assert distance(perceptual_hash(original), perceptual_hash(turned)) > DUP_HASH_MAX


def test_different_images_hash_far(tmp_path, make_pattern_jpeg):
    one = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    two = make_pattern_jpeg(tmp_path / "b.jpg", 2)
    h1 = perceptual_hash(one)
    assert 0 <= h1 < 2**64
    assert distance(h1, perceptual_hash(two)) > REPEAT_HASH_MAX


def test_sharpness_orders_blur(tmp_path, make_pattern_jpeg):
    sharp = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    blurry = make_pattern_jpeg(tmp_path / "b.jpg", 1, blur=3)
    assert sharpness(sharp) > sharpness(blurry)


def test_unreadable_raises(tmp_path):
    empty = tmp_path / "x.jpg"
    empty.write_bytes(b"")
    text = tmp_path / "y.jpg"
    text.write_text("this is not a photo")
    for path in (empty, text):
        for func in (open_image, image_size, perceptual_hash, sharpness, thumbnail):
            with pytest.raises(UnreadableImage):
                func(path)


def test_thumbnail_bounded(tmp_path, make_pattern_jpeg):
    jpeg = make_pattern_jpeg(tmp_path / "a.jpg", 1, size=(1600, 1200))
    png = tmp_path / "b.png"
    Image.new("RGBA", (1000, 500), (10, 20, 30, 128)).save(png)
    for path in (jpeg, png):
        thumb = thumbnail(path)
        assert max(thumb.size) <= THUMB_PX
        assert thumb.mode == "RGB"


def test_sharpness_measured_at_full_width(tmp_path, make_pattern_jpeg):
    # Portrait photos are measured at SHARPNESS_WIDTH wide, never shrunk below it.
    path = make_pattern_jpeg(tmp_path / "a.jpg", 1, size=(800, 600), orientation=6)
    upright = Image.open(path).convert("L").transpose(Image.Transpose.ROTATE_270)
    height = round(upright.height * SHARPNESS_WIDTH / upright.width)
    grey = np.asarray(upright.resize((SHARPNESS_WIDTH, height)), dtype=np.float64)
    lap = (grey[:-2, 1:-1] + grey[2:, 1:-1] + grey[1:-1, :-2] + grey[1:-1, 2:]
           - 4 * grey[1:-1, 1:-1])
    assert sharpness(path) == pytest.approx(lap.var(), rel=0.05)


def test_sharpness_graded_by_blur(tmp_path, make_pattern_jpeg):
    values = [sharpness(make_pattern_jpeg(tmp_path / f"{b}.jpg", 5, blur=b)) for b in (0, 0.5, 2)]
    assert values == sorted(values, reverse=True)


def test_analyse_matches_separate_measures(tmp_path, make_pattern_jpeg):
    for name, kwargs in [("a.jpg", {}), ("b.jpg", {"orientation": 6, "size": (1600, 1200)}),
                         ("c.jpg", {"size": (300, 200), "blur": 1})]:
        path = make_pattern_jpeg(tmp_path / name, 9, **kwargs)
        assert analyse(path) == (perceptual_hash(path), sharpness(path))


def test_analyse_unreadable_raises(tmp_path):
    empty = tmp_path / "x.jpg"
    empty.write_bytes(b"")
    with pytest.raises(UnreadableImage):
        analyse(empty)
