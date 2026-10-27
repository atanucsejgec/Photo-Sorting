"""scan_all_files: every file, of any type, minus the clutter it leaves alone."""

import os

import pytest

from photosort import scanner
from photosort.scanner import CLUTTER_NAMES, PARTIAL_SUFFIXES, scan, scan_all_files


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_every_kind_of_file_is_found(tmp_path):
    for name in ("app.apk", "setup.exe", "doc.pdf", "photo.jpg", "README"):
        _touch(tmp_path / name)
    assert scan_all_files(tmp_path, False) == [
        "README", "app.apk", "doc.pdf", "photo.jpg", "setup.exe"]


def test_the_skip_lists():
    assert PARTIAL_SUFFIXES == frozenset({".part", ".crdownload", ".download", ".partial",
                                          ".!qb"})
    assert CLUTTER_NAMES == frozenset({"desktop.ini", "thumbs.db", ".ds_store"})


def test_hidden_files_and_folders_are_skipped(tmp_path):
    _touch(tmp_path / "keep.txt")
    _touch(tmp_path / ".hidden.txt")
    _touch(tmp_path / ".config" / "x.txt")
    assert scan_all_files(tmp_path, True) == ["keep.txt"]


def test_symlinks_are_skipped(tmp_path):
    _touch(tmp_path / "real.txt")
    _touch(tmp_path / "sub" / "z.txt")
    try:
        (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
        (tmp_path / "ldir").symlink_to(tmp_path / "sub", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not allowed here")
    assert scan_all_files(tmp_path, True) == ["real.txt", "sub/z.txt"]


@pytest.mark.parametrize("folder", ["Duplicates", "repeated shots", "Bad Photos",
                                    "$RECYCLE.BIN", "System Volume Information"])
def test_skip_dirs_are_skipped(tmp_path, folder):
    _touch(tmp_path / "keep.txt")
    _touch(tmp_path / folder / "x.exe")
    _touch(tmp_path / "sub" / folder / "y.exe")
    assert scan_all_files(tmp_path, True) == ["keep.txt"]


@pytest.mark.parametrize("name", ["film.mkv.part", "setup.exe.crdownload", "a.download",
                                  "b.partial", "movie.mp4.!qb", "BIG.PART",
                                  "x.CrDownload"])
def test_unfinished_downloads_are_skipped(tmp_path, name):
    _touch(tmp_path / "keep.txt")
    _touch(tmp_path / name)
    _touch(tmp_path / "sub" / name)
    assert scan_all_files(tmp_path, True) == ["keep.txt"]


@pytest.mark.parametrize("name", ["desktop.ini", "Desktop.ini", "Thumbs.db", "THUMBS.DB"])
def test_system_clutter_is_skipped(tmp_path, name):
    _touch(tmp_path / "keep.txt")
    _touch(tmp_path / name)
    _touch(tmp_path / "sub" / name)
    assert scan_all_files(tmp_path, True) == ["keep.txt"]


def test_ds_store_is_skipped(tmp_path):
    _touch(tmp_path / "keep.txt")
    _touch(tmp_path / ".DS_Store")
    assert scan_all_files(tmp_path, True) == ["keep.txt"]


def test_windows_hidden_or_system_entries_are_skipped(tmp_path, monkeypatch):
    _touch(tmp_path / "keep.txt")
    _touch(tmp_path / "hidden.txt")
    _touch(tmp_path / "System" / "x.dll")
    _touch(tmp_path / "Programs" / "y.exe")
    monkeypatch.setattr(scanner, "_hidden_on_windows",
                        lambda entry: entry.name in ("hidden.txt", "System"))
    assert scan_all_files(tmp_path, True) == ["Programs/y.exe", "keep.txt"]


def test_no_windows_attributes_off_windows(tmp_path):
    _touch(tmp_path / "a.txt")
    with os.scandir(tmp_path) as entries:
        entry = next(entries)
    if hasattr(entry.stat(follow_symlinks=False), "st_file_attributes"):
        pytest.skip("this system has file attributes")
    assert scanner._hidden_on_windows(entry) is False


def test_subfolders_on_and_off(tmp_path):
    _touch(tmp_path / "a.zip")
    _touch(tmp_path / "sub" / "b.exe")
    _touch(tmp_path / "sub" / "deeper" / "c")
    assert scan_all_files(tmp_path, False) == ["a.zip"]
    assert scan_all_files(tmp_path, True) == ["a.zip", "sub/b.exe", "sub/deeper/c"]


def test_photosort_folder_is_never_entered(tmp_path):
    _touch(tmp_path / "keep.txt")
    _touch(tmp_path / ".photosort" / "runs" / "r.jsonl")
    _touch(tmp_path / "sub" / ".photosort" / "cache.db")
    assert scan_all_files(tmp_path, True) == ["keep.txt"]


def test_media_scan_is_unchanged(tmp_path):
    _touch(tmp_path / "a.jpg")
    _touch(tmp_path / "b.apk")
    assert scan(tmp_path, True) == ["a.jpg"]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="needs POSIX permissions, and root can read any folder")
def test_unreadable_subfolder_is_skipped(tmp_path):
    _touch(tmp_path / "keep.txt")
    locked = tmp_path / "locked"
    _touch(locked / "secret.txt")
    locked.chmod(0o000)
    try:
        assert scan_all_files(tmp_path, True) == ["keep.txt"]
    finally:
        locked.chmod(0o755)


def test_missing_root_raises(tmp_path):
    with pytest.raises(OSError):
        scan_all_files(tmp_path / "missing", True)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="needs POSIX permissions, and root can read any folder")
def test_unreadable_root_raises(tmp_path):
    root = tmp_path / "root"
    _touch(root / "a.txt")
    root.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            scan_all_files(root, False)
    finally:
        root.chmod(0o755)
