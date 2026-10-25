import shutil
import threading

import pytest
from PIL import Image

from photosort.executor import execute
from photosort.groups import plan_set_aside
from photosort.imageinfo import open_image, perceptual_hash
from photosort.journal import RunJournal
from photosort.modes import _analysis
from photosort.modes.duplicates import find_duplicate_groups
from photosort.thresholds import DUP_HASH_MAX


def _rels(group):
    return [c.rel for c in group.members]


def test_exact_copies_grouped(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a/x.jpg", 1)
    (tmp_path / "b").mkdir()
    shutil.copyfile(tmp_path / "a/x.jpg", tmp_path / "b/x.jpg")
    make_pattern_jpeg(tmp_path / "other.jpg", 2)
    groups = find_duplicate_groups(tmp_path, include_subfolders=True)
    assert len(groups) == 1
    group = groups[0]
    assert group.kind == "exact"
    assert _rels(group) == ["a/x.jpg", "b/x.jpg"]
    assert [c.note for c in group.members] == ["Exact copy", "Exact copy"]
    assert group.suggested_keep == {"a/x.jpg"}
    first = group.members[0]
    assert (first.kind, first.width, first.height) == ("photo", 800, 600)
    assert first.size == (tmp_path / "a/x.jpg").stat().st_size
    assert first.sharpness is not None and first.sharpness > 0


def test_subfolders_off_ignores_nested_copies(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "x.jpg", 1)
    (tmp_path / "b").mkdir()
    shutil.copyfile(tmp_path / "x.jpg", tmp_path / "b/x.jpg")
    assert find_duplicate_groups(tmp_path, include_subfolders=False) == []


def test_exact_video_copies_grouped(tmp_path, make_mp4):
    make_mp4(tmp_path / "clip.mp4", "2025-03-01T10:10:10Z")
    shutil.copyfile(tmp_path / "clip.mp4", tmp_path / "clip copy.mp4")
    groups = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert len(groups) == 1
    group = groups[0]
    assert group.kind == "exact"
    assert _rels(group) == ["clip copy.mp4", "clip.mp4"]
    for c in group.members:
        assert (c.kind, c.width, c.height, c.sharpness) == ("video", None, None, None)
        assert c.note == "Exact copy"
    assert group.suggested_keep == {"clip copy.mp4"}  # identical: first in path order


def test_resized_copy_grouped(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "IMG20250220181325.jpg", 3, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "IMG-20250220-WA0012.jpg", 3, size=(400, 300), quality=60)
    groups = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert len(groups) == 1
    group = groups[0]
    assert group.kind == "resized"
    assert group.suggested_keep == {"IMG20250220181325.jpg"}
    notes = {c.rel: c.note for c in group.members}
    assert notes == {
        "IMG-20250220-WA0012.jpg": "Smaller copy (WhatsApp)",
        "IMG20250220181325.jpg": "",
    }


def test_smaller_copy_note(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "big.jpg", 3)
    make_pattern_jpeg(tmp_path / "small.jpg", 3, size=(400, 300), quality=60)
    [group] = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert {c.rel: c.note for c in group.members} == {"big.jpg": "", "small.jpg": "Smaller copy"}


def test_different_aspect_not_duplicate(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a.jpg", 3)
    original = open_image(tmp_path / "a.jpg")
    original.resize((800, 500)).save(tmp_path / "b.jpg", "JPEG", quality=90)
    assert find_duplicate_groups(tmp_path, include_subfolders=False) == []


def test_burst_not_duplicate(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a.jpg", 4, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "b.jpg", 4, shift=3, exif_dt="2025:02:20 18:13:29")
    assert find_duplicate_groups(tmp_path, include_subfolders=False) == []


def test_burst_one_second_apart_not_duplicate(tmp_path, make_pattern_jpeg):
    a = make_pattern_jpeg(tmp_path / "IMG20250220181325.jpg", 4, exif_dt="2025:02:20 18:13:25")
    b = make_pattern_jpeg(tmp_path / "IMG20250220181326.jpg", 4, shift=3,
                          exif_dt="2025:02:20 18:13:26")
    # They look alike enough to be copies: only the second apart tells them apart.
    assert (perceptual_hash(a) ^ perceptual_hash(b)).bit_count() <= DUP_HASH_MAX
    assert find_duplicate_groups(tmp_path, include_subfolders=False) == []


def test_same_second_resave_is_duplicate(tmp_path, make_pattern_jpeg):
    # A phone re-save: same resolution, same EXIF second, different bytes.
    make_pattern_jpeg(tmp_path / "IMG20250601071647.jpg", 4, exif_dt="2025:06:01 07:16:47")
    make_pattern_jpeg(tmp_path / "IMG20250601071647~2.jpg", 4, quality=60,
                      exif_dt="2025:06:01 07:16:47")
    groups = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert len(groups) == 1
    group = groups[0]
    assert group.kind == "resized"
    assert _rels(group) == ["IMG20250601071647.jpg", "IMG20250601071647~2.jpg"]
    assert group.suggested_keep == {"IMG20250601071647.jpg"}  # same pixels: the larger file


@pytest.mark.parametrize("b_time", ["18:13:26", "18:13:29"])
def test_burst_not_joined_through_undated_copy(tmp_path, make_pattern_jpeg, b_time):
    # shared.jpg matches both shots, but a and b were taken at different
    # seconds: a group must never span two moments, whatever links it.
    a = make_pattern_jpeg(tmp_path / "a.jpg", 4, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "b.jpg", 4, shift=3, exif_dt=f"2025:02:20 {b_time}")
    open_image(a).resize((400, 300)).save(tmp_path / "shared.jpg", "JPEG", quality=80)
    groups = find_duplicate_groups(tmp_path, include_subfolders=False)
    for group in groups:
        assert not {"a.jpg", "b.jpg"} <= set(_rels(group))
    assert [_rels(g) for g in groups] == [["a.jpg", "shared.jpg"]]
    assert groups[0].suggested_keep == {"a.jpg"}


def test_rotated_camera_photo_and_upright_copy(tmp_path, make_pattern_jpeg):
    camera = make_pattern_jpeg(tmp_path / "camera.jpg", 5, orientation=6)
    upright = open_image(camera).resize((300, 400))
    upright.save(tmp_path / "copy.jpg", "JPEG", quality=80)
    groups = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert len(groups) == 1
    group = groups[0]
    assert _rels(group) == ["camera.jpg", "copy.jpg"]
    sizes = {c.rel: (c.width, c.height) for c in group.members}
    assert sizes == {"camera.jpg": (600, 800), "copy.jpg": (300, 400)}
    assert group.suggested_keep == {"camera.jpg"}


def test_broken_files_skipped(tmp_path, make_pattern_jpeg):
    (tmp_path / "empty.jpg").write_bytes(b"")
    (tmp_path / "text.jpg").write_text("this is not a photo")
    Image.new("RGBA", (300, 200), (10, 200, 30, 128)).save(tmp_path / "alpha.png")
    make_pattern_jpeg(tmp_path / "pair.jpg", 6)
    make_pattern_jpeg(tmp_path / "pair small.jpg", 6, size=(400, 300), quality=60)
    groups = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert [_rels(g) for g in groups] == [["pair small.jpg", "pair.jpg"]]


def test_unreadable_exact_copies_still_grouped(tmp_path):
    (tmp_path / "a.jpg").write_text("this is not a photo")
    (tmp_path / "b.jpg").write_text("this is not a photo")
    [group] = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert group.kind == "exact"
    assert _rels(group) == ["a.jpg", "b.jpg"]
    assert all(c.width is None and c.sharpness is None for c in group.members)


def test_groups_sorted_by_first_member(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "m.jpg", 7)
    shutil.copyfile(tmp_path / "m.jpg", tmp_path / "z.jpg")
    make_pattern_jpeg(tmp_path / "c.jpg", 8)
    shutil.copyfile(tmp_path / "c.jpg", tmp_path / "d.jpg")
    groups = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert [_rels(g) for g in groups] == [["c.jpg", "d.jpg"], ["m.jpg", "z.jpg"]]


def test_set_aside_files_not_found_again(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a/x.jpg", 1)
    (tmp_path / "b").mkdir()
    shutil.copyfile(tmp_path / "a/x.jpg", tmp_path / "b/x.jpg")
    make_pattern_jpeg(tmp_path / "big.jpg", 3)
    make_pattern_jpeg(tmp_path / "small.jpg", 3, size=(400, 300), quality=60)
    groups = find_duplicate_groups(tmp_path, include_subfolders=True)
    assert len(groups) == 2
    keep = {i: g.suggested_keep for i, g in enumerate(groups)}
    plan = plan_set_aside(tmp_path, "duplicates", "Duplicates", groups, keep)
    journal = RunJournal.create(tmp_path, "duplicates", True, len(plan.moves))
    result = execute(tmp_path, plan, journal)
    assert result.moved == 2
    assert (tmp_path / "Duplicates/x.jpg").is_file()
    assert (tmp_path / "Duplicates/small.jpg").is_file()
    assert find_duplicate_groups(tmp_path, include_subfolders=True) == []


def test_progress_reported_per_file(tmp_path, make_pattern_jpeg):
    for n in range(3):
        make_pattern_jpeg(tmp_path / f"{n}.jpg", n, size=(80, 60))
    calls = []
    find_duplicate_groups(tmp_path, False, progress=lambda i, total: calls.append((i, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_cancel_returns_none(tmp_path, make_pattern_jpeg):
    for n in range(3):
        make_pattern_jpeg(tmp_path / f"{n}.jpg", n, size=(80, 60))
    cancel = threading.Event()
    cancel.set()
    assert find_duplicate_groups(tmp_path, False, cancel=cancel) is None

    cancel = threading.Event()
    calls = []

    def progress(i, total):
        calls.append(i)
        cancel.set()

    assert find_duplicate_groups(tmp_path, False, progress=progress, cancel=cancel) is None
    assert calls == [1]


def test_duplicates_second_run_uses_cache(tmp_path, make_pattern_jpeg, monkeypatch):
    make_pattern_jpeg(tmp_path / "x.jpg", 1)
    shutil.copyfile(tmp_path / "x.jpg", tmp_path / "x copy.jpg")
    make_pattern_jpeg(tmp_path / "small.jpg", 1, size=(400, 300), quality=60)
    calls = []
    real_analyse = _analysis.analyse

    def counting(path):
        calls.append(path)
        return real_analyse(path)

    monkeypatch.setattr(_analysis, "analyse", counting)
    first = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert len(calls) == 3
    calls.clear()
    second = find_duplicate_groups(tmp_path, include_subfolders=False)
    assert calls == []
    assert [[(c.rel, c.width, c.height, c.sharpness) for c in g.members] for g in second] \
        == [[(c.rel, c.width, c.height, c.sharpness) for c in g.members] for g in first]
    assert len(first) == 1 and len(first[0].members) == 3
