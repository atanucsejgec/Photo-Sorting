import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from photosort import appdata, journal
from photosort.journal import (
    JournalMove, RunJournal, forget_runs, forgotten_keys, import_run, load_all_runs,
    load_dirs, load_moves, load_runs, mark_undone, relocate_root,
)

# Taken at import, before conftest's _app_data points data_dir at a tmp folder.
_REAL_DATA_DIR = appdata.data_dir


def _runs_dir(root=None):
    """The central runs folder (root is ignored: kept so old tests read alike)."""
    return appdata.runs_dir()


def _meta(run_id):
    return json.loads((appdata.runs_dir() / f"{run_id}.json").read_text())


def _write_meta(run_id, **changes):
    meta = _meta(run_id)
    meta.update(changes)
    (appdata.runs_dir() / f"{run_id}.json").write_text(json.dumps(meta))


def _old_run(runs: Path, run_id: str, started: str, lines: bytes = b"") -> None:
    """A run in the old <root>/.photosort/runs layout: no "root" in its meta."""
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{run_id}.jsonl").write_bytes(lines)
    (runs / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "mode": "date", "started": started,
        "finished": started, "status": "done", "include_subfolders": False,
        "planned": 1, "moved": 1, "undone": False,
    }))


def test_data_dir_is_user_data_dir_and_not_created(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata.platformdirs, "user_data_dir",
                        lambda app: str(tmp_path / app))
    assert _REAL_DATA_DIR() == tmp_path / "photo-sorter"
    assert not (tmp_path / "photo-sorter").exists()


def test_tests_never_use_the_real_data_dir():
    assert appdata.data_dir() != _REAL_DATA_DIR()
    assert appdata.runs_dir() == appdata.data_dir() / "runs"


def test_old_runs_dir_kept_for_migration():
    assert journal.RUNS_DIR == Path(".photosort") / "runs"


def test_records_live_in_the_central_folder(tmp_path):
    pics = tmp_path / "pics"
    pics.mkdir()
    j = RunJournal.create(pics, "date", False, 1)
    j.finish("done")
    assert not (pics / ".photosort").exists()
    assert list(pics.iterdir()) == []
    assert (appdata.runs_dir() / f"{j.run_id}.jsonl").is_file()
    assert _meta(j.run_id)["root"] == str(tmp_path / "pics")


