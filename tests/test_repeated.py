import shutil
import threading

from photosort.imageinfo import perceptual_hash
from photosort.modes.repeated import find_repeated_groups
from photosort.thresholds import REPEAT_HASH_MAX


def _rels(group):
    return [c.rel for c in group.members]


def test_burst_grouped(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a.jpg", 5, shift=0, blur=0, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "b.jpg", 5, shift=2, blur=2, exif_dt="2025:02:20 18:13:26")
    make_pattern_jpeg(tmp_path / "c.jpg", 5, shift=4, blur=0.5, exif_dt="2025:02:20 18:13:27")
    groups = find_repeated_groups(tmp_path, include_subfolders=False)
    assert len(groups) == 1
    group = groups[0]
    assert group.kind == "repeated"
    assert _rels(group) == ["a.jpg", "b.jpg", "c.jpg"]
    assert group.suggested_keep == {"a.jpg"}
    first = group.members[0]
    assert (first.kind, first.width, first.height) == ("photo", 800, 600)
    assert first.size == (tmp_path / "a.jpg").stat().st_size
    assert first.sharpness is not None and first.sharpness > 0


def test_far_in_time_not_grouped(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a.jpg", 5, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "b.jpg", 5, shift=2, exif_dt="2025:02:20 18:23:25")
    assert find_repeated_groups(tmp_path, include_subfolders=False) == []


def test_different_scene_breaks_chain(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a.jpg", 5, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "b.jpg", 6, exif_dt="2025:02:20 18:13:26")
    make_pattern_jpeg(tmp_path / "c.jpg", 7, exif_dt="2025:02:20 18:13:27")
    assert find_repeated_groups(tmp_path, include_subfolders=False) == []


def test_no_date_ignored(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "holiday.jpg", 5)
    make_pattern_jpeg(tmp_path / "holiday 2.jpg", 5, shift=2)
    assert find_repeated_groups(tmp_path, include_subfolders=False) == []


def test_filename_time_used(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "IMG20250220181325.jpg", 5)
    make_pattern_jpeg(tmp_path / "IMG20250220181326.jpg", 5, shift=2)
    groups = find_repeated_groups(tmp_path, include_subfolders=False)
    assert [_rels(g) for g in groups] == [["IMG20250220181325.jpg", "IMG20250220181326.jpg"]]


def test_exact_copy_not_repeat(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a/x.jpg", 5, exif_dt="2025:02:20 18:13:25")
    (tmp_path / "b").mkdir()
    shutil.copyfile(tmp_path / "a/x.jpg", tmp_path / "b/x.jpg")
    assert find_repeated_groups(tmp_path, include_subfolders=True) == []


def test_copy_breaks_chain_and_becomes_previous(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a/x.jpg", 5, exif_dt="2025:02:20 18:13:25")
    (tmp_path / "b").mkdir()
    shutil.copyfile(tmp_path / "a/x.jpg", tmp_path / "b/x.jpg")
    make_pattern_jpeg(tmp_path / "c.jpg", 5, shift=2, exif_dt="2025:02:20 18:13:26")
    groups = find_repeated_groups(tmp_path, include_subfolders=True)
    assert [_rels(g) for g in groups] == [["b/x.jpg", "c.jpg"]]


def test_orientation_change_breaks_chain(tmp_path, make_pattern_jpeg):
    wide = make_pattern_jpeg(tmp_path / "a.jpg", 5, exif_dt="2025:02:20 18:13:25")
    square = make_pattern_jpeg(tmp_path / "b.jpg", 5, size=(600, 600),
                               exif_dt="2025:02:20 18:13:26")
    # The pattern looks the same at any shape, so only the orientation differs.
    assert (perceptual_hash(wide) ^ perceptual_hash(square)).bit_count() <= REPEAT_HASH_MAX
    assert find_repeated_groups(tmp_path, include_subfolders=False) == []


def test_window_is_between_neighbours(tmp_path, make_pattern_jpeg):
    # Each shot is 3 s after the one before, so all chain, though the burst spans 9 s.
    for n in range(4):
        make_pattern_jpeg(tmp_path / f"{n}.jpg", 5, shift=n,
                          exif_dt=f"2025:02:20 18:13:{25 + 3 * n}")
    make_pattern_jpeg(tmp_path / "late.jpg", 5, shift=4, exif_dt="2025:02:20 18:13:38")
    groups = find_repeated_groups(tmp_path, include_subfolders=False)
    assert [_rels(g) for g in groups] == [["0.jpg", "1.jpg", "2.jpg", "3.jpg"]]


def test_members_and_groups_in_time_order(tmp_path, make_pattern_jpeg):
    # Names sort the other way round from the times taken.
    make_pattern_jpeg(tmp_path / "z1.jpg", 5, exif_dt="2025:02:20 09:00:00")
    make_pattern_jpeg(tmp_path / "y1.jpg", 5, shift=2, exif_dt="2025:02:20 09:00:01")
    make_pattern_jpeg(tmp_path / "b2.jpg", 6, exif_dt="2025:02:20 10:00:00")
    make_pattern_jpeg(tmp_path / "a2.jpg", 6, shift=2, exif_dt="2025:02:20 10:00:02")
    groups = find_repeated_groups(tmp_path, include_subfolders=False)
    assert [_rels(g) for g in groups] == [["z1.jpg", "y1.jpg"], ["b2.jpg", "a2.jpg"]]
    rels = [rel for g in groups for rel in _rels(g)]
    assert len(rels) == len(set(rels))  # no file in two groups


def test_broken_files_and_videos_skipped(tmp_path, make_pattern_jpeg):
    (tmp_path / "IMG20250220181326.jpg").write_text("this is not a photo")
    (tmp_path / "VID20250220181326.mp4").write_bytes(b"not a video")
    make_pattern_jpeg(tmp_path / "IMG20250220181325.jpg", 5)
    make_pattern_jpeg(tmp_path / "IMG20250220181327.jpg", 5, shift=2)
    groups = find_repeated_groups(tmp_path, include_subfolders=False)
    assert [_rels(g) for g in groups] == [["IMG20250220181325.jpg", "IMG20250220181327.jpg"]]


def test_subfolders_off_ignores_nested_shots(tmp_path, make_pattern_jpeg):
    make_pattern_jpeg(tmp_path / "a.jpg", 5, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(tmp_path / "sub/b.jpg", 5, shift=2, exif_dt="2025:02:20 18:13:26")
    assert find_repeated_groups(tmp_path, include_subfolders=False) == []
    assert len(find_repeated_groups(tmp_path, include_subfolders=True)) == 1


def test_progress_reported_per_file(tmp_path, make_pattern_jpeg):
    for n in range(3):
        make_pattern_jpeg(tmp_path / f"{n}.jpg", n, size=(80, 60))
    calls = []
    find_repeated_groups(tmp_path, False, progress=lambda i, total: calls.append((i, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_cancel_returns_none(tmp_path, make_pattern_jpeg):
    for n in range(3):
        make_pattern_jpeg(tmp_path / f"{n}.jpg", n, size=(80, 60))
    cancel = threading.Event()
    cancel.set()
    assert find_repeated_groups(tmp_path, False, cancel=cancel) is None

    cancel = threading.Event()
    calls = []

    def progress(i, total):
        calls.append(i)
        cancel.set()

    assert find_repeated_groups(tmp_path, False, progress=progress, cancel=cancel) is None
    assert calls == [1]
