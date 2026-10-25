import threading
from datetime import datetime

import numpy as np
import pytest
from PIL import Image, ImageDraw

from photosort.groups import Candidate
from photosort.modes import MODE_SPECS
from photosort.modes.bad_photos import find_bad_photos, plan_bad
from photosort.plan import Move


def _black(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), "black").save(path, "JPEG")
    return path


def _bad(rel: str, note: str = "blurry") -> Candidate:
    return Candidate(rel, "photo", 10, 10, 1, None, 1.0, note)


def test_finds_the_black_and_the_blurred_photos(tmp_path, make_pattern_jpeg, make_mp4):
    _black(tmp_path / "pocket.jpg")
    make_pattern_jpeg(tmp_path / "blurred.jpg", 1, blur=4, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "sharp.jpg", 2)
    make_mp4(tmp_path / "clip.mp4", None)  # black, but a video
    progress = []
    bad = find_bad_photos(tmp_path, False, progress=lambda i, n: progress.append((i, n)))
    assert [(c.rel, c.note) for c in bad] == [
        ("blurred.jpg", "blurry"), ("pocket.jpg", "accidental shot")]
    blurred, pocket = bad
    assert blurred.kind == "photo"
    assert (blurred.width, blurred.height) == (800, 600)
    assert blurred.size == (tmp_path / "blurred.jpg").stat().st_size
    assert blurred.taken == datetime(2025, 2, 20, 18, 13, 25)
    assert 0 < blurred.sharpness < 25
    assert pocket.sharpness == 0.0
    assert progress == [(1, 3), (2, 3), (3, 3)]  # photos only


def test_skips_unreadable_photos(tmp_path):
    (tmp_path / "broken.jpg").write_bytes(b"not a photo")
    assert find_bad_photos(tmp_path, False) == []


def test_bad_photos_folder_is_never_listed(tmp_path):
    _black(tmp_path / "Bad Photos" / "old.jpg")
    _black(tmp_path / "sub" / "bad photos" / "older.jpg")
    _black(tmp_path / "sub" / "new.jpg")
    assert [c.rel for c in find_bad_photos(tmp_path, True)] == ["sub/new.jpg"]
    assert find_bad_photos(tmp_path, False) == []


def test_cancel(tmp_path):
    _black(tmp_path / "a.jpg")
    cancel = threading.Event()
    cancel.set()
    assert find_bad_photos(tmp_path, False, cancel=cancel) is None


def test_quality_is_cached(tmp_path):
    from photosort.cache import AnalysisCache
    from photosort.quality import QUALITY_KIND

    _black(tmp_path / "a.jpg")
    find_bad_photos(tmp_path, False)
    with AnalysisCache(tmp_path) as cache:
        assert cache.get(QUALITY_KIND, tmp_path / "a.jpg") is not None


def test_plan_moves_the_ticked_photos(tmp_path):
    bad = [_bad("a.jpg"), _bad("sub/b.jpg", "too dark"), _bad("c.jpg")]
    plan = plan_bad(tmp_path, bad, {"a.jpg", "sub/b.jpg"})
    assert plan.mode == "bad"
    assert plan.moves == [Move("a.jpg", "Bad Photos/a.jpg"), Move("sub/b.jpg", "Bad Photos/b.jpg")]
    assert plan.summary == ["2 photos → Bad Photos", "1 photos stay where they are"]


def test_plan_with_every_photo_ticked_or_none(tmp_path):
    bad = [_bad("a.jpg"), _bad("b.jpg")]
    assert plan_bad(tmp_path, bad, {"a.jpg", "b.jpg"}).summary == ["2 photos → Bad Photos"]
    nothing = plan_bad(tmp_path, bad, set())
    assert nothing.moves == []
    assert nothing.summary == ["0 photos → Bad Photos", "2 photos stay where they are"]


def test_plan_avoids_name_clashes(tmp_path):
    (tmp_path / "Bad Photos").mkdir()
    (tmp_path / "Bad Photos" / "a.jpg").write_bytes(b"")
    bad = [_bad("a.jpg"), _bad("sub/a.jpg")]
    assert plan_bad(tmp_path, bad, {"a.jpg", "sub/a.jpg"}).moves == [
        Move("a.jpg", "Bad Photos/a (1).jpg"), Move("sub/a.jpg", "Bad Photos/a (2).jpg")]


def test_plan_reuses_the_folder_spelt_another_way(tmp_path):
    (tmp_path / "bad photos").mkdir()
    plan = plan_bad(tmp_path, [_bad("a.jpg")], {"a.jpg"})
    assert plan.moves == [Move("a.jpg", "bad photos/a.jpg")]
    assert plan.summary == ["1 photos → bad photos"]


