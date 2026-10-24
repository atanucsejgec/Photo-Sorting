import os

import pytest

from photosort.media import kind
from photosort.scanner import scan


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _tree(root):
    _touch(root / "a.jpg")
    _touch(root / "sub" / "b.JPG")
    _touch(root / "notes.txt")


def test_scan_top_level_only(tmp_path):
    _tree(tmp_path)
    assert scan(tmp_path, False) == ["a.jpg"]


def test_scan_with_subfolders(tmp_path):
    _tree(tmp_path)
    assert scan(tmp_path, True) == ["a.jpg", "sub/b.JPG"]


def test_scan_skips_app_folders_any_depth(tmp_path):
    _touch(tmp_path / "keep.jpg")
    for d in ["Duplicates", "sub/bad photos", ".photosort", "Repeated Shots"]:
        _touch(tmp_path / d / "x.jpg")
    assert scan(tmp_path, True) == ["keep.jpg"]


def test_scan_skips_hidden_and_symlinks(tmp_path):
    _touch(tmp_path / "real.jpg")
    _touch(tmp_path / ".trashed-1.jpg")
    (tmp_path / "link.jpg").symlink_to(tmp_path / "real.jpg")
    _touch(tmp_path / ".thumbs" / "y.jpg")
    _touch(tmp_path / "sub" / "z.jpg")
    (tmp_path / "ldir").symlink_to(tmp_path / "sub", target_is_directory=True)
    assert scan(tmp_path, True) == ["real.jpg", "sub/z.jpg"]


def test_scan_skips_windows_system_dirs(tmp_path):
    _touch(tmp_path / "keep.jpg")
    _touch(tmp_path / "$RECYCLE.BIN" / "S-1-5-21" / "$R1.jpg")
    _touch(tmp_path / "System Volume Information" / "x.jpg")
    assert scan(tmp_path, True) == ["keep.jpg"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any folder")
def test_scan_skips_unreadable_dir(tmp_path):
    _touch(tmp_path / "keep.jpg")
    _touch(tmp_path / "other" / "b.jpg")
    locked = tmp_path / "locked"
    _touch(locked / "secret.jpg")
    locked.chmod(0o000)
    try:
        assert scan(tmp_path, True) == ["keep.jpg", "other/b.jpg"]
    finally:
        locked.chmod(0o755)


def test_kind():
    assert kind("x.HEIC") == "photo"
    assert kind("v.3gp") == "video"
    assert kind("a.txt") is None
