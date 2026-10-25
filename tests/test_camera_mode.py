import threading

from mp4util import mdta_meta, mp4, write
from PIL import Image

from photosort.modes.camera_mode import prepare_camera_plan


def jpeg(path, make=None, model=None):
    """A JPEG with the given EXIF Make and Model (either may be None)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if make is not None:
        exif[271] = make
    if model is not None:
        exif[272] = model
    Image.new("RGB", (8, 8), "red").save(path, "JPEG", exif=exif)
    return path


def _dsts(plan):
    return {m.src: m.dst for m in plan.moves}


def test_one_folder_per_camera(tmp_path):
    jpeg(tmp_path / "a.jpg", "OPPO", "CPH2581")
    jpeg(tmp_path / "b.jpg", "OPPO", "CPH2581")
    jpeg(tmp_path / "c.jpg", "Google", "Pixel 7")
    jpeg(tmp_path / "d.jpg", model="Pixel 7")
    plan = prepare_camera_plan(tmp_path, False)
    assert plan.mode == "camera"
    assert _dsts(plan) == {
        "a.jpg": "OPPO CPH2581/a.jpg",
        "b.jpg": "OPPO CPH2581/b.jpg",
        "c.jpg": "Google Pixel 7/c.jpg",
        "d.jpg": "Pixel 7/d.jpg",
    }
    assert plan.summary == ["4 files → 3 camera folders"]
    assert plan.unchanged == 0


def test_no_camera_info_stays(tmp_path):
    jpeg(tmp_path / "a.jpg", "OPPO", "CPH2581")
    jpeg(tmp_path / "plain.jpg")
    write(tmp_path / "broken.jpg", b"not a jpeg")
    jpeg(tmp_path / "junk.jpg", "...", "::")  # no letter or digit: no folder
    plan = prepare_camera_plan(tmp_path, False)
    assert _dsts(plan) == {"a.jpg": "OPPO CPH2581/a.jpg"}
    assert plan.summary == [
        "1 files → 1 camera folders",
        "3 files have no camera info and stay where they are",
    ]


def test_one_file_without_camera_info(tmp_path):
    jpeg(tmp_path / "plain.jpg")
    plan = prepare_camera_plan(tmp_path, False)
    assert plan.moves == []
    assert plan.summary == ["1 files have no camera info and stay where they are"]


def test_video_with_apple_keys(tmp_path):
    meta = mdta_meta({"com.apple.quicktime.make": "Apple",
                      "com.apple.quicktime.model": "iPhone 12"})
    write(tmp_path / "v.mov", mp4(meta))
    write(tmp_path / "w.mp4", mp4(mdta_meta({"com.android.version": "13"})))
    plan = prepare_camera_plan(tmp_path, False)
    assert _dsts(plan) == {"v.mov": "Apple iPhone 12/v.mov"}
    assert plan.summary == [
        "1 files → 1 camera folders",
        "1 files have no camera info and stay where they are",
    ]


def test_rerun_with_subfolders_is_unchanged(tmp_path):
    jpeg(tmp_path / "OPPO CPH2581" / "a.jpg", "OPPO", "CPH2581")
    jpeg(tmp_path / "oppo cph2581" / "b.jpg", "OPPO", "CPH2581")  # any case
    jpeg(tmp_path / "Pixel 7" / "c.jpg", "OPPO", "CPH2581")  # in the wrong folder
    plan = prepare_camera_plan(tmp_path, True)
    assert plan.unchanged == 2
    assert len(plan.moves) == 1
    assert plan.moves[0].src == "Pixel 7/c.jpg"
    assert plan.moves[0].dst in {"OPPO CPH2581/c.jpg", "oppo cph2581/c.jpg"}
    assert plan.summary == ["1 files → 1 camera folders", "2 already in place"]


def test_existing_folder_in_another_case_is_reused(tmp_path):
    (tmp_path / "oppo cph2581").mkdir()
    jpeg(tmp_path / "a.jpg", "OPPO", "CPH2581")
    jpeg(tmp_path / "b.jpg", "Oppo", "cph2581")
    plan = prepare_camera_plan(tmp_path, False)
    assert _dsts(plan) == {"a.jpg": "oppo cph2581/a.jpg", "b.jpg": "oppo cph2581/b.jpg"}
    assert plan.summary == ["2 files → 1 camera folders"]


def test_name_clash_in_the_camera_folder(tmp_path):
    jpeg(tmp_path / "OPPO CPH2581" / "A.JPG", "OPPO", "CPH2581")
    jpeg(tmp_path / "a.jpg", "OPPO", "CPH2581")
    plan = prepare_camera_plan(tmp_path, False)
    assert _dsts(plan) == {"a.jpg": "OPPO CPH2581/a (1).jpg"}


def test_subfolders_off_leaves_them_alone(tmp_path):
    jpeg(tmp_path / "sub" / "a.jpg", "OPPO", "CPH2581")
    plan = prepare_camera_plan(tmp_path, False)
    assert plan.moves == [] and plan.summary == []


def test_progress_and_cancel(tmp_path):
    for name in "abc":
        jpeg(tmp_path / f"{name}.jpg", "OPPO", "CPH2581")
    seen = []
    prepare_camera_plan(tmp_path, False, progress=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 3), (2, 3), (3, 3)]

    cancel = threading.Event()
    calls = []

    def progress(i, n):
        calls.append(i)
        cancel.set()

    assert prepare_camera_plan(tmp_path, False, progress=progress, cancel=cancel) is None
    assert calls == [1]
