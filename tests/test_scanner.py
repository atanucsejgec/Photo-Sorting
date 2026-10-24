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
    assert scan(tmp_path, True) == ["real.jpg"]


def test_kind():
    assert kind("x.HEIC") == "photo"
    assert kind("v.3gp") == "video"
    assert kind("a.txt") is None
