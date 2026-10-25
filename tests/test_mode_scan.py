import threading

from mp4util import write

from photosort.modes._scan import read_each


def _files(root):
    for rel in ("b.jpg", "a.jpg", "sub/c.jpg"):
        write(root / rel, b"x")


def test_reads_each_file_in_scan_order(tmp_path):
    _files(tmp_path)
    seen = []

    def read(path):
        seen.append(path)
        return path.name.upper()

    files, values = read_each(tmp_path, False, read, None, None)
    assert files == ["a.jpg", "b.jpg"]
    assert values == {"a.jpg": "A.JPG", "b.jpg": "B.JPG"}
    assert seen == [tmp_path / "a.jpg", tmp_path / "b.jpg"]

    files, values = read_each(tmp_path, True, lambda p: p.stat().st_size, None, None)
    assert files == ["a.jpg", "b.jpg", "sub/c.jpg"]
    assert values == {"a.jpg": 1, "b.jpg": 1, "sub/c.jpg": 1}


def test_empty_folder(tmp_path):
    assert read_each(tmp_path, True, lambda p: 1, None, None) == ([], {})


def test_progress_after_each_file(tmp_path):
    _files(tmp_path)
    seen = []
    read_each(tmp_path, True, lambda p: None, lambda i, n: seen.append((i, n)), None)
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_cancel_before_a_file_stops_reading(tmp_path):
    _files(tmp_path)
    cancel = threading.Event()
    read = []

    def reader(path):
        read.append(path.name)
        cancel.set()

    assert read_each(tmp_path, True, reader, None, cancel) is None
    assert read == ["a.jpg"]


def test_cancel_during_the_last_file(tmp_path):
    write(tmp_path / "a.jpg", b"x")
    cancel = threading.Event()
    assert read_each(tmp_path, False, lambda p: cancel.set(), None, cancel) is None


def test_cancelled_from_the_start(tmp_path):
    cancel = threading.Event()
    cancel.set()
    assert read_each(tmp_path, False, lambda p: 1, None, cancel) is None
