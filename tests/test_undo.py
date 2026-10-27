import errno
import os
import tempfile
from pathlib import Path

import pytest

from photosort import undo
from photosort.executor import execute
from photosort.journal import RunJournal, load_runs
from photosort.modes.date_mode import prepare_date_plan
from photosort.plan import Move, Plan
from photosort.undo import BlockingRun, UndoBlocked, UndoResult, blocking_runs, undo_run


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
    assert blocking_runs(tmp_path, run1) == [BlockingRun(run2, "")]
    assert blocking_runs(tmp_path, run2) == []

    with pytest.raises(UndoBlocked) as exc:
        undo_run(tmp_path, run1)
    assert exc.value.blocking == [BlockingRun(run2, "")]
    assert str(exc.value) == f"Undo these runs first: {run2}"
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
    assert blocking_runs(tmp_path, run1) == [BlockingRun(run2, "")]
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


def _case_sensitive(path) -> bool:
    probe = path / "case-probe"
    probe.write_bytes(b"")
    sensitive = not (path / "CASE-PROBE").exists()
    probe.unlink()
    return sensitive


def _temp_fs_case_sensitive() -> bool:
    with tempfile.TemporaryDirectory() as d:
        return _case_sensitive(Path(d))


needs_case_sensitive_fs = pytest.mark.skipif(
    not _temp_fs_case_sensitive(),
    reason="case twins cannot exist on a case-insensitive filesystem",
)


