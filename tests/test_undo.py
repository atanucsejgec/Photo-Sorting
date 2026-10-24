import os

import pytest

from photosort.executor import execute
from photosort.journal import RunJournal, load_runs
from photosort.modes.date_mode import prepare_date_plan
from photosort.plan import Move, Plan
from photosort.undo import UndoBlocked, UndoResult, blocking_runs, undo_run


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _run(root, moves) -> str:
    plan = Plan("date", [Move(s, d) for s, d in moves], [])
    journal = RunJournal.create(root, "date", False, len(moves))
    execute(root, plan, journal)
    return journal.run_id


def _crashed_journal(root, entries) -> str:
    """Write a journal the way a run that died mid-way leaves it.

    entries: (src, dst, done) per recorded move.
    """
    j = RunJournal.create(root, "date", False, len(entries))
    for src, dst, done in entries:
        i = j.record_intent(src, dst)
        if done:
            j.record_done(i)
    j._log.close()  # the process died: finish() never ran
    return j.run_id


def _undone(root, run_id) -> bool:
    return next(r for r in load_runs(root) if r.run_id == run_id).undone


def test_undo_full_run(tmp_path, make_jpeg):
    originals = {}
    for name, dt in [("a.jpg", "2025:02:20 10:00:00"), ("b.jpg", "2025:02:20 11:00:00"),
                     ("c.jpg", "2025:02:20 12:00:00"), ("d.jpg", "2025:03:01 09:00:00")]:
        path = make_jpeg(tmp_path / "sub" / name, exif_dt=dt)
        originals[f"sub/{name}"] = path.read_bytes()
    plan = prepare_date_plan(tmp_path, include_subfolders=True)
    journal = RunJournal.create(tmp_path, "date", True, len(plan.moves))
    execute(tmp_path, plan, journal)
    assert not (tmp_path / "sub").exists()
    assert (tmp_path / "2025/02-February/20-02-2025/a.jpg").is_file()

    calls = []
    result = undo_run(tmp_path, journal.run_id, progress=lambda i, t: calls.append((i, t)))

    assert result == UndoResult(restored=4, missing=[], renamed=[])
    for rel, data in originals.items():
        assert (tmp_path / rel).read_bytes() == data
    assert sorted(os.listdir(tmp_path / "sub")) == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
    assert not (tmp_path / "2025").exists()
    assert sorted(os.listdir(tmp_path)) == [".photosort", "sub"]
    assert calls == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert _undone(tmp_path, journal.run_id)


def test_undo_after_crash(tmp_path):
    run_id = _crashed_journal(tmp_path, [
        ("a.jpg", "x/a.jpg", True), ("b.jpg", "x/b.jpg", False), ("c.jpg", "x/c.jpg", False),
    ])
    _write(tmp_path / "x/a.jpg", b"a")  # moved and marked done
    _write(tmp_path / "x/b.jpg", b"b")  # moved, crashed before the done mark
    _write(tmp_path / "c.jpg", b"c")    # crashed before the move
    (run,) = load_runs(tmp_path)
    assert run.status == "running"

    result = undo_run(tmp_path, run_id)

    assert result.restored == 2
    assert result.missing == []
    assert result.renamed == []
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        assert (tmp_path / name).read_bytes() == name[0].encode()
    assert not (tmp_path / "x").exists() or os.listdir(tmp_path / "x") == []
    assert _undone(tmp_path, run_id)


def test_undo_trusts_disk_over_done_mark(tmp_path):
    # Power loss: the done mark reached the disk, the rename did not.
    run_id = _crashed_journal(tmp_path, [("a.jpg", "x/a.jpg", True)])
    _write(tmp_path / "a.jpg", b"a")
    result = undo_run(tmp_path, run_id)
    assert result == UndoResult(restored=0, missing=[], renamed=[])
    assert (tmp_path / "a.jpg").read_bytes() == b"a"


def test_undo_keeps_both_copies_of_half_finished_cross_drive_move(tmp_path):
    # The verified copy reached dst but removing the source failed.
    run_id = _crashed_journal(tmp_path, [("a.jpg", "x/a.jpg", False)])
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "x/a.jpg", b"a")
    result = undo_run(tmp_path, run_id)
    assert result == UndoResult(restored=1, missing=[], renamed=[("a.jpg", "a (1).jpg")])
    assert (tmp_path / "a.jpg").read_bytes() == b"a"
    assert (tmp_path / "a (1).jpg").read_bytes() == b"a"


