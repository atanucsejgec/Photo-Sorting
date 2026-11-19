import logging
import os
import sqlite3

import numpy as np
import pytest

from photosort import appdata
from photosort.cache import AnalysisCache, _set_aside, merge_cache
from photosort.faces import Face, pack_faces, unpack_faces
from photosort.modes import _analysis


def _cache_db():
    """The central cache's database."""
    return appdata.data_dir() / "cache.db"


def test_round_trip(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"\x00data\xff")
        assert cache.get("photo-v1", photo) == b"\x00data\xff"
        assert cache.get("faces-v1", photo) is None  # kinds are kept apart
    with AnalysisCache() as cache:  # committed on exit
        assert cache.get("photo-v1", photo) == b"\x00data\xff"
    assert _cache_db().is_file()


def test_cache_is_central(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    photo = photos / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"data")
    with AnalysisCache() as cache:  # a new cache, the same central database
        assert cache.get("photo-v1", photo) == b"data"
    assert AnalysisCache().path == appdata.data_dir() / "cache.db"
    assert _cache_db().is_file()
    assert os.listdir(photos) == ["a.jpg"]  # nothing created in the photo folder


def test_central_folder_is_created(tmp_path, monkeypatch):
    central = tmp_path / "not" / "yet" / "there"
    monkeypatch.setattr(appdata, "data_dir", lambda: central)
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"data")
    assert (central / "cache.db").is_file()


def test_entry_survives_a_move_between_roots(tmp_path):
    photo = tmp_path / "one" / "IMG_1.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"data")
    moved = tmp_path / "two" / "deeper" / "IMG_1.jpg"  # another folder altogether
    moved.parent.mkdir(parents=True)
    os.rename(photo, moved)
    with AnalysisCache() as cache:
        assert cache.get("photo-v1", moved) == b"data"


def _old_db(path, rows):
    """An old <root>/.photosort/cache.db holding rows (kind, name, size, mtime_ns, data)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE entries (
            kind TEXT, name TEXT, size INT, mtime_ns INT, data BLOB,
            PRIMARY KEY (kind, name, size, mtime_ns))""")
        conn.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?)", rows)
    conn.close()
    return path


def _central_rows():
    with sqlite3.connect(_cache_db()) as conn:
        rows = conn.execute("SELECT * FROM entries ORDER BY kind, name").fetchall()
    conn.close()
    return rows


def test_merge_cache_imports_entries(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    st = photo.stat()
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"central wins")
    old = _old_db(tmp_path / "pics" / ".photosort" / "cache.db", [
        ("photo-v1", "a.jpg", st.st_size, st.st_mtime_ns, b"old copy"),  # already there
        ("faces-v1", "a.jpg", st.st_size, st.st_mtime_ns, b"faces"),
        ("photo-v1", "b.jpg", 7, 123, b"b data"),
    ])
    before = old.read_bytes()
    assert merge_cache(old) is True
    assert old.read_bytes() == before  # the old db is never changed
    assert sorted(os.listdir(old.parent)) == ["cache.db"]
    with AnalysisCache() as cache:
        assert cache.get("photo-v1", photo) == b"central wins"  # OR IGNORE
        assert cache.get("faces-v1", photo) == b"faces"
    assert ("photo-v1", "b.jpg", 7, 123, b"b data") in _central_rows()
    assert len(_central_rows()) == 3
    assert merge_cache(old) is True  # again: nothing new, still fine
    assert len(_central_rows()) == 3


def test_merge_cache_many_entries(tmp_path):
    rows = [("photo-v1", f"{n}.jpg", n, n, bytes([n % 256]) * 10) for n in range(1234)]
    old = _old_db(tmp_path / ".photosort" / "cache.db", rows)
    assert merge_cache(old) is True
    assert len(_central_rows()) == 1234


