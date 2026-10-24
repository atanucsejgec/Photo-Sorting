import errno
import os
import threading

import pytest

from photosort import executor
from photosort.executor import ExecResult, execute, move_file
from photosort.journal import JournalMove, RunJournal, load_dirs, load_moves, load_runs
from photosort.plan import Move, Plan


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _run(root, moves, **kwargs) -> tuple[ExecResult, RunJournal]:
    plan = Plan("date", [Move(s, d) for s, d in moves], [])
    journal = RunJournal.create(root, "date", False, len(moves))
    return execute(root, plan, journal, **kwargs), journal


def test_execute_moves_and_journals(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    result, j = _run(tmp_path, [("a.jpg", "2025/a.jpg"), ("b.jpg", "No Date/b.jpg")])
    assert (tmp_path / "2025/a.jpg").is_file()
    assert (tmp_path / "No Date/b.jpg").is_file()
    assert not (tmp_path / "a.jpg").exists()
    assert not (tmp_path / "b.jpg").exists()
    assert result == ExecResult(moved=2, skipped=[], cancelled=False, run_id=j.run_id)
    assert load_moves(tmp_path, j.run_id) == [
        JournalMove("a.jpg", "2025/a.jpg", True),
        JournalMove("b.jpg", "No Date/b.jpg", True),
    ]
    (run,) = load_runs(tmp_path)
    assert (run.status, run.moved) == ("done", 2)


def test_execute_records_created_dirs(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "2025/old.jpg", b"o")
    _, j = _run(tmp_path, [("a.jpg", "2025/02-February/20-02-2025/a.jpg")])
    created, removed = load_dirs(tmp_path, j.run_id)
    assert created == ["2025/02-February", "2025/02-February/20-02-2025"]
    assert removed == []


def test_execute_never_overwrites(tmp_path):
    _write(tmp_path / "a.jpg", b"new")
    _write(tmp_path / "d/a.jpg", b"original")
    result, j = _run(tmp_path, [("a.jpg", "d/a.jpg")])
    assert (tmp_path / "d/a.jpg").read_bytes() == b"original"
    assert (tmp_path / "d/a (1).jpg").read_bytes() == b"new"
    assert result.moved == 1
    assert load_moves(tmp_path, j.run_id) == [JournalMove("a.jpg", "d/a (1).jpg", True)]


def test_execute_reallocates_on_case_variant_clash(tmp_path):
    _write(tmp_path / "a.jpg", b"new")
    _write(tmp_path / "d/A.JPG", b"original")
    _, j = _run(tmp_path, [("a.jpg", "d/a.jpg")])
    assert (tmp_path / "d/A.JPG").read_bytes() == b"original"
    assert (tmp_path / "d/a (1).jpg").read_bytes() == b"new"
    assert load_moves(tmp_path, j.run_id)[0].dst == "d/a (1).jpg"


def test_execute_skips_missing_source(tmp_path):
    _write(tmp_path / "b.jpg", b"b")
    result, j = _run(tmp_path, [("gone.jpg", "x/gone.jpg"), ("b.jpg", "x/b.jpg")])
    assert result.skipped == ["gone.jpg"]
    assert result.moved == 1
    assert (tmp_path / "x/b.jpg").read_bytes() == b"b"
    assert load_moves(tmp_path, j.run_id) == [JournalMove("b.jpg", "x/b.jpg", True)]


def test_move_file_refuses_existing_dst(tmp_path):
    src = _write(tmp_path / "a.jpg", b"src")
    dst = _write(tmp_path / "b.jpg", b"dst")
    with pytest.raises(FileExistsError):
        move_file(src, dst)
    assert src.read_bytes() == b"src"
    assert dst.read_bytes() == b"dst"


def _fake_cross_device(monkeypatch, src):
    real_rename = os.rename

    def fake_rename(a, b, *args, **kwargs):
        if os.fspath(a) == os.fspath(src):
            raise OSError(errno.EXDEV, "x")
        return real_rename(a, b, *args, **kwargs)

    monkeypatch.setattr(os, "rename", fake_rename)


def test_move_file_cross_device(tmp_path, monkeypatch):
    data = os.urandom(200_000)
    src = _write(tmp_path / "a.jpg", data)
    dst = tmp_path / "other" / "a.jpg"
    dst.parent.mkdir()
    _fake_cross_device(monkeypatch, src)
    move_file(src, dst)
    assert dst.read_bytes() == data
    assert not src.exists()
    assert os.listdir(dst.parent) == ["a.jpg"]  # no temp file left behind


def test_move_file_cross_device_mismatch_keeps_source(tmp_path, monkeypatch):
    src = _write(tmp_path / "a.jpg", b"good bytes")
    dst = tmp_path / "other" / "a.jpg"
    dst.parent.mkdir()
    _fake_cross_device(monkeypatch, src)

    def bad_copy(a, b, *args, **kwargs):
        with open(b, "wb") as f:
            f.write(b"bad bytes!")

    monkeypatch.setattr(executor.shutil, "copy2", bad_copy)
    with pytest.raises(IOError):
        move_file(src, dst)
    assert src.read_bytes() == b"good bytes"
    assert os.listdir(dst.parent) == []


def test_move_file_cross_device_refuses_existing_dst(tmp_path, monkeypatch):
    src = _write(tmp_path / "a.jpg", b"src")
    dst = _write(tmp_path / "other" / "a.jpg", b"dst")
    _fake_cross_device(monkeypatch, src)
    with pytest.raises(FileExistsError):
        move_file(src, dst)
    assert src.read_bytes() == b"src"
    assert dst.read_bytes() == b"dst"
    assert os.listdir(dst.parent) == ["a.jpg"]


def test_execute_removes_empty_source_dirs(tmp_path):
    _write(tmp_path / "sub/a.jpg", b"a")
    _write(tmp_path / "deep/er/b.jpg", b"b")
    _write(tmp_path / "keep/c.jpg", b"c")
    _write(tmp_path / "keep/other.txt", b"t")
    _, j = _run(tmp_path, [
        ("sub/a.jpg", "x/a.jpg"), ("deep/er/b.jpg", "x/b.jpg"), ("keep/c.jpg", "x/c.jpg"),
    ])
    assert not (tmp_path / "sub").exists()
    assert not (tmp_path / "deep").exists()
    assert (tmp_path / "keep/other.txt").exists()
    assert tmp_path.is_dir()
    assert (tmp_path / ".photosort").is_dir()
    _, removed = load_dirs(tmp_path, j.run_id)
    assert sorted(removed) == ["deep", "deep/er", "sub"]
    assert removed.index("deep/er") < removed.index("deep")


def test_execute_keeps_dst_parent_dirs(tmp_path):
    # A file moved out of a folder that is also the parent of another move's
    # target must not see that folder removed.
    _write(tmp_path / "x/a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    _, j = _run(tmp_path, [("x/a.jpg", "a.jpg"), ("b.jpg", "x/b.jpg")])
    assert (tmp_path / "x/b.jpg").read_bytes() == b"b"
    assert load_dirs(tmp_path, j.run_id)[1] == []


def test_execute_cancel(tmp_path):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _write(tmp_path / "sub" / name, name.encode())
    cancel = threading.Event()
    calls = []

    def progress(done, total):
        calls.append((done, total))
        cancel.set()

    result, j = _run(
        tmp_path,
        [("sub/a.jpg", "x/a.jpg"), ("sub/b.jpg", "x/b.jpg"), ("sub/c.jpg", "x/c.jpg")],
        progress=progress, cancel=cancel,
    )
    assert result.cancelled is True
    assert result.moved == 1
    assert calls == [(1, 3)]
    assert (tmp_path / "sub/b.jpg").exists()
    (run,) = load_runs(tmp_path)
    assert (run.status, run.moved) == ("cancelled", 1)


def test_execute_progress_counts_every_move(tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    calls = []
    _run(tmp_path, [("gone.jpg", "x/gone.jpg"), ("a.jpg", "x/a.jpg")],
         progress=lambda d, t: calls.append((d, t)))
    assert calls == [(1, 2), (2, 2)]


def test_execute_failure_marks_failed_and_keeps_journal(tmp_path, monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    real_move = executor.move_file

    def flaky(src, dst):
        if src.name == "b.jpg":
            raise PermissionError("nope")
        real_move(src, dst)

    monkeypatch.setattr(executor, "move_file", flaky)
    plan = Plan("date", [Move("a.jpg", "x/a.jpg"), Move("b.jpg", "x/b.jpg")], [])
    j = RunJournal.create(tmp_path, "date", False, 2)
    with pytest.raises(PermissionError):
        execute(tmp_path, plan, j)
    assert load_moves(tmp_path, j.run_id) == [
        JournalMove("a.jpg", "x/a.jpg", True), JournalMove("b.jpg", "x/b.jpg", False),
    ]
    (run,) = load_runs(tmp_path)
    assert (run.status, run.moved) == ("failed", 1)
    assert (tmp_path / "b.jpg").read_bytes() == b"b"


def test_execute_lists_target_folder_once(tmp_path, monkeypatch):
    from photosort import naming

    moves = []
    for k in range(300):
        _write(tmp_path / f"f{k:03d}.jpg", b"x")
        moves.append((f"f{k:03d}.jpg", f"x/f{k:03d}.jpg"))
    real_listdir = os.listdir
    listed = []

    def spy(path="."):
        listed.append(os.fspath(path))
        return real_listdir(path)

    monkeypatch.setattr(naming.os, "listdir", spy)
    result, _ = _run(tmp_path, moves)
    monkeypatch.undo()
    assert result.moved == 300
    assert len(real_listdir(tmp_path / "x")) == 300
    assert listed.count(os.fspath(tmp_path / "x")) <= 2


def test_execute_journals_rmdir_before_removing(tmp_path, monkeypatch):
    _write(tmp_path / "sub/a.jpg", b"a")
    real_rmdir = os.rmdir
    seen = []

    def spy_rmdir(path, *args, **kwargs):
        runs = tmp_path / ".photosort" / "runs"
        (log,) = runs.glob("*.jsonl")
        seen.append(b'"rmdir"' in log.read_bytes())
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(executor.os, "rmdir", spy_rmdir)
    _, j = _run(tmp_path, [("sub/a.jpg", "x/a.jpg")])
    monkeypatch.undo()
    assert seen == [True]
    assert not (tmp_path / "sub").exists()
    assert load_dirs(tmp_path, j.run_id)[1] == ["sub"]


def test_execute_journals_mkdir_before_creating(tmp_path, monkeypatch):
    # A folder made with no record would be left behind by undo; a record
    # for a folder never made is harmless (undo only removes empty folders).
    _write(tmp_path / "a.jpg", b"a")
    real_mkdir = os.mkdir
    seen = []

    def spy_mkdir(path, *args, **kwargs):
        runs = tmp_path / ".photosort" / "runs"
        (log,) = runs.glob("*.jsonl")
        rel = os.path.relpath(path, tmp_path)
        seen.append(f'"path": "{rel}"'.encode() in log.read_bytes())
        return real_mkdir(path, *args, **kwargs)

    plan = Plan("date", [Move("a.jpg", "x/y/a.jpg")], [])
    journal = RunJournal.create(tmp_path, "date", False, 1)
    monkeypatch.setattr(executor.os, "mkdir", spy_mkdir)
    execute(tmp_path, plan, journal)
    monkeypatch.undo()
    assert seen == [True, True]
    assert (tmp_path / "x/y/a.jpg").read_bytes() == b"a"
    assert load_dirs(tmp_path, journal.run_id)[0] == ["x", "x/y"]


@pytest.mark.usefixtures("case_sensitive_fs")
def test_execute_reuses_case_twin_folder(tmp_path, make_jpeg):
    # The owner's drive is NTFS (used from Windows too) mounted case-sensitively:
    # a "No Date" beside the owner's "no date" would be a broken twin.
    from photosort.modes.date_mode import prepare_date_plan

    _write(tmp_path / "no date/old.jpg", b"o")
    (tmp_path / "2025/02-february").mkdir(parents=True)
    make_jpeg(tmp_path / "x.jpg")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_jpeg(tmp_path / name, "2025:02:20 10:00:00")
    plan = prepare_date_plan(tmp_path, False)
    journal = RunJournal.create(tmp_path, "date", False, len(plan.moves))
    result = execute(tmp_path, plan, journal)

    assert result.moved == 4
    assert sorted(os.listdir(tmp_path)) == [".photosort", "2025", "no date"]
    assert sorted(os.listdir(tmp_path / "no date")) == ["old.jpg", "x.jpg"]
    assert os.listdir(tmp_path / "2025") == ["02-february"]
    day = tmp_path / "2025/02-february/20-02-2025"
    assert sorted(os.listdir(day)) == ["a.jpg", "b.jpg", "c.jpg"]
    assert load_dirs(tmp_path, journal.run_id)[0] == ["2025/02-february/20-02-2025"]
