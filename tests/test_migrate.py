import logging
import os
import sqlite3
import threading
import time

import pytest
from fstree import old_layout_run, tree

from photosort import appdata, journal, migrate
from photosort.cache import AnalysisCache
from photosort.journal import forget_runs, import_run, load_all_runs, load_runs
from photosort.migrate import bring_in
from photosort.undo import undo_run


def _photos(root, *names):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_bytes(f"bytes of {name}".encode())
    return root


def _old_cache(photosort, rows):
    """An old <root>/.photosort/cache.db holding rows."""
    photosort.mkdir(parents=True, exist_ok=True)
    path = photosort / "cache.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE entries (
            kind TEXT, name TEXT, size INT, mtime_ns INT, data BLOB,
            PRIMARY KEY (kind, name, size, mtime_ns))""")
        conn.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?)", rows)
    conn.close()
    return path


def _central_rows():
    with sqlite3.connect(appdata.data_dir() / "cache.db") as conn:
        rows = conn.execute("SELECT kind, name, data FROM entries").fetchall()
    conn.close()
    return rows


def test_bring_in_moves_old_runs_and_cleans_up(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg", "b.jpg")
    before = tree(root)
    old_layout_run(root, [("a.jpg", "2025/01/a.jpg"), ("b.jpg", "2025/01/b.jpg")])
    assert not (root / "a.jpg").exists()

    assert bring_in(root).imported == 1
    (run,) = load_runs(root)
    assert run.run_id == "20250101-120000-date"
    assert run.root == root
    assert not (root / ".photosort").exists()

    result = undo_run(root, run.run_id)
    assert result.restored == 2 and not result.failed
    assert tree(root) == before  # byte for byte, the folders it made gone too
    assert bring_in(root).imported == 0  # nothing left to do


def test_bring_in_with_nothing_to_do(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg")
    assert bring_in(root).imported == 0
    assert not appdata.runs_dir().exists()  # nothing written
    assert tree(root) == {"a.jpg": b"bytes of a.jpg"}


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores folder permissions")
def test_bring_in_read_only_keeps_old_files_and_does_not_reimport(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg")
    runs = old_layout_run(root, [("a.jpg", "x/a.jpg")])
    os.chmod(runs, 0o555)
    os.chmod(runs.parent, 0o555)
    try:
        assert bring_in(root).imported == 1  # copied, so History shows it
        assert sorted(p.name for p in runs.iterdir()) == [
            "20250101-120000-date.json", "20250101-120000-date.jsonl"]
        assert bring_in(root).imported == 0  # and never copied twice
        assert [r.run_id for r in load_all_runs()] == ["20250101-120000-date"]
    finally:
        os.chmod(runs.parent, 0o755)
        os.chmod(runs, 0o755)
    assert bring_in(root).imported == 0  # writable again: the old copy goes now
    assert not (root / ".photosort").exists()
    assert len(load_all_runs()) == 1


def test_bring_in_skips_forgotten(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg")
    runs = old_layout_run(root, [("a.jpg", "x/a.jpg")])
    assert import_run(runs, "20250101-120000-date", root) == "20250101-120000-date"
    forget_runs(["20250101-120000-date"])
    assert bring_in(root).imported == 0
    assert load_all_runs() == []
    assert not (root / ".photosort").exists()  # forgotten: its old copy can go


def test_bring_in_nested_and_parent_folders(tmp_path):
    top = _photos(tmp_path / "top", "p.jpg")
    root = _photos(top / "root", "r.jpg")
    deep = _photos(root / "sub" / "deep", "d.jpg")
    sibling = _photos(top / "sibling", "s.jpg")
    old_layout_run(top, [("p.jpg", "y/p.jpg")], run_id="r-top")
    old_layout_run(root, [("r.jpg", "y/r.jpg")], run_id="r-root")
    old_layout_run(deep, [("d.jpg", "y/d.jpg")], run_id="r-deep")
    old_layout_run(sibling, [("s.jpg", "y/s.jpg")], run_id="r-sibling")

    assert bring_in(root).imported == 3
    assert [r.run_id for r in load_runs(top)] == ["r-top"]
    assert [r.run_id for r in load_runs(root)] == ["r-root"]
    assert [r.run_id for r in load_runs(deep)] == ["r-deep"]
    assert load_runs(sibling) == []  # neither around nor inside root
    assert (sibling / ".photosort" / "runs" / "r-sibling.json").is_file()
    for folder in (top, root, deep):
        assert not (folder / ".photosort").exists()


def test_bring_in_does_not_walk_into_photosort_folders(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg")
    hidden = _photos(root / ".photosort" / "inner", "h.jpg")
    old_layout_run(hidden, [("h.jpg", "y/h.jpg")], run_id="r-hidden")
    assert bring_in(root).imported == 0
    assert load_all_runs() == []
    assert (hidden / ".photosort" / "runs" / "r-hidden.json").is_file()


def test_bring_in_keeps_photosort_with_unknown_files(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg")
    runs = old_layout_run(root, [("a.jpg", "x/a.jpg")])
    (root / ".photosort" / "notes.txt").write_text("mine")
    (runs / "stray.txt").write_text("also mine")
    assert bring_in(root).imported == 1
    assert sorted(p.name for p in (root / ".photosort").iterdir()) == ["notes.txt", "runs"]
    assert sorted(p.name for p in runs.iterdir()) == ["stray.txt"]


def test_bring_in_keeps_an_unreadable_old_run(tmp_path, caplog):
    root = _photos(tmp_path / "pics", "a.jpg")
    runs = old_layout_run(root, [("a.jpg", "x/a.jpg")])
    (runs / "broken.json").write_text("{not json")
    (runs / "broken.jsonl").write_text("")
    with caplog.at_level(logging.WARNING, logger="photosort"):
        assert bring_in(root).imported == 1
    assert sorted(p.name for p in runs.iterdir()) == ["broken.json", "broken.jsonl"]
    assert "broken" in caplog.text


def test_bring_in_merges_the_old_cache_then_removes_it(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg")
    old = _old_cache(root / ".photosort", [("photo-v1", "a.jpg", 14, 1, b"old data")])
    (root / ".photosort" / "cache.db-shm").write_bytes(b"")  # a side file
    assert bring_in(root).imported == 0
    assert ("photo-v1", "a.jpg", b"old data") in _central_rows()
    assert not old.exists()
    assert not (root / ".photosort").exists()


def test_bring_in_keeps_an_old_cache_it_cannot_merge(tmp_path, monkeypatch, caplog):
    root = _photos(tmp_path / "pics", "a.jpg")
    old = _old_cache(root / ".photosort", [("photo-v1", "a.jpg", 14, 1, b"old data")])
    before = old.read_bytes()
    monkeypatch.setattr(migrate, "merge_cache", lambda path: False)
    with caplog.at_level(logging.WARNING, logger="photosort"):
        assert bring_in(root).imported == 0
    assert old.read_bytes() == before
    assert "could not be read" in caplog.text


def test_bring_in_never_touches_the_central_store(tmp_path, monkeypatch):
    """Even a data folder that sits where an old .photosort would (inside the
    folder picked) is never merged into itself or emptied."""
    root = _photos(tmp_path / "pics", "a.jpg")
    monkeypatch.setattr(appdata, "data_dir", lambda: root / ".photosort")
    with AnalysisCache() as cache:
        cache.put("photo-v1", root / "a.jpg", b"central")
    j = journal.RunJournal.create(root, "date", False, 0)
    j.finish("done")
    merged = []
    monkeypatch.setattr(migrate, "merge_cache", lambda path: merged.append(path) or True)
    assert bring_in(root).imported == 0
    assert merged == []
    assert (root / ".photosort" / "cache.db").is_file()
    assert [r.run_id for r in load_runs(root)] == [j.run_id]


def test_bring_in_stops_when_cancelled(tmp_path):
    root = _photos(tmp_path / "pics", "a.jpg")
    old_layout_run(root, [("a.jpg", "x/a.jpg")])
    cancel = threading.Event()
    cancel.set()
    assert bring_in(root, cancel=cancel).imported == 0
    assert (root / ".photosort" / "runs").is_dir()


def test_bring_in_at_the_same_time_imports_once(tmp_path, monkeypatch):
    root = _photos(tmp_path / "pics", "a.jpg", "b.jpg")
    old_layout_run(root, [("a.jpg", "x/a.jpg")], run_id="r1")
    old_layout_run(root, [("b.jpg", "x/b.jpg")], run_id="r2",
                   started="2025-01-02T12:00:00+05:30")
    real = journal.forgotten_keys

    def slow():  # widen the gap between "not imported yet" and the copy
        time.sleep(0.05)
        return real()

    monkeypatch.setattr(journal, "forgotten_keys", slow)
    counts = []
    threads = [threading.Thread(target=lambda: counts.append(bring_in(root).imported))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(counts) == 2
    assert sorted(r.run_id for r in load_all_runs()) == ["r1", "r2"]
    assert not (root / ".photosort").exists()


# ----- a sorted folder copied with its old records ---------------------------

def _copied_pair(tmp_path):
    """drive/Photos with an old run, then copied whole (old records too) to
    drive/Photos backup: the same run key in both."""
    import shutil
    drive = tmp_path / "drive"
    photos = _photos(drive / "Photos", "a.jpg", "b.jpg")
    before = tree(photos)
    old_layout_run(photos, [("a.jpg", "2025/a.jpg"), ("b.jpg", "2025/b.jpg")])
    backup = drive / "Photos backup"
    shutil.copytree(photos, backup, symlinks=True)
    return drive, photos, backup, before


def test_bring_in_keeps_a_copied_folders_records_as_its_own_run(tmp_path):
    drive, photos, backup, before = _copied_pair(tmp_path)
    assert bring_in(drive).imported == 2
    runs = load_all_runs()
    assert sorted(r.root for r in runs) == [photos, backup]
    assert sorted(r.run_id for r in runs) == ["20250101-120000-date",
                                              "20250101-120000-date-imported-1"]
    assert not (photos / ".photosort").exists()
    assert not (backup / ".photosort").exists()
    for folder in (photos, backup):
        (run,) = load_runs(folder)
        result = undo_run(folder, run.run_id)
        assert result.restored == 2 and not result.failed
        assert tree(folder) == before  # each undoes its own folder, byte for byte
    assert bring_in(drive).imported == 0


def test_forgetting_the_original_never_drops_a_copys_records(tmp_path):
    drive, photos, backup, before = _copied_pair(tmp_path)
    assert bring_in(photos).imported == 1
    (run,) = load_runs(photos)
    forget_runs([run.run_id])
    assert bring_in(drive).imported == 1  # the copy is its own run still
    (copy,) = load_all_runs()
    assert copy.root == backup
    assert not (backup / ".photosort").exists()
    undo_run(backup, copy.run_id)
    assert tree(backup) == before


def test_an_old_forgotten_entry_without_a_root_matches_any_root(tmp_path):
    drive, photos, backup, _ = _copied_pair(tmp_path)
    runs = appdata.runs_dir()
    runs.mkdir(parents=True)
    (runs / "forgotten.json").write_text(
        '[{"run_id": "20250101-120000-date", "started": "2025-01-01T12:00:00+05:30"}]')
    assert bring_in(drive).imported == 0
    assert load_all_runs() == []
    assert not (photos / ".photosort").exists()
    assert not (backup / ".photosort").exists()