def test_undo_renames_around_case_twin_of_file(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    run_id = _run(tmp_path, [("a.jpg", "d/a.jpg")])
    _write(tmp_path / "A.JPG", b"owner's")
    result = undo_run(tmp_path, run_id)
    assert result == UndoResult(restored=1, missing=[], renamed=[("a.jpg", "a (1).jpg")])
    assert (tmp_path / "A.JPG").read_bytes() == b"owner's"
    assert (tmp_path / "a (1).jpg").read_bytes() == b"a"
    if _case_sensitive(tmp_path):
        assert not (tmp_path / "a.jpg").exists()


@needs_case_sensitive_fs
def test_undo_reuses_case_twin_folder_for_restored_file(tmp_path):
    _write(tmp_path / "sub/a.jpg", b"a")
    run_id = _run(tmp_path, [("sub/a.jpg", "x/a.jpg")])
    (tmp_path / "SUB").mkdir()  # owner made it after the run
    result = undo_run(tmp_path, run_id)
    assert result == UndoResult(restored=1, missing=[], renamed=[("sub/a.jpg", "SUB/a.jpg")])
    assert (tmp_path / "SUB/a.jpg").read_bytes() == b"a"
    assert sorted(os.listdir(tmp_path)) == [".photosort", "SUB"]


@needs_case_sensitive_fs
def test_undo_does_not_recreate_removed_dir_beside_case_twin(tmp_path):
    _write(tmp_path / "deep/er/a.jpg", b"a")
    run_id = _run(tmp_path, [("deep/er/a.jpg", "x/a.jpg")])
    (tmp_path / "x/a.jpg").unlink()
    (tmp_path / "DEEP/ER").mkdir(parents=True)
    result = undo_run(tmp_path, run_id)
    assert result.missing == ["deep/er/a.jpg"]
    assert sorted(os.listdir(tmp_path)) == [".photosort", "DEEP"]
    assert os.listdir(tmp_path / "DEEP") == ["ER"]


def test_undo_restores_exact_name_freed_earlier_in_the_undo(tmp_path):
    # Undoing m3 lists root while n.jpg (m2's dst) is still there; undoing m2
    # then moves it away, so m1 must still get its exact name back.
    run_id = _crashed_journal(tmp_path, [
        ("n.jpg", "e/n.jpg", True), ("d/n.jpg", "n.jpg", True), ("r.jpg", "e/r.jpg", True),
    ])
    _write(tmp_path / "e/n.jpg", b"root n")
    _write(tmp_path / "n.jpg", b"d n")
    _write(tmp_path / "e/r.jpg", b"r")
    result = undo_run(tmp_path, run_id)
    assert result == UndoResult(restored=3, missing=[], renamed=[])
    assert (tmp_path / "n.jpg").read_bytes() == b"root n"
    assert (tmp_path / "d/n.jpg").read_bytes() == b"d n"
    assert (tmp_path / "r.jpg").read_bytes() == b"r"


def _fail_moves_of(monkeypatch, names):
    """Make undo's move_file fail (as for a file held open on Windows) for names."""
    real = undo.move_file

    def flaky(src, dst):
        if os.path.basename(src) in names:
            raise PermissionError(errno.EACCES, "File in use", str(src))
        return real(src, dst)

    monkeypatch.setattr(undo, "move_file", flaky)


def test_undo_continues_past_a_failing_move_and_can_be_retried(tmp_path, monkeypatch):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _write(tmp_path / "sub" / name, name.encode())
    run_id = _run(tmp_path, [(f"sub/{n}", f"d/{n}") for n in ("a.jpg", "b.jpg", "c.jpg")])

    _fail_moves_of(monkeypatch, {"b.jpg"})
    result = undo_run(tmp_path, run_id)
    monkeypatch.undo()

    assert result.restored == 2
    assert result.missing == []
    assert [src for src, _ in result.failed] == ["sub/b.jpg"]
    assert "File in use" in result.failed[0][1]
    assert (tmp_path / "sub/a.jpg").read_bytes() == b"a.jpg"
    assert (tmp_path / "sub/c.jpg").read_bytes() == b"c.jpg"
    assert (tmp_path / "d/b.jpg").read_bytes() == b"b.jpg"
    assert not _undone(tmp_path, run_id)  # so Undo can be pressed again

    again = undo_run(tmp_path, run_id)
    assert again == UndoResult(restored=1, missing=[], renamed=[], failed=[])
    assert sorted(os.listdir(tmp_path / "sub")) == ["a.jpg", "b.jpg", "c.jpg"]
    assert not (tmp_path / "d").exists()
    assert _undone(tmp_path, run_id)


def test_undo_failing_folder_creation_is_recorded_not_raised(tmp_path, monkeypatch):
    _write(tmp_path / "sub/a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    run_id = _run(tmp_path, [("sub/a.jpg", "d/a.jpg"), ("b.jpg", "d/b.jpg")])
    real_mkdir = Path.mkdir

    def no_sub(self, *args, **kwargs):
        if self.name == "sub":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", no_sub)
    result = undo_run(tmp_path, run_id)
    monkeypatch.undo()
    assert result.restored == 1
    assert [src for src, _ in result.failed] == ["sub/a.jpg"]
    assert (tmp_path / "b.jpg").read_bytes() == b"b"
    assert (tmp_path / "d/a.jpg").read_bytes() == b"a"
    assert not _undone(tmp_path, run_id)


# ----- runs in nested picked folders --------------------------------------
# Each picked folder keeps its own .photosort, so a run in a folder inside
# (or around) this one can move files this run placed.


def test_undo_blocked_by_newer_run_in_a_folder_inside(tmp_path, make_jpeg):
    n = tmp_path / "n"
    for name in ("c1.jpg", "c2.jpg", "c3.jpg"):
        make_jpeg(n / "Camera" / name, "2025:02:20 10:00:00")
    plan = prepare_date_plan(n, include_subfolders=True)
    j1 = RunJournal.create(n, "date", True, len(plan.moves))
    execute(n, plan, j1)
    day_rel = "2025/02-February/20-02-2025"
    day = n / day_rel
    assert sorted(os.listdir(day)) == ["c1.jpg", "c2.jpg", "c3.jpg"]

    plan2 = prepare_date_plan(day, include_subfolders=False)  # owner picks the day folder
    j2 = RunJournal.create(day, "date", False, len(plan2.moves))
    execute(day, plan2, j2)
    assert (day / day_rel / "c1.jpg").is_file()

    assert blocking_runs(n, j1.run_id) == [BlockingRun(j2.run_id, day_rel)]
    with pytest.raises(UndoBlocked) as exc:
        undo_run(n, j1.run_id)
    assert str(exc.value) == f"Undo these runs first: {j2.run_id} (in {day_rel})"
    assert not _undone(n, j1.run_id)
    assert (day / day_rel / "c1.jpg").is_file()  # nothing was touched

    undo_run(day, j2.run_id)
    assert blocking_runs(n, j1.run_id) == []
    result = undo_run(n, j1.run_id)
    assert result == UndoResult(restored=3, missing=[], renamed=[], failed=[])
    assert sorted(os.listdir(n / "Camera")) == ["c1.jpg", "c2.jpg", "c3.jpg"]
    assert _undone(n, j1.run_id)


def test_undo_blocked_by_newer_run_in_a_folder_around(tmp_path):
    n = tmp_path / "n"
    inner = n / "X"
    _write(inner / "a.jpg", b"a")
    run_a = _run(inner, [("a.jpg", "2025/02-February/a.jpg")])
    # Then the owner sorts the outer folder with subfolders on.
    run_b = _run(n, [("X/2025/02-February/a.jpg", "2025/02-February/a.jpg")])
    assert (n / "2025/02-February/a.jpg").read_bytes() == b"a"

    assert blocking_runs(inner, run_a) == [BlockingRun(run_b, str(n))]
    with pytest.raises(UndoBlocked) as exc:
        undo_run(inner, run_a)
    assert str(exc.value) == f"Undo these runs first: {run_b} (in {n})"
    assert not _undone(inner, run_a)

    undo_run(n, run_b)
    assert blocking_runs(inner, run_a) == []
    result = undo_run(inner, run_a)
    assert result.restored == 1
    assert (inner / "a.jpg").read_bytes() == b"a"


def test_nested_blocking_compares_case_insensitively(tmp_path):
    n = tmp_path / "n"
    inner = n / "X"
    _write(inner / "a.jpg", b"a")
    run_a = _run(inner, [("a.jpg", "d/a.jpg")])
    run_b = _crashed_journal(n, [("x/D/A.JPG", "e/a.jpg", False)])
    assert blocking_runs(inner, run_a) == [BlockingRun(run_b, str(n))]


def test_unrelated_nested_runs_do_not_block(tmp_path):
    n = tmp_path / "n"
    inner = n / "X"
    for rel in ("X/a.jpg", "X/sub/s.jpg", "Xtra/d/a.jpg", "b.jpg"):
        _write(n / rel, b"x")
    older_outer = _run(n, [("b.jpg", "X/d/b.jpg")])  # older than run_a
    run_a = _run(inner, [("a.jpg", "d/a.jpg")])
    _run(inner / "sub", [("s.jpg", "t/s.jpg")])  # newer, inside, other files
    _run(n, [("Xtra/d/a.jpg", "e/a.jpg")])  # newer, around: a sibling folder
    moved_b = _run(n, [("X/d/b.jpg", "f/b.jpg")])  # newer, around: not run_a's file
    assert blocking_runs(inner, run_a) == []
    # The runs inside moved none of older_outer's files; only moved_b did.
    assert blocking_runs(n, older_outer) == [BlockingRun(moved_b, "")]
    assert undo_run(inner, run_a).restored == 1
    assert (inner / "a.jpg").read_bytes() == b"x"


def test_undone_nested_runs_do_not_block(tmp_path):
    n = tmp_path / "n"
    _write(n / "a.jpg", b"a")
    run1 = _run(n, [("a.jpg", "d/a.jpg")])
    run2 = _run(n / "d", [("a.jpg", "e/a.jpg")])
    assert blocking_runs(n, run1) == [BlockingRun(run2, "d")]
    undo_run(n / "d", run2)
    assert blocking_runs(n, run1) == []


def test_undo_leaves_a_folder_it_did_not_make(tmp_path):
    _write(tmp_path / "x", b"x")
    run_id = _run(tmp_path, [("x", "Other/x")])
    (tmp_path / "x").mkdir()  # made by the owner after the run
    result = undo_run(tmp_path, run_id)
    assert result.renamed == [("x", "x (1)")]
    assert (tmp_path / "x").is_dir()
    assert (tmp_path / "x (1)").read_bytes() == b"x"


def test_undo_removes_an_empty_folder_of_the_run_in_a_files_place(tmp_path):
    _write(tmp_path / "PDF", b"p")
    _write(tmp_path / "a.pdf", b"a")
    run_id = _run(tmp_path, [("PDF", "Other/PDF"), ("a.pdf", "PDF/a.pdf")])
    result = undo_run(tmp_path, run_id)
    assert result.renamed == [] and result.failed == []
    assert (tmp_path / "PDF").read_bytes() == b"p"
    assert (tmp_path / "a.pdf").read_bytes() == b"a"
    assert not (tmp_path / "Other").exists()


def test_undo_keeps_a_folder_of_the_run_that_is_not_empty(tmp_path):
    _write(tmp_path / "PDF", b"p")
    _write(tmp_path / "a.pdf", b"a")
    run_id = _run(tmp_path, [("PDF", "Other/PDF"), ("a.pdf", "PDF/a.pdf")])
    _write(tmp_path / "PDF" / "new.pdf", b"n")  # the owner's, put there after the run
    result = undo_run(tmp_path, run_id)
    assert result.renamed == [("PDF", "PDF (1)")]
    assert (tmp_path / "PDF" / "new.pdf").read_bytes() == b"n"
    assert (tmp_path / "PDF (1)").read_bytes() == b"p"


def test_undo_never_removes_a_folder_that_was_there_before(tmp_path, case_sensitive_fs):
    (tmp_path / "PDF").mkdir()  # the owner's own, empty
    _write(tmp_path / "pdf", b"p")
    _write(tmp_path / "a.pdf", b"a")
    run_id = _run(tmp_path, [("pdf", "Other/pdf"), ("a.pdf", "PDF/a.pdf")])
    result = undo_run(tmp_path, run_id)
    assert (tmp_path / "PDF").is_dir()
    assert result.renamed == [("pdf", "pdf (1)")]
    assert (tmp_path / "a.pdf").read_bytes() == b"a"
