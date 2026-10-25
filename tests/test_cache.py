import logging
import os
import sqlite3

import numpy as np
import pytest

from photosort.cache import AnalysisCache, _set_aside
from photosort.faces import Face, pack_faces, unpack_faces
from photosort.modes import _analysis


def _cache_db(root):
    return root / ".photosort" / "cache.db"


def test_round_trip(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache(tmp_path) as cache:
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"\x00data\xff")
        assert cache.get("photo-v1", photo) == b"\x00data\xff"
        assert cache.get("faces-v1", photo) is None  # kinds are kept apart
    with AnalysisCache(tmp_path) as cache:  # committed on exit
        assert cache.get("photo-v1", photo) == b"\x00data\xff"
    assert _cache_db(tmp_path).is_file()


def test_put_replaces(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache(tmp_path) as cache:
        cache.put("photo-v1", photo, b"old")
        cache.put("photo-v1", photo, b"new")
        assert cache.get("photo-v1", photo) == b"new"


def test_hit_after_move_within_root(tmp_path):
    photo = tmp_path / "IMG_1.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache(tmp_path) as cache:
        cache.put("photo-v1", photo, b"data")
    moved = tmp_path / "2025" / "img_1.JPG"  # moved, and spelt in another case
    moved.parent.mkdir()
    os.rename(photo, moved)
    with AnalysisCache(tmp_path) as cache:
        assert cache.get("photo-v1", moved) == b"data"


def test_miss_after_content_changes(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache(tmp_path) as cache:
        cache.put("photo-v1", photo, b"data")
    st = photo.stat()
    os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    with AnalysisCache(tmp_path) as cache:
        assert cache.get("photo-v1", photo) is None  # mtime differs
    photo.write_bytes(b"photo bytes, edited")
    os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns))
    with AnalysisCache(tmp_path) as cache:
        assert cache.get("photo-v1", photo) is None  # size differs


def test_missing_file_is_a_miss(tmp_path):
    with AnalysisCache(tmp_path) as cache:
        cache.put("photo-v1", tmp_path / "gone.jpg", b"data")  # does nothing
        assert cache.get("photo-v1", tmp_path / "gone.jpg") is None


def test_commits_every_200_puts(tmp_path):
    photos = []
    for n in range(201):
        photo = tmp_path / f"{n}.jpg"
        photo.write_bytes(b"x" * (n + 1))
        photos.append(photo)
    with AnalysisCache(tmp_path) as cache:
        for photo in photos:
            cache.put("photo-v1", photo, b"data")
        # Read through a second connection: only committed rows are seen.
        with sqlite3.connect(_cache_db(tmp_path)) as other:
            (count,) = other.execute("SELECT count(*) FROM entries").fetchone()
        other.close()
        assert count == 200


def test_corrupt_database_set_aside(tmp_path, caplog):
    db = _cache_db(tmp_path)
    db.parent.mkdir()
    db.write_bytes(b"this is not a database" * 100)
    (db.parent / "cache.db.broken-1").write_bytes(b"an earlier one")
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    caplog.set_level(logging.WARNING, logger="photosort")
    with AnalysisCache(tmp_path) as cache:
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"data")
        assert cache.get("photo-v1", photo) == b"data"
    assert (db.parent / "cache.db.broken-2").read_bytes() == b"this is not a database" * 100
    assert (db.parent / "cache.db.broken-1").read_bytes() == b"an earlier one"
    assert any("cache" in r.getMessage().lower() for r in caplog.records)


def test_side_files_set_aside_with_the_database(tmp_path):
    folder = _cache_db(tmp_path).parent
    folder.mkdir()
    (folder / "cache.db").write_bytes(b"this is not a database" * 100)
    (folder / "cache.db-journal").write_bytes(b"stale journal")
    (folder / "cache.db-wal").write_bytes(b"stale wal")
    (folder / "cache.db.broken-1").write_bytes(b"an earlier one")
    (folder / "cache.db.broken-2-journal").write_bytes(b"a stray side file")
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache(tmp_path) as cache:
        cache.put("photo-v1", photo, b"data")
        assert cache.get("photo-v1", photo) == b"data"
    # broken-2 is taken by a side file, so the set comes out as broken-3.
    assert (folder / "cache.db.broken-3").read_bytes() == b"this is not a database" * 100
    assert (folder / "cache.db.broken-2-journal").read_bytes() == b"a stray side file"
    assert (folder / "cache.db.broken-1").read_bytes() == b"an earlier one"
    # SQLite discards side files it can't use (the journal on opening, the
    # WAL on closing), and _set_aside takes any left: none stays stale beside
    # the fresh database. (test_set_aside_takes_every_side_file_... covers
    # side files still there when it runs.)
    for suffix, stale in [("-journal", b"stale journal"), ("-wal", b"stale wal")]:
        side = folder / f"cache.db{suffix}"
        assert not side.exists() or side.read_bytes() != stale


