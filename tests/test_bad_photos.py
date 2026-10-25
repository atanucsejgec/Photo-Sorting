import threading
from datetime import datetime

from PIL import Image

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