def test_undo_after_manual_changes(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    run_id = _run(tmp_path, [("a.jpg", "d/a.jpg"), ("b.jpg", "d/b.jpg")])
    (tmp_path / "d/a.jpg").unlink()
    _write(tmp_path / "b.jpg", b"hand-made")

    result = undo_run(tmp_path, run_id)

    assert result.missing == ["a.jpg"]
    assert result.renamed == [("b.jpg", "b (1).jpg")]
    assert result.restored == 1
    assert (tmp_path / "b.jpg").read_bytes() == b"hand-made"
    assert (tmp_path / "b (1).jpg").read_bytes() == b"b"
    assert not (tmp_path / "a.jpg").exists()
    assert not (tmp_path / "d").exists()


def test_undo_renames_around_several_clashes_in_one_folder(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    run_id = _run(tmp_path, [("a.jpg", "d/a.jpg"), ("b.jpg", "d/b.jpg")])
    _write(tmp_path / "a.jpg", b"new a")
    _write(tmp_path / "a (1).jpg", b"new a1")
    _write(tmp_path / "b.jpg", b"new b")
    result = undo_run(tmp_path, run_id)
    assert sorted(result.renamed) == [("a.jpg", "a (2).jpg"), ("b.jpg", "b (1).jpg")]
    assert (tmp_path / "a (2).jpg").read_bytes() == b"a"
    assert (tmp_path / "b (1).jpg").read_bytes() == b"b"
    assert (tmp_path / "a.jpg").read_bytes() == b"new a"
    assert (tmp_path / "a (1).jpg").read_bytes() == b"new a1"
    assert (tmp_path / "b.jpg").read_bytes() == b"new b"


def test_undo_recreates_removed_dirs_and_keeps_foreign_files(tmp_path):
    _write(tmp_path / "deep/er/a.jpg", b"a")
    run_id = _run(tmp_path, [("deep/er/a.jpg", "x/y/a.jpg")])
    assert not (tmp_path / "deep").exists()
    _write(tmp_path / "x/notes.txt", b"mine")  # owner added a file to a run folder

    undo_run(tmp_path, run_id)

    assert (tmp_path / "deep/er/a.jpg").read_bytes() == b"a"
    assert not (tmp_path / "x/y").exists()
    assert (tmp_path / "x/notes.txt").read_bytes() == b"mine"
    assert (tmp_path / ".photosort").is_dir()


def test_undo_recreates_removed_empty_dir_without_files(tmp_path):
    # A folder removed by the run that no restored file goes back into.
    _write(tmp_path / "sub/a.jpg", b"a")
    run_id = _run(tmp_path, [("sub/a.jpg", "x/a.jpg")])
    (tmp_path / "x/a.jpg").unlink()
    result = undo_run(tmp_path, run_id)
    assert result.missing == ["sub/a.jpg"]
    assert (tmp_path / "sub").is_dir()
    assert not (tmp_path / "x").exists()


def test_undo_blocked_by_newer_run(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    run1 = _run(tmp_path, [("a.jpg", "d/a.jpg")])
    run2 = _run(tmp_path, [("d/a.jpg", "e/a.jpg")])
    assert blocking_runs(tmp_path, run1) == [run2]
    assert blocking_runs(tmp_path, run2) == []

    with pytest.raises(UndoBlocked) as exc:
        undo_run(tmp_path, run1)
    assert exc.value.blocking == [run2]
    assert (tmp_path / "e/a.jpg").read_bytes() == b"a"
    assert not _undone(tmp_path, run1)

    undo_run(tmp_path, run2)
    assert blocking_runs(tmp_path, run1) == []
    result = undo_run(tmp_path, run1)
    assert result.restored == 1
    assert (tmp_path / "a.jpg").read_bytes() == b"a"
    assert not (tmp_path / "d").exists()
    assert not (tmp_path / "e").exists()


def test_blocking_compares_case_insensitively_and_ignores_older_runs(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "z.jpg", b"z")
    run1 = _run(tmp_path, [("a.jpg", "d/a.jpg")])
    run2 = _crashed_journal(tmp_path, [("D/A.JPG", "e/a.jpg", False)])
    run3 = _run(tmp_path, [("z.jpg", "d/z.jpg")])
    assert blocking_runs(tmp_path, run1) == [run2]
    assert blocking_runs(tmp_path, run3) == []


def test_undo_twice_raises(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    run_id = _run(tmp_path, [("a.jpg", "d/a.jpg")])
    undo_run(tmp_path, run_id)
    with pytest.raises(ValueError):
        undo_run(tmp_path, run_id)
    assert (tmp_path / "a.jpg").read_bytes() == b"a"


def test_undo_unknown_run_raises(tmp_path):
    with pytest.raises(ValueError):
        undo_run(tmp_path, "no-such-run")
