import json
from datetime import datetime

from photosort.journal import (
    JournalMove, RunJournal, load_dirs, load_moves, load_runs, mark_undone,
)


def _runs_dir(root):
    return root / ".photosort" / "runs"


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