@pytest.mark.parametrize("content", [b"this is not a database" * 100, b""])
def test_merge_cache_corrupt_returns_false(tmp_path, caplog, content):
    old = tmp_path / ".photosort" / "cache.db"
    old.parent.mkdir()
    old.write_bytes(content)
    with AnalysisCache() as cache:
        cache.put("photo-v1", old, b"central entry")
    rows = _central_rows()
    caplog.set_level(logging.WARNING, logger="photosort")
    assert merge_cache(old) is False
    assert old.read_bytes() == content  # left alone
    assert sorted(os.listdir(old.parent)) == ["cache.db"]
    assert _central_rows() == rows  # the central cache is untouched and kept
    assert sorted(os.listdir(appdata.data_dir())) == ["cache.db"]  # not set aside
    assert any("cache" in r.getMessage().lower() for r in caplog.records)


def test_merge_cache_damaged_part_way_returns_false(tmp_path, caplog):
    rows = [("photo-v1", f"{n}.jpg", n, n, b"x" * 2000) for n in range(500)]
    old = _old_db(tmp_path / ".photosort" / "cache.db", rows)
    data = bytearray(old.read_bytes())
    half = len(data) // 2
    data[half:half + 8192] = b"\xff" * 8192  # damage pages in the middle
    old.write_bytes(bytes(data))
    caplog.set_level(logging.WARNING, logger="photosort")
    assert merge_cache(old) is False
    assert old.read_bytes() == bytes(data)
    assert not _cache_db().exists() or _central_rows() == []  # all or nothing


def test_merge_cache_missing_or_wrong_db_returns_false(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="photosort")
    assert merge_cache(tmp_path / "gone.db") is False
    assert not (tmp_path / "gone.db").exists()  # not created by trying
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE something_else (x)")
    conn.close()
    before = other.read_bytes()
    assert merge_cache(other) is False
    assert other.read_bytes() == before


def test_merge_cache_into_corrupt_central_starts_it_afresh(tmp_path):
    central = _cache_db()
    central.parent.mkdir(parents=True, exist_ok=True)
    central.write_bytes(b"this is not a database" * 100)
    old = _old_db(tmp_path / ".photosort" / "cache.db", [("photo-v1", "a.jpg", 1, 2, b"d")])
    assert merge_cache(old) is True
    assert (central.parent / "cache.db.broken-1").is_file()
    assert _central_rows() == [("photo-v1", "a.jpg", 1, 2, b"d")]


def test_merge_cache_central_locked_returns_false(tmp_path, monkeypatch):
    old = _old_db(tmp_path / ".photosort" / "cache.db", [("photo-v1", "a.jpg", 1, 2, b"d")])
    with AnalysisCache():
        pass  # the central database exists
    holder = sqlite3.connect(_cache_db(), timeout=0)
    holder.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr("photosort.cache._TIMEOUT", 0.05)
    try:
        assert merge_cache(old) is False
    finally:
        holder.rollback()
        holder.close()
    assert sorted(os.listdir(appdata.data_dir())) == ["cache.db"]  # not set aside
    assert _central_rows() == []
    assert merge_cache(old) is True  # retried later, it works


def test_put_replaces(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"old")
        cache.put("photo-v1", photo, b"new")
        assert cache.get("photo-v1", photo) == b"new"


def test_hit_after_move_within_root(tmp_path):
    photo = tmp_path / "IMG_1.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"data")
    moved = tmp_path / "2025" / "img_1.JPG"  # moved, and spelt in another case
    moved.parent.mkdir()
    os.rename(photo, moved)
    with AnalysisCache() as cache:
        assert cache.get("photo-v1", moved) == b"data"


def test_miss_after_content_changes(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"data")
    st = photo.stat()
    os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    with AnalysisCache() as cache:
        assert cache.get("photo-v1", photo) is None  # mtime differs
    photo.write_bytes(b"photo bytes, edited")
    os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns))
    with AnalysisCache() as cache:
        assert cache.get("photo-v1", photo) is None  # size differs


def test_missing_file_is_a_miss(tmp_path):
    with AnalysisCache() as cache:
        cache.put("photo-v1", tmp_path / "gone.jpg", b"data")  # does nothing
        assert cache.get("photo-v1", tmp_path / "gone.jpg") is None


