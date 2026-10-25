import threading

import pytest
from mp4util import mdta_meta, mp4, write
from PIL import Image

from photosort.modes.type_mode import (
    CAMERA_PHOTOS, OTHER, SCREENSHOTS, VIDEOS, WHATSAPP, prepare_type_plan, source_of,
)


def image(path, make=None, model=None, fmt="JPEG"):
    """A small photo with the given EXIF Make and Model (either may be None)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if make is not None:
        exif[271] = make
    if model is not None:
        exif[272] = model
    Image.new("RGB", (8, 8), "red").save(path, fmt, exif=exif)
    return path


def video(path):
    return write(path, mp4(mdta_meta({"com.android.version": "13"})))


def _all_kinds(root):
    """One file of every kind; returns {name: expected folder}."""
    image(root / "IMG-20250315-WA0036.jpg")
    video(root / "VID-20250315-WA0001.mp4")
    image(root / "Screenshot_2025-03-01-10-00-00.png", fmt="PNG")
    image(root / "Screen Shot 2025.jpg")
    image(root / "x.png", fmt="PNG")
    video(root / "VID20250627.mp4")
    image(root / "IMG20250627.jpg", "OPPO", "CPH2581")
    image(root / "plain.jpg")
    write(root / "broken.jpg", b"not a jpeg")
    return {
        "IMG-20250315-WA0036.jpg": WHATSAPP,
        "VID-20250315-WA0001.mp4": WHATSAPP,
        "Screenshot_2025-03-01-10-00-00.png": SCREENSHOTS,
        "Screen Shot 2025.jpg": SCREENSHOTS,
        "x.png": SCREENSHOTS,
        "VID20250627.mp4": VIDEOS,
        "IMG20250627.jpg": CAMERA_PHOTOS,
        "plain.jpg": OTHER,
        "broken.jpg": OTHER,
    }


def test_folder_names():
    assert (CAMERA_PHOTOS, SCREENSHOTS, WHATSAPP, VIDEOS, OTHER) == (
        "Camera Photos", "Screenshots", "WhatsApp", "Videos", "Other")


def test_source_of_each_kind(tmp_path):
    expected = _all_kinds(tmp_path)
    assert {name: source_of(tmp_path / name) for name in expected} == expected


@pytest.mark.parametrize("name, fmt, make, folder", [
    ("img-20250315-wa0036.JPG", "JPEG", "OPPO", WHATSAPP),  # WhatsApp wins, any case
    ("screenshot.jpg", "JPEG", "OPPO", SCREENSHOTS),  # the name wins over a camera
    ("SCREEN-SHOT.webp", "WEBP", None, SCREENSHOTS),
    ("photo.png", "PNG", "Canon", CAMERA_PHOTOS),  # a png from a camera
    ("photo.webp", "WEBP", None, OTHER),
    ("WA0036.jpg", "JPEG", None, OTHER),  # no dash: not WhatsApp's pattern
    ("IMG-WA.jpg", "JPEG", None, OTHER),  # no number
])
def test_source_of_rules(tmp_path, name, fmt, make, folder):
    assert source_of(image(tmp_path / name, make, fmt=fmt)) == folder


def test_unreadable_png_is_other(tmp_path):
    assert source_of(write(tmp_path / "x.png", b"not a png")) == OTHER


def test_screenshot_named_video_is_a_video(tmp_path):
    assert source_of(video(tmp_path / "Screenshot_2025.mp4")) == VIDEOS


def test_type_plan_and_summary(tmp_path):
    expected = _all_kinds(tmp_path)
    plan = prepare_type_plan(tmp_path, False)
    assert plan.mode == "type"
    assert {m.src: m.dst for m in plan.moves} == {
        name: f"{folder}/{name}" for name, folder in expected.items()}
    assert plan.summary == [
        "9 files → Camera Photos 1, Screenshots 3, WhatsApp 2, Videos 1, Other 2"]
    assert plan.unchanged == 0


def test_summary_leaves_out_empty_folders(tmp_path):
    video(tmp_path / "a.mp4")
    image(tmp_path / "b.jpg", "OPPO", "CPH2581")
    plan = prepare_type_plan(tmp_path, False)
    assert plan.summary == ["2 files → Camera Photos 1, Videos 1"]


def test_rerun_leaves_them_all_unchanged(tmp_path):
    expected = _all_kinds(tmp_path)
    for name, folder in expected.items():
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / name).rename(tmp_path / folder / name)
    plan = prepare_type_plan(tmp_path, True)
    assert plan.moves == []
    assert plan.unchanged == 9
    assert plan.summary == ["9 already in place"]


def test_existing_folder_in_another_case_is_reused(tmp_path):
    (tmp_path / "videos").mkdir()
    video(tmp_path / "videos" / "a.mp4")
    video(tmp_path / "b.mp4")
    plan = prepare_type_plan(tmp_path, True)
    assert {m.src: m.dst for m in plan.moves} == {"b.mp4": "videos/b.mp4"}
    assert plan.summary == ["1 files → Videos 1", "1 already in place"]


def test_progress_and_cancel(tmp_path):
    for name in "abc":
        image(tmp_path / f"{name}.jpg")
    seen = []
    prepare_type_plan(tmp_path, False, progress=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 3), (2, 3), (3, 3)]

    cancel = threading.Event()
    calls = []

    def progress(i, n):
        calls.append(i)
        cancel.set()

    assert prepare_type_plan(tmp_path, False, progress=progress, cancel=cancel) is None
    assert calls == [1]