def test_set_aside_takes_every_side_file_under_one_free_number(tmp_path):
    db = tmp_path / "cache.db"
    for suffix, data in [("", b"db"), ("-journal", b"j"), ("-wal", b"w"), ("-shm", b"s")]:
        (tmp_path / f"cache.db{suffix}").write_bytes(data)
    (tmp_path / "cache.db.broken-1").write_bytes(b"older db")
    (tmp_path / "cache.db.broken-2-shm").write_bytes(b"older shm")
    assert _set_aside(db) == tmp_path / "cache.db.broken-3"
    for suffix, data in [("", b"db"), ("-journal", b"j"), ("-wal", b"w"), ("-shm", b"s")]:
        assert (tmp_path / f"cache.db.broken-3{suffix}").read_bytes() == data
        assert not (tmp_path / f"cache.db{suffix}").exists()
    assert (tmp_path / "cache.db.broken-1").read_bytes() == b"older db"
    assert (tmp_path / "cache.db.broken-2-shm").read_bytes() == b"older shm"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to read-only folders")
def test_read_only_folder_degrades_to_nothing(tmp_path, caplog):
    root = tmp_path / "ro"
    root.mkdir()
    photo = root / "a.jpg"
    photo.write_bytes(b"photo bytes")
    root.chmod(0o555)
    caplog.set_level(logging.WARNING, logger="photosort")
    try:
        with AnalysisCache(root) as cache:
            cache.put("photo-v1", photo, b"data")
            assert cache.get("photo-v1", photo) is None
    finally:
        root.chmod(0o755)
    assert not (root / ".photosort").exists()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_database_error_mid_run_degrades(tmp_path, caplog):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    caplog.set_level(logging.WARNING, logger="photosort")
    with AnalysisCache(tmp_path) as cache:
        cache._conn.execute("DROP TABLE entries")  # something goes wrong underneath
        cache.put("photo-v1", photo, b"data")
        cache.put("photo-v1", photo, b"data")
        assert cache.get("photo-v1", photo) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert sorted(p.name for p in _cache_db(tmp_path).parent.iterdir()) == ["cache.db"]


def test_locked_database_is_not_set_aside(tmp_path, monkeypatch, caplog):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache(tmp_path) as cache:
        cache.put("photo-v1", photo, b"data")
    real_connect = sqlite3.connect

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", locked)
    caplog.set_level(logging.WARNING, logger="photosort")
    with AnalysisCache(tmp_path) as cache:  # the run carries on without it
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"other")
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert sorted(p.name for p in _cache_db(tmp_path).parent.iterdir()) == ["cache.db"]
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    with AnalysisCache(tmp_path) as cache:
        assert cache.get("photo-v1", photo) == b"data"


class _Corrupt:
    """Stands in for a connection to a database found damaged while in use."""

    def __init__(self, real):
        self.real = real

    def execute(self, *args):
        raise sqlite3.DatabaseError("database disk image is malformed")

    def commit(self):
        raise sqlite3.DatabaseError("database disk image is malformed")

    def close(self):
        self.real.close()


def test_corruption_found_mid_run_sets_it_aside(tmp_path, caplog):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    caplog.set_level(logging.WARNING, logger="photosort")
    with AnalysisCache(tmp_path) as cache:
        cache.put("photo-v1", photo, b"data")
        cache._conn = _Corrupt(cache._conn)
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"data")  # off for the rest of the run
        assert cache.get("photo-v1", photo) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert (_cache_db(tmp_path).parent / "cache.db.broken-1").is_file()
    assert not _cache_db(tmp_path).exists()
    with AnalysisCache(tmp_path) as cache:  # the next run starts afresh
        cache.put("photo-v1", photo, b"new")
        assert cache.get("photo-v1", photo) == b"new"


def test_measure_photo_round_trips_through_cache(tmp_path, make_pattern_jpeg):
    photo = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    with AnalysisCache(tmp_path) as cache:
        measured = _analysis.measure_photo(photo, cache)
        assert _analysis.measure_photo(photo, cache) == measured
    assert measured == _analysis.measure_photo(photo)


def _face(seed: int, size=(1600, 1200)) -> Face:
    rng = np.random.default_rng(seed)
    embedding = rng.standard_normal(512).astype(np.float32)
    embedding /= np.linalg.norm(embedding)
    landmarks = rng.uniform(0, 1000, (5, 2)).astype(np.float32)
    box = tuple(float(v) for v in rng.uniform(0, 1000, 4))
    return Face(box, landmarks, float(rng.uniform(0.5, 1)), embedding, size)


@pytest.mark.parametrize("count", [0, 1, 3])
def test_faces_pack_round_trip(count):
    faces = [_face(n, (1600 + n, 1200 - n)) for n in range(count)]
    back = unpack_faces(pack_faces(faces))
    assert len(back) == count
    for a, b in zip(faces, back):
        assert b.box == a.box
        assert b.score == a.score
        assert b.image_size == a.image_size
        assert b.landmarks.dtype == np.float32 and b.landmarks.shape == (5, 2)
        assert np.array_equal(b.landmarks, a.landmarks)
        assert b.embedding.dtype == np.float32 and b.embedding.shape == (512,)
        assert np.array_equal(b.embedding, a.embedding)


def test_unpack_faces_refuses_bad_data():
    data = pack_faces([_face(1)])
    with pytest.raises(ValueError):
        unpack_faces(data[:-1])
    with pytest.raises(ValueError):
        unpack_faces(b"")