def test_root_is_stored_resolved(tmp_path, monkeypatch):
    """Every root enters the store through norm_root: absolute, symlinks
    resolved, as Home resolves the folder picked."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.chdir(tmp_path)
    j = RunJournal.create(Path("link"), "date", False, 0)
    j.finish("done")
    assert _meta(j.run_id)["root"] == os.path.realpath(real)
    (run,) = load_runs(real)
    assert run.root == real.resolve()


def test_load_runs_filters_by_root(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ja = RunJournal.create(a, "date", False, 0)
    ja.finish("done")
    jb = RunJournal.create(b, "date", False, 0)
    jb.finish("done")
    _write_meta(jb.run_id, started="2030-01-01T00:00:00+00:00")
    assert [r.run_id for r in load_runs(a)] == [ja.run_id]
    assert [r.run_id for r in load_runs(b)] == [jb.run_id]
    assert [r.run_id for r in load_runs(tmp_path)] == []
    runs = load_all_runs()
    assert [r.run_id for r in runs] == [jb.run_id, ja.run_id]
    assert [r.root for r in runs] == [b, a]


def test_load_runs_skips_records_without_root(tmp_path):
    j = RunJournal.create(tmp_path, "date", False, 0)
    j.finish("done")
    meta = _meta(j.run_id)
    del meta["root"]
    (appdata.runs_dir() / f"{j.run_id}.json").write_text(json.dumps(meta))
    assert load_runs(tmp_path) == []
    assert load_all_runs() == []


def test_load_all_runs_with_no_folder_yet():
    assert not appdata.runs_dir().exists()
    assert load_all_runs() == []
    assert forgotten_keys() == set()


def test_forget_runs_removes_and_remembers(tmp_path):
    keep = RunJournal.create(tmp_path, "date", False, 0)
    keep.finish("done")
    gone = RunJournal.create(tmp_path, "date", False, 0)
    gone.finish("done")
    started = _meta(gone.run_id)["started"]
    forget_runs([gone.run_id])
    assert not (appdata.runs_dir() / f"{gone.run_id}.json").exists()
    assert not (appdata.runs_dir() / f"{gone.run_id}.jsonl").exists()
    assert (gone.run_id, started, str(tmp_path)) in forgotten_keys()
    assert [r.run_id for r in load_runs(tmp_path)] == [keep.run_id]
    # A second forget adds to the list, never replaces it.
    forget_runs([keep.run_id])
    assert len(forgotten_keys()) == 2
    assert not list(appdata.runs_dir().glob(".*.tmp"))


def test_relocate_root_moves_prefix():
    ids = {}
    for root in ("/x/a", "/x/a/b", "/x/ab"):
        j = RunJournal.create(Path(root), "date", False, 0)
        j.finish("done")
        ids[root] = j.run_id
    assert relocate_root(Path("/x/a"), Path("/y")) == 2
    assert _meta(ids["/x/a"])["root"] == "/y"
    assert _meta(ids["/x/a/b"])["root"] == "/y/b"
    assert _meta(ids["/x/ab"])["root"] == "/x/ab"
    assert [r.run_id for r in load_runs(Path("/y/b"))] == [ids["/x/a/b"]]
    assert relocate_root(Path("/nowhere"), Path("/y")) == 0


def _three_under(root: str) -> list[str]:
    ids = []
    for sub in ("", "/b", "/c"):
        j = RunJournal.create(Path(root + sub), "date", False, 0)
        j.finish("done")
        ids.append(j.run_id)
    return ids


def _failing_writes(monkeypatch, bad: set[str]):
    """journal._write_meta raises for the records of the run ids in bad."""
    real = journal._write_meta

    def flaky(path, meta):
        if path.stem in bad:
            raise OSError("disk full")
        return real(path, meta)

    monkeypatch.setattr(journal, "_write_meta", flaky)


def _pending():
    """The pending pairs, without the time each was made."""
    path = appdata.runs_dir() / "relocating.json"
    pairs = json.loads(path.read_text()) if path.exists() else []
    return [{"old": p["old"], "new": p["new"]} for p in pairs]


def test_a_pending_relocation_never_moves_a_run_recorded_after_it(monkeypatch):
    """The drive is back at its old path and sorted again before the
    pending pair is finished: that newer run stays where it was recorded."""
    ids = _three_under("/x/a")
    _failing_writes(monkeypatch, {ids[1]})
    with pytest.raises(OSError):
        relocate_root(Path("/x/a"), Path("/y"))
    (pair,) = json.loads((appdata.runs_dir() / "relocating.json").read_text())
    assert datetime.fromisoformat(pair["at"]) >= datetime.fromisoformat(
        _meta(ids[2])["started"])
    monkeypatch.undo()
    later = RunJournal.create(Path("/x/a/b"), "date", False, 0)
    later.finish("done")
    journal.finish_relocations()
    assert [_meta(i)["root"] for i in ids] == ["/y", "/y/b", "/y/c"]
    assert _meta(later.run_id)["root"] == "/x/a/b"  # recorded after: kept
    assert _pending() == []


def test_relocate_root_tries_every_record_and_keeps_the_pair_pending(monkeypatch):
    ids = _three_under("/x/a")
    _failing_writes(monkeypatch, {ids[1]})
    with pytest.raises(OSError, match="disk full"):
        relocate_root(Path("/x/a"), Path("/y"))
    # the ones before and after the failure are re-pointed all the same
    assert [_meta(i)["root"] for i in ids] == ["/y", "/x/a/b", "/y/c"]
    assert _pending() == [{"old": "/x/a", "new": "/y"}]
    assert [r.run_id for r in load_all_runs()] != []  # the marker is no run

    monkeypatch.undo()
    journal.finish_relocations()
    assert [_meta(i)["root"] for i in ids] == ["/y", "/y/b", "/y/c"]
    assert _pending() == []
    journal.finish_relocations()  # nothing pending: nothing to do


def test_finish_relocations_leaves_a_failing_pair_pending(monkeypatch):
    ids = _three_under("/x/a")
    other = _three_under("/p/q")
    _failing_writes(monkeypatch, {ids[2]})
    with pytest.raises(OSError):
        relocate_root(Path("/x/a"), Path("/y"))
    with pytest.raises(OSError):
        journal.finish_relocations()
    relocate_root(Path("/p/q"), Path("/r"))  # another pair, done at once
    assert _pending() == [{"old": "/x/a", "new": "/y"}]
    assert [_meta(i)["root"] for i in other] == ["/r", "/r/b", "/r/c"]
    monkeypatch.undo()
    journal.finish_relocations()
    assert _pending() == []
    assert _meta(ids[2])["root"] == "/y/c"


def test_relocate_root_writes_nothing_when_the_marker_cannot_be(monkeypatch):
    ids = _three_under("/x/a")
    real = journal._write_meta

    def no_marker(path, meta):
        if path.name == "relocating.json":
            raise OSError("read-only")
        return real(path, meta)

    monkeypatch.setattr(journal, "_write_meta", no_marker)
    with pytest.raises(OSError, match="read-only"):
        relocate_root(Path("/x/a"), Path("/y"))
    assert [_meta(i)["root"] for i in ids] == ["/x/a", "/x/a/b", "/x/a/c"]


def test_finish_relocations_refuses_an_unreadable_marker():
    appdata.runs_dir().mkdir(parents=True, exist_ok=True)
    (appdata.runs_dir() / "relocating.json").write_text("{broken")
    with pytest.raises(ValueError):
        journal.finish_relocations()


def test_import_run_copies_and_verifies(tmp_path):
    old = tmp_path / "pics" / ".photosort" / "runs"
    lines = b'{"op": "move", "i": 0, "src": "a.jpg", "dst": "x/a.jpg"}\n{"op": "done", "i": 0}\n'
    _old_run(old, "20250101-120000-date", "2025-01-01T12:00:00+05:30", lines)
    new_id = import_run(old, "20250101-120000-date", tmp_path / "pics")
    assert new_id == "20250101-120000-date"
    (run,) = load_runs(tmp_path / "pics")
    assert run.run_id == new_id
    assert run.root == tmp_path / "pics"
    assert load_moves(tmp_path / "pics", new_id) == [JournalMove("a.jpg", "x/a.jpg", True)]
    # import only copies: deleting the old files is the caller's job.
    assert (old / "20250101-120000-date.json").is_file()
    assert (old / "20250101-120000-date.jsonl").is_file()


def test_import_run_skips_forgotten_and_duplicates(tmp_path):
    old = tmp_path / "pics" / ".photosort" / "runs"
    _old_run(old, "r1", "2025-01-01T12:00:00+00:00")
    _old_run(old, "r2", "2025-01-02T12:00:00+00:00")
    assert import_run(old, "r1", tmp_path / "pics") == "r1"
    assert import_run(old, "r1", tmp_path / "pics") is None  # already imported
    assert import_run(old, "r2", tmp_path / "pics") == "r2"
    forget_runs(["r2"])
    assert import_run(old, "r2", tmp_path / "pics") is None  # forgotten
    assert [r.run_id for r in load_all_runs()] == ["r1"]


def test_import_run_suffixes_clashing_id(tmp_path):
    a = tmp_path / "a" / ".photosort" / "runs"
    b = tmp_path / "b" / ".photosort" / "runs"
    _old_run(a, "r", "2025-01-01T12:00:00+00:00")
    _old_run(b, "r", "2025-03-01T12:00:00+00:00")  # same id, another run
    assert import_run(a, "r", tmp_path / "a") == "r"
    assert import_run(b, "r", tmp_path / "b") == "r-imported-1"
    (run,) = load_runs(tmp_path / "b")
    assert run.run_id == "r-imported-1"
    # Imported under a new id, it is still recognised as already imported.
    assert import_run(b, "r", tmp_path / "b") is None
    _old_run(tmp_path / "c", "r", "2025-04-01T12:00:00+00:00")
    assert import_run(tmp_path / "c", "r", tmp_path / "c") == "r-imported-2"


def test_import_run_missing_log_raises_and_leaves_nothing(tmp_path):
    old = tmp_path / "pics" / ".photosort" / "runs"
    _old_run(old, "r", "2025-01-01T12:00:00+00:00")
    (old / "r.jsonl").unlink()
    with pytest.raises(OSError):
        import_run(old, "r", tmp_path / "pics")
    assert load_all_runs() == []
    assert not (appdata.runs_dir() / "r.json").exists()


@pytest.mark.parametrize("which", ["json", "jsonl"])
def test_import_run_copy_mismatch_removes_partial_copy_and_raises(
        tmp_path, monkeypatch, which):
    old = tmp_path / "pics" / ".photosort" / "runs"
    lines = b'{"op": "move", "i": 0, "src": "a.jpg", "dst": "x/a.jpg"}\n'
    _old_run(old, "r", "2025-01-01T12:00:00+00:00", lines)
    central = appdata.runs_dir() / f"r.{which}"
    real_read_bytes = Path.read_bytes

    def read_back_differs(self):
        data = real_read_bytes(self)
        if self != central:
            return data
        return b"{}" if which == "json" else data + b"\n"  # the disk lied

    monkeypatch.setattr(Path, "read_bytes", read_back_differs)
    with pytest.raises(ValueError, match="does not match"):
        import_run(old, "r", tmp_path / "pics")
    monkeypatch.undo()
    assert not (appdata.runs_dir() / "r.json").exists()
    assert not (appdata.runs_dir() / "r.jsonl").exists()
    assert load_all_runs() == []
    # The old copy is untouched, so a later try can bring it in.
    assert (old / "r.json").is_file()
    assert (old / "r.jsonl").read_bytes() == lines
    assert import_run(old, "r", tmp_path / "pics") == "r"


def test_journal_roundtrip(tmp_path):
    j = RunJournal.create(tmp_path, "date", False, 2)
    i0 = j.record_intent("a", "b")
    i1 = j.record_intent("c", "d")
    assert (i0, i1) == (0, 1)
    j.record_done(0)
    j.finish("done")

    assert load_moves(tmp_path, j.run_id) == [
        JournalMove("a", "b", True), JournalMove("c", "d", False),
    ]
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    run = runs[0]
    assert run.run_id == j.run_id
    assert run.mode == "date"
    assert run.status == "done"
    assert run.moved == 1
    assert run.undone is False
    assert isinstance(run.started, datetime)
    assert run.started.tzinfo is not None


def test_run_id_format_and_files(tmp_path):
    j = RunJournal.create(tmp_path, "date", True, 5)
    assert j.run_id.endswith("-date")
    datetime.strptime(j.run_id[:15], "%Y%m%d-%H%M%S")
    meta = json.loads((_runs_dir(tmp_path) / f"{j.run_id}.json").read_text())
    assert meta["status"] == "running"
    assert meta["finished"] is None
    assert meta["include_subfolders"] is True
    assert meta["planned"] == 5
    assert meta["moved"] == 0
    assert meta["undone"] is False
    assert (_runs_dir(tmp_path) / f"{j.run_id}.jsonl").exists()
    j.finish("cancelled")
    meta = json.loads((_runs_dir(tmp_path) / f"{j.run_id}.json").read_text())
    assert meta["status"] == "cancelled"
    datetime.fromisoformat(meta["finished"])


def test_journal_lines_shapes(tmp_path):
    j = RunJournal.create(tmp_path, "date", False, 1)
    j.record_dir_created("x")
    j.record_intent("a.jpg", "x/a.jpg")
    j.record_done(0)
    j.record_dir_removed("sub")
    j.finish("done")
    lines = (_runs_dir(tmp_path) / f"{j.run_id}.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {"op": "mkdir", "path": "x"},
        {"op": "move", "i": 0, "src": "a.jpg", "dst": "x/a.jpg"},
        {"op": "done", "i": 0},
        {"op": "rmdir", "path": "sub"},
    ]


def test_runs_newest_first(tmp_path):
    first = RunJournal.create(tmp_path, "date", False, 0)
    first.finish("done")
    second = RunJournal.create(tmp_path, "date", False, 0)
    second.finish("done")
    assert first.run_id != second.run_id
    assert [r.run_id for r in load_runs(tmp_path)] == [second.run_id, first.run_id]


def test_run_id_gets_suffix_when_taken(tmp_path):
    journals = [RunJournal.create(tmp_path, "date", False, 0) for _ in range(3)]
    for j in journals:
        j.finish("done")
    ids = [j.run_id for j in journals]
    assert len(set(ids)) == 3
    # Created within the same second, they share a base and get -2, -3.
    if ids[0][:15] == ids[2][:15]:
        assert ids[1:] == [ids[0] + "-2", ids[0] + "-3"]


def test_load_runs_empty_and_skips_corrupt(tmp_path):
    assert load_runs(tmp_path) == []
    j = RunJournal.create(tmp_path, "date", False, 0)
    j.finish("done")
    (_runs_dir(tmp_path) / "broken.json").write_text("{not json")
    (_runs_dir(tmp_path) / "partial.json").write_text('{"run_id": "partial"}')
    assert [r.run_id for r in load_runs(tmp_path)] == [j.run_id]


def test_load_moves_ignores_truncated_last_line(tmp_path):
    j = RunJournal.create(tmp_path, "date", False, 2)
    j.record_intent("a", "b")
    j.record_done(0)
    j.record_intent("c", "d")
    j.finish("failed")
    path = _runs_dir(tmp_path) / f"{j.run_id}.jsonl"
    with open(path, "ab") as f:
        f.write(b'{"op":"done","i"')  # crash mid-write
    assert load_moves(tmp_path, j.run_id) == [
        JournalMove("a", "b", True), JournalMove("c", "d", False),
    ]


def test_load_dirs(tmp_path):
    j = RunJournal.create(tmp_path, "date", False, 0)
    j.record_dir_created("2025")
    j.record_dir_created("2025/02-February")
    j.record_dir_removed("old")
    j.finish("done")
    assert load_dirs(tmp_path, j.run_id) == (["2025", "2025/02-February"], ["old"])


def test_mark_undone(tmp_path):
    j = RunJournal.create(tmp_path, "date", False, 0)
    j.finish("done")
    mark_undone(tmp_path, j.run_id)
    (run,) = load_runs(tmp_path)
    assert run.undone is True
    assert run.status == "done"


def test_undecodable_names_roundtrip(tmp_path):
    # Linux names that are not valid UTF-8 arrive as lone surrogates.
    name = "photo-\udcff.jpg"
    j = RunJournal.create(tmp_path, "date", False, 1)
    j.record_intent(name, "x/" + name)
    j.record_done(0)
    j.finish("done")
    assert load_moves(tmp_path, j.run_id) == [JournalMove(name, "x/" + name, True)]


def test_forgetting_a_suffixed_import_keeps_it_out(tmp_path):
    a = tmp_path / "a" / ".photosort" / "runs"
    b = tmp_path / "b" / ".photosort" / "runs"
    _old_run(a, "r", "2025-01-01T12:00:00+00:00")
    _old_run(b, "r", "2025-03-01T12:00:00+00:00")
    import_run(a, "r", tmp_path / "a")
    assert import_run(b, "r", tmp_path / "b") == "r-imported-1"
    forget_runs(["r-imported-1"])
    assert import_run(b, "r", tmp_path / "b") is None


def test_forget_runs_sets_a_corrupt_list_aside(tmp_path, caplog):
    """A forgotten list that can't be read is moved aside, never written over:
    the keys in it may still be recovered by hand."""
    runs = appdata.runs_dir()
    runs.mkdir(parents=True)
    (runs / "forgotten.json").write_bytes(b"[{broken")
    (runs / "forgotten.json.broken-1").write_bytes(b"an earlier one")
    j = RunJournal.create(tmp_path, "date", False, 0)
    j.finish("done")
    started = _meta(j.run_id)["started"]
    with caplog.at_level("WARNING", logger="photosort"):
        forget_runs([j.run_id])
    assert (runs / "forgotten.json.broken-1").read_bytes() == b"an earlier one"
    assert (runs / "forgotten.json.broken-2").read_bytes() == b"[{broken"
    assert forgotten_keys() == {(j.run_id, started, str(tmp_path))}
    assert "forgotten.json.broken-2" in caplog.text


# ----- paths outside the root (Preview moves anywhere) ------------------------

def test_abs_path_relative_and_absolute(tmp_path):
    assert journal.abs_path(tmp_path, "a/b.jpg") == tmp_path / "a" / "b.jpg"
    assert journal.abs_path(tmp_path, "/mnt/usb/b.jpg") == Path("/mnt/usb/b.jpg")
    assert journal.abs_path(Path("/x"), "") == Path("/x")
    assert isinstance(journal.abs_path(tmp_path, "/y"), Path)


def test_also_roots_kept_in_the_meta(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "link").symlink_to(other, target_is_directory=True)
    j = RunJournal.create(tmp_path / "root", "preview", False, 0)
    (run,) = load_all_runs()
    assert run.also_roots == ()
    assert "also_roots" not in _meta(j.run_id)  # an inside-only run looks as before
    j.add_also_root(tmp_path / "link")
    j.add_also_root(other)  # the same folder: listed once
    assert _meta(j.run_id)["also_roots"] == [str(other)]
    assert _meta(j.run_id)["status"] == "running"
    j.add_also_root(tmp_path / "second")
    j.finish("done")
    meta = _meta(j.run_id)
    assert meta["also_roots"] == [str(other), str(tmp_path / "second")]
    assert meta["status"] == "done"
    (run,) = load_all_runs()
    assert run.also_roots == (other, tmp_path / "second")


def test_old_meta_without_also_roots_reads_as_none(tmp_path):
    j = RunJournal.create(tmp_path, "date", False, 0)
    j.finish("done")
    meta = _meta(j.run_id)
    assert "also_roots" not in meta
    (run,) = load_runs(tmp_path)
    assert run.also_roots == ()


def test_mounts_recorded_for_every_new_run(tmp_path, monkeypatch):
    media = tmp_path / "media"
    monkeypatch.setattr(os.path, "ismount",
                        lambda p: os.path.abspath(p) in {"/", str(media)})
    assert journal.mount_of(media / "a" / "b") == str(media)
    assert journal.mount_of(media) == str(media)
    assert journal.mount_of(tmp_path) == "/"
    j = RunJournal.create(media / "pics", "date", False, 0)
    assert _meta(j.run_id)["mounts"] == {str(media / "pics"): str(media)}
    j.add_also_root(tmp_path / "other")
    assert _meta(j.run_id)["mounts"] == {str(media / "pics"): str(media),
                                         str(tmp_path / "other"): "/"}
    j.finish("done")
    (run,) = load_all_runs()
    assert run.mounts == ((media / "pics", media), (tmp_path / "other", Path("/")))


def test_relocation_records_the_new_mount(tmp_path, monkeypatch):
    media = tmp_path / "media"
    monkeypatch.setattr(os.path, "ismount",
                        lambda p: os.path.abspath(p) in {"/", str(media)})
    j = RunJournal.create(tmp_path / "pics", "date", False, 0)
    j.finish("done")
    (media / "pics").mkdir(parents=True)
    relocate_root(tmp_path / "pics", media / "pics")
    assert _meta(j.run_id)["mounts"] == {str(media / "pics"): str(media)}