def test_mode_spec():
    spec = MODE_SPECS["bad"]
    assert spec.kind == "bad"
    assert spec.set_aside_folder == "Bad Photos"
    assert spec.none_found == "No bad photos found."


# Screenshots and graphics: sharp, but mostly white or nearly black, so the
# light rules alone would call them "too bright" or "too dark".

def _chat_screen(dark: bool) -> Image.Image:
    """A 1080x2400 phone chat screen: bubbles and lines of text."""
    back, bubble, text = ((12, 12, 12), (32, 32, 32), (90, 90, 90)) if dark else (
        (255, 255, 255), (236, 236, 236), (20, 20, 20))
    img = Image.new("RGB", (1080, 2400), back)
    draw = ImageDraw.Draw(img)
    for i, top in enumerate(range(200, 2200, 260)):
        left = 40 if i % 2 else 380
        draw.rounded_rectangle((left, top, left + 660, top + 200), 30, fill=bubble)
        for line in range(3):
            y = top + 40 + line * 50
            draw.rectangle((left + 30, y, left + 600 - line * 120, y + 12), fill=text)
    return img


def _sticker(transparent: bool) -> Image.Image:
    """A 512x512 cartoon face on a white background (see-through or not)."""
    img = Image.new("RGBA", (512, 512), (255, 255, 255, 0 if transparent else 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((156, 156, 356, 356), fill=(250, 200, 40, 255), outline="black", width=8)
    draw.ellipse((210, 220, 230, 240), fill="black")
    draw.ellipse((282, 220, 302, 240), fill="black")
    draw.arc((206, 240, 306, 320), 20, 160, fill="black", width=8)
    return img


def _graphics(root):
    """Screenshots named the way phones and computers name them, and the
    same pictures as plain graphics with no EXIF at all."""
    light, dark, sticker = _chat_screen(False), _chat_screen(True), _sticker(False)
    light.save(root / "Screenshot_20250315-101500.png")
    dark.save(root / "Screenshot 2025-03-15 at 22.10.00.png")
    light.save(root / "Screenshot_20250316-090000.jpg", quality=90)
    dark.save(root / "screen-shot dark.jpg", quality=90)
    light.save(root / "chat.png")
    dark.save(root / "dark mode.png")
    dark.save(root / "dark mode.webp", lossless=True)
    sticker.save(root / "sticker.png")
    _sticker(True).save(root / "see-through sticker.png")
    sticker.save(root / "sticker.webp", lossless=True)
    sticker.convert("RGB").save(root / "sticker.gif")  # not a photo at all


def test_graphics_would_be_bad_by_their_light_alone(tmp_path):
    from photosort.quality import bad_reason, measure_quality

    _graphics(tmp_path)
    reasons = {p.name: bad_reason(measure_quality(p)) for p in tmp_path.iterdir()
               if p.suffix != ".gif"}
    assert reasons["chat.png"] == "too bright"
    assert reasons["dark mode.png"] == "too dark"
    assert reasons["sticker.png"] == "too bright"
    assert all(reasons.values()), reasons


def test_screenshots_and_graphics_are_left_alone(tmp_path):
    _graphics(tmp_path)
    progress = []
    assert find_bad_photos(tmp_path, False, lambda i, n: progress.append((i, n))) == []
    assert progress[-1] == (10, 10)  # every photo looked at; the GIF isn't one


def _dark_pattern(path, fmt, make=None, model=None):
    rng = np.random.default_rng(7)
    grey = (rng.integers(0, 256, (48, 64)) * 0.25).astype(np.uint8)
    img = Image.fromarray(np.dstack([grey] * 3)).resize((800, 600), Image.Resampling.NEAREST)
    exif = Image.Exif()
    if make is not None:
        exif[271] = make
    if model is not None:
        exif[272] = model
    img.save(path, fmt, exif=exif)
    return path


@pytest.mark.parametrize("name, fmt, make, model", [
    ("IMG_20250315_101500.jpg", "JPEG", "Google", "Pixel 7"),
    ("no camera info.jpg", "JPEG", None, None),  # a JPEG is a photo either way
    ("from a camera.png", "PNG", None, "Canon EOS R6"),
    ("from a camera.webp", "WEBP", "samsung", None),
])
def test_dark_camera_photos_are_still_judged(tmp_path, name, fmt, make, model):
    _dark_pattern(tmp_path / name, fmt, make, model)
    assert [(c.rel, c.note) for c in find_bad_photos(tmp_path, False)] == [(name, "too dark")]