def test_commits_every_200_puts(tmp_path):
    photos = []
    for n in range(201):
        photo = tmp_path / f"{n}.jpg"
        photo.write_bytes(b"x" * (n + 1))
        photos.append(photo)
    with AnalysisCache() as cache:
        for photo in photos:
            cache.put("photo-v1", photo, b"data")
        # Read through a second connection: only committed rows are seen.
        with sqlite3.connect(_cache_db()) as other:
            (count,) = other.execute("SELECT count(*) FROM entries").fetchone()
        other.close()
        assert count == 200


def test_corrupt_database_set_aside(tmp_path, caplog):
    db = _cache_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is not a database" * 100)
    (db.parent / "cache.db.broken-1").write_bytes(b"an earlier one")
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    caplog.set_level(logging.WARNING, logger="photosort")
    with AnalysisCache() as cache:
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"data")
        assert cache.get("photo-v1", photo) == b"data"
    assert (db.parent / "cache.db.broken-2").read_bytes() == b"this is not a database" * 100
    assert (db.parent / "cache.db.broken-1").read_bytes() == b"an earlier one"
    assert any("cache" in r.getMessage().lower() for r in caplog.records)


def test_side_files_set_aside_with_the_database(tmp_path):
    folder = _cache_db().parent
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cache.db").write_bytes(b"this is not a database" * 100)
    (folder / "cache.db-journal").write_bytes(b"stale journal")
    (folder / "cache.db-wal").write_bytes(b"stale wal")
    (folder / "cache.db.broken-1").write_bytes(b"an earlier one")
    (folder / "cache.db.broken-2-journal").write_bytes(b"a stray side file")
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
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
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    central = appdata.data_dir()
    central.mkdir(parents=True, exist_ok=True)
    central.chmod(0o555)
    caplog.set_level(logging.WARNING, logger="photosort")
    try:
        with AnalysisCache() as cache:
            cache.put("photo-v1", photo, b"data")
            assert cache.get("photo-v1", photo) is None
    finally:
        central.chmod(0o755)
    assert not _cache_db().exists()
    assert sorted(os.listdir(tmp_path)) == ["a.jpg"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to read-only folders")
def test_uncreatable_folder_degrades_to_nothing(tmp_path, caplog):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    caplog.set_level(logging.WARNING, logger="photosort")
    try:
        with AnalysisCache(locked / "app" / "cache.db") as cache:
            cache.put("photo-v1", photo, b"data")
            assert cache.get("photo-v1", photo) is None
    finally:
        locked.chmod(0o755)
    assert os.listdir(locked) == []
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_database_error_mid_run_degrades(tmp_path, caplog):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    caplog.set_level(logging.WARNING, logger="photosort")
    with AnalysisCache() as cache:
        cache._conn.execute("DROP TABLE entries")  # something goes wrong underneath
        cache.put("photo-v1", photo, b"data")
        cache.put("photo-v1", photo, b"data")
        assert cache.get("photo-v1", photo) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert sorted(p.name for p in _cache_db().parent.iterdir()) == ["cache.db"]


def test_locked_database_is_not_set_aside(tmp_path, monkeypatch, caplog):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo bytes")
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"data")
    real_connect = sqlite3.connect

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", locked)
    caplog.set_level(logging.WARNING, logger="photosort")
    with AnalysisCache() as cache:  # the run carries on without it
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"other")
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert sorted(p.name for p in _cache_db().parent.iterdir()) == ["cache.db"]
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    with AnalysisCache() as cache:
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
    with AnalysisCache() as cache:
        cache.put("photo-v1", photo, b"data")
        cache._conn = _Corrupt(cache._conn)
        assert cache.get("photo-v1", photo) is None
        cache.put("photo-v1", photo, b"data")  # off for the rest of the run
        assert cache.get("photo-v1", photo) is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert (_cache_db().parent / "cache.db.broken-1").is_file()
    assert not _cache_db().exists()
    with AnalysisCache() as cache:  # the next run starts afresh
        cache.put("photo-v1", photo, b"new")
        assert cache.get("photo-v1", photo) == b"new"


def test_measure_photo_round_trips_through_cache(tmp_path, make_pattern_jpeg):
    photo = make_pattern_jpeg(tmp_path / "a.jpg", 1)
    with AnalysisCache() as cache:
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
