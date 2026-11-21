"""Preview's core: which files are listed, and moving, putting back and journalling them."""

import errno
import os
from datetime import date, datetime
from pathlib import Path

import pytest

from fstree import tree
from photosort import undo
from photosort.executor import execute
from photosort.journal import RunJournal, load_all_runs, load_dirs, load_moves, load_runs
from photosort.modes import MODE_LABELS, MODE_SPECS, MODES
from photosort.modes.preview import (
    NOT_NEEDED,
    SLOTS,
    PreviewSession,
    PutBackError,
    day_text,
    file_day,
    file_kind,
    inside_root,
    preview_files,
    target_problem,
)
from photosort.plan import Move, Plan

APP_FOLDER = "Photo Sorter keeps its own records there. Pick another folder."


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def root(tmp_path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


# ----- the mode ----------------------------------------------------------------------------

def test_the_mode_comes_after_pick_faces():
    keys = [key for key, _, _ in MODES]
    assert keys[keys.index("pick_faces") + 1] == "preview"
    assert ("preview", "Preview", True) in MODES
    assert MODE_LABELS["preview"] == "Preview"
    assert MODE_SPECS["preview"].kind == "preview"
    with pytest.raises(NotImplementedError):
        MODE_SPECS["preview"].prepare(Path("."), False)
    assert NOT_NEEDED == "Not needed"
    assert SLOTS == 10


# ----- kinds and listing -------------------------------------------------------------------

@pytest.mark.parametrize("name, kind", [
    ("a.jpg", "image"), ("b.HEIC", "image"), ("c.png", "image"), ("d.gif", "image"),
    ("e.pdf", "pdf"), ("f.PDF", "pdf"),
    ("g.mp4", "video"), ("h.MOV", "video"),
    ("i.mp3", "audio"), ("j.m4a", "audio"), ("k.wav", "audio"), ("l.OPUS", "audio"),
    ("m.txt", "text"), ("n.CSV", "text"), ("o.yml", "text"), ("p.srt", "text"),
    ("q.zip", "other"), ("r.docx", "other"), ("noext", "other"),
])
def test_file_kind_by_extension(name, kind):
    assert file_kind(Path("/x") / name) == kind


def test_preview_files_skips_not_needed_slots_and_app_folders(root, tmp_path):
    for rel in ["a.jpg", "sub/b.txt", "sub/deep/c.zip", "Not needed/x.jpg",
                "not NEEDED/y.jpg", "Trips/t.jpg", "Family/Kids/k.jpg", "Family/f.jpg",
                "Duplicates/d.jpg", ".hidden/h.jpg", ".dot.jpg", "sub/.photosort/z"]:
        _write(root / rel, b"x")
    _write(tmp_path / "elsewhere" / "e.jpg", b"x")
    slots = [str(root / "Trips"), None, str(root / "Family" / "Kids"),
             str(tmp_path / "elsewhere"), str(root)] + [None] * 5
    assert preview_files(root, True, slots) == [
        "a.jpg", "Family/f.jpg", "sub/b.txt", "sub/deep/c.zip"]
    assert preview_files(root, False, slots) == ["a.jpg"]


def test_preview_files_natural_order(root):
    for rel in ["IMG_10.jpg", "IMG_2.jpg", "img_1.jpg", "b/IMG_3.jpg", "a10/x.jpg",
                "a9/x.jpg", "Z.jpg"]:
        _write(root / rel, b"x")
    assert preview_files(root, True, [None] * SLOTS) == [
        "img_1.jpg", "IMG_2.jpg", "IMG_10.jpg", "Z.jpg",
        "a9/x.jpg", "a10/x.jpg", "b/IMG_3.jpg"]


# ----- slots ---------------------------------------------------------------------------------

def test_inside_root(root, tmp_path):
    assert inside_root(root, root)
    assert inside_root(root, root / "a" / "b")
    assert not inside_root(root, tmp_path)
    assert not inside_root(root, tmp_path / "photos2")
    (tmp_path / "link").symlink_to(root, target_is_directory=True)
    assert inside_root(root, tmp_path / "link" / "Trips")
    (root / "out").symlink_to(tmp_path, target_is_directory=True)
    assert not inside_root(root, root / "out")


# ----- moving --------------------------------------------------------------------------------

def test_move_to_not_needed_then_undo_run_restores_bytes(root):
    _write(root / "a.jpg", b"aaa")
    _write(root / "sub" / "b.txt", b"bbb")
    _write(root / "Trips" / "old.jpg", b"old")
    before = tree(root)
    session = PreviewSession(root, True)
    assert session.move("a.jpg", root / NOT_NEEDED) == "Not needed/a.jpg"
    assert session.move("sub/b.txt", root / "Trips") == "Trips/b.txt"
    assert (root / "Not needed" / "a.jpg").read_bytes() == b"aaa"
    assert session.moved == [("a.jpg", "Not needed/a.jpg"), ("sub/b.txt", "Trips/b.txt")]
    assert session.counts() == (1, 1)
    run_id = session.finish()
    assert run_id is not None
    assert (root / "sub").is_dir()  # the owner's folder, not one the session made
    created, _ = load_dirs(root, run_id)
    assert created == ["Not needed"]
    undo.undo_run(root, run_id)
    assert tree(root) == before
    assert not (root / "Not needed").exists()


def test_put_back_all_then_undo_is_noop_and_tree_unchanged(root):
    _write(root / "a.jpg", b"aaa")
    _write(root / "sub" / "b.txt", b"bbb")
    before = tree(root)
    session = PreviewSession(root, True)
    session.move("a.jpg", root / NOT_NEEDED)
    session.move("sub/b.txt", root / "New" / "Deeper")
    assert session.counts() == (1, 1)
    assert session.put_back() == "sub/b.txt"
    assert session.counts() == (1, 0)
    assert session.put_back() == "a.jpg"
    assert session.put_back() is None
    assert session.moved == []
    assert session.counts() == (0, 0)
    run_id = session.finish()
    assert run_id is not None
    assert tree(root) == before  # Not needed, New and New/Deeper tidied away
    assert [(m.src, m.dst, m.done) for m in load_moves(root, run_id)] == [
        ("a.jpg", "Not needed/a.jpg", True),
        ("sub/b.txt", "New/Deeper/b.txt", True),
        ("New/Deeper/b.txt", "sub/b.txt", True),
        ("Not needed/a.jpg", "a.jpg", True),
    ]
    undo.undo_run(root, run_id)
    assert tree(root) == before
    assert not (root / "Not needed").exists()


def test_put_back_when_the_place_is_taken_gets_a_new_name(root):
    _write(root / "a.jpg", b"first")
    session = PreviewSession(root, False)
    session.move("a.jpg", root / NOT_NEEDED)
    _write(root / "a.jpg", b"second")
    assert session.put_back() == "a (1).jpg"
    assert (root / "a.jpg").read_bytes() == b"second"
    assert (root / "a (1).jpg").read_bytes() == b"first"
    session.finish()


def test_move_never_overwrites(root):
    _write(root / "a.jpg", b"new")
    _write(root / "Not needed" / "a.jpg", b"there")
    _write(root / "Not needed" / "a (1).jpg", b"there too")
    before = tree(root)
    session = PreviewSession(root, False)
    assert session.move("a.jpg", root / NOT_NEEDED) == "Not needed/a (2).jpg"
    assert (root / "Not needed" / "a.jpg").read_bytes() == b"there"
    assert (root / "Not needed" / "a (1).jpg").read_bytes() == b"there too"
    assert (root / "Not needed" / "a (2).jpg").read_bytes() == b"new"
    run_id = session.finish()
    assert load_dirs(root, run_id) == ([], [])  # the folder was the owner's
    undo.undo_run(root, run_id)
    assert tree(root) == before


def test_move_outside_root_goes_there_absolute_and_undo_restores(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    other = tmp_path / "other"
    other.mkdir()
    before, before_other = tree(root), tree(other)
    session = PreviewSession(root, False)
    dst = session.move("a.jpg", other)
    assert dst == (other / "a.jpg").as_posix()
    assert (other / "a.jpg").read_bytes() == b"aaa"
    assert session.moved == [("a.jpg", dst)]
    assert session.counts() == (0, 1)
    run_id = session.finish()
    [run] = load_runs(root)
    assert run.also_roots == (other,)
    assert [(m.src, m.dst) for m in load_moves(root, run_id)] == [("a.jpg", dst)]
    undo.undo_run(root, run_id)
    assert tree(root) == before
    assert tree(other) == before_other


def test_move_through_a_link_to_outside_is_journalled_resolved(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    (tmp_path / "other").mkdir()
    (root / "out").symlink_to(tmp_path / "other", target_is_directory=True)
    session = PreviewSession(root, False)
    assert session.move("a.jpg", root / "out") == (tmp_path / "other" / "a.jpg").as_posix()
    run_id = session.finish()
    assert load_runs(root)[0].also_roots == (tmp_path / "other",)
    undo.undo_run(root, run_id)
    assert (root / "a.jpg").read_bytes() == b"aaa"


def test_target_problem(root, tmp_path, _app_data):
    assert target_problem(root, None) == "Slot is empty"
    assert target_problem(root, root) is None
    assert target_problem(root, root / "Trips") is None  # missing inside: made on use
    (tmp_path / "other").mkdir()
    assert target_problem(root, tmp_path / "other") is None
    assert target_problem(root, str(tmp_path / "other")) is None
    assert target_problem(root, tmp_path / "gone") == (
        f"{tmp_path / 'gone'} isn't there — is the drive plugged in?")
    assert target_problem(root, tmp_path / "gone") == undo.drive_missing(tmp_path / "gone")
    (root / ".photosort").mkdir()
    assert target_problem(root, root / ".photosort") == APP_FOLDER
    (tmp_path / "x" / ".photosort" / "y").mkdir(parents=True)
    assert target_problem(root, tmp_path / "x" / ".photosort" / "y") == APP_FOLDER
    (_app_data / "runs").mkdir(parents=True, exist_ok=True)
    assert target_problem(root, _app_data) == APP_FOLDER
    assert target_problem(root, _app_data / "runs") == APP_FOLDER


def test_move_refuses_a_missing_outside_folder_and_app_folders(root, tmp_path, _app_data):
    _write(root / "a.jpg", b"aaa")
    before = tree(tmp_path)
    session = PreviewSession(root, False)
    with pytest.raises(FileNotFoundError) as caught:
        session.move("a.jpg", tmp_path / "Drive" / "Trips")
    assert str(caught.value) == (
        f"{tmp_path / 'Drive' / 'Trips'} isn't there — is the drive plugged in?")
    assert not (tmp_path / "Drive").exists()
    with pytest.raises(ValueError, match="keeps its own records"):
        session.move("a.jpg", _app_data)
    (tmp_path / "x" / ".photosort").mkdir(parents=True)
    with pytest.raises(ValueError, match="keeps its own records"):
        session.move("a.jpg", tmp_path / "x" / ".photosort")
    (tmp_path / "x" / ".photosort").rmdir()
    (tmp_path / "x").rmdir()
    assert tree(tmp_path) == before
    assert session.moved == []
    assert session.finish() is None
    assert load_runs(root) == []


# ----- moving anywhere: the review focus ------------------------------------------------------

def _date_run(root: Path, moves) -> str:
    plan = Plan("date", [Move(s, d) for s, d in moves], [])
    journal = RunJournal.create(root, "date", False, len(moves))
    execute(root, plan, journal)
    return journal.run_id


def _undone(run_id: str) -> bool:
    return next(r for r in load_all_runs() if r.run_id == run_id).undone


def test_later_sort_in_the_outside_target_blocks_preview_undo(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    _write(root / "b.jpg", b"bbb")
    target = tmp_path / "Archive"
    _write(target / "mine.jpg", b"mine")
    before, before_target = tree(root), tree(target)
    session = PreviewSession(root, False)
    session.move("a.jpg", target)
    session.move("b.jpg", root / NOT_NEEDED)
    preview_run = session.finish()
    later = _date_run(target, [("a.jpg", "2024/a.jpg")])  # moves the file Preview placed
    expected = [undo.BlockingRun(later, str(target))]
    assert undo.blocking_runs(root, preview_run) == expected
    with pytest.raises(undo.UndoBlocked) as caught:
        undo.undo_run(root, preview_run)
    assert caught.value.blocking == expected
    assert not _undone(preview_run)
    assert (target / "2024" / "a.jpg").read_bytes() == b"aaa"
    assert (root / NOT_NEEDED / "b.jpg").read_bytes() == b"bbb"  # nothing moved
    undo.undo_run(target, later)
    assert undo.blocking_runs(root, preview_run) == []
    undo.undo_run(root, preview_run)
    assert tree(root) == before
    assert tree(target) == before_target
    assert _undone(preview_run)


def test_a_later_sort_of_a_folder_around_the_target_blocks_too(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    target = tmp_path / "Disk" / "Archive"
    target.mkdir(parents=True)
    session = PreviewSession(root, False)
    session.move("a.jpg", target)
    preview_run = session.finish()
    later = Plan("date", [Move("Archive/a.jpg", "x/a.jpg")], [])
    journal = RunJournal.create(tmp_path / "Disk", "date", True, 1)
    execute(tmp_path / "Disk", later, journal)
    assert [b.run_id for b in undo.blocking_runs(root, preview_run)] == [journal.run_id]
    # Older runs of the target's folders never block.
    assert undo.blocking_runs(tmp_path / "Disk", journal.run_id) == []


def _other_drive(monkeypatch, drive: Path) -> list:
    """os.rename fails with EXDEV between drive and anywhere else, as across drives."""
    real = os.rename
    drive = os.path.abspath(drive)
    crossed = []

    def on(path) -> bool:
        path = os.path.abspath(path)
        return path == drive or path.startswith(drive + os.sep)

    def rename(src, dst, *args, **kwargs):
        if on(src) != on(dst):
            crossed.append((str(src), str(dst)))
            raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), str(src))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename)
    return crossed


def test_cross_drive_move_and_undo_restore_byte_for_byte(root, tmp_path, monkeypatch):
    data = os.urandom(300_000)
    _write(root / "sub" / "big.mov", data)
    _write(root / "c.jpg", b"ccc")
    drive = tmp_path / "usb"
    target = drive / "Videos"
    target.mkdir(parents=True)
    crossed = _other_drive(monkeypatch, drive)
    before, before_drive = tree(root), tree(drive)
    session = PreviewSession(root, True)
    dst = session.move("sub/big.mov", target)
    assert crossed  # it really went the cross-drive way
    assert not (root / "sub" / "big.mov").exists()
    assert (target / "big.mov").read_bytes() == data
    assert not [p for p in target.iterdir() if p.name != "big.mov"]  # no temp left
    session.move("c.jpg", target)
    assert session.put_back() == "c.jpg"  # back across too
    run_id = session.finish()
    assert dst == (target / "big.mov").as_posix()
    n = len(crossed)
    undo.undo_run(root, run_id)
    assert len(crossed) > n
    assert tree(root) == before
    assert tree(drive) == before_drive


def test_cross_drive_copy_that_does_not_verify_keeps_the_original(root, tmp_path, monkeypatch):
    from photosort import executor

    _write(root / "a.jpg", b"original")
    drive = tmp_path / "usb"
    drive.mkdir()
    _other_drive(monkeypatch, drive)

    def bad_copy(src, dst, *args, **kwargs):
        Path(dst).write_bytes(b"garbled!")

    monkeypatch.setattr(executor.shutil, "copy2", bad_copy)
    before, before_drive = tree(root), tree(drive)
    session = PreviewSession(root, False)
    with pytest.raises(OSError, match="does not match"):
        session.move("a.jpg", drive)
    assert tree(root) == before  # the original stays
    assert tree(drive) == before_drive  # no copy, no temp file
    assert session.moved == []
    run_id = session.finish()
    undo.undo_run(root, run_id)
    assert tree(root) == before


def test_missing_target_drive_at_undo_refuses_and_moves_nothing(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    _write(root / "b.jpg", b"bbb")
    drive = tmp_path / "usb"
    target = drive / "Keep"
    target.mkdir(parents=True)
    session = PreviewSession(root, False)
    session.move("a.jpg", target)
    session.move("b.jpg", root / "Trips")  # an inside move: must not go back alone
    run_id = session.finish()
    os.rename(drive, tmp_path / "unplugged")  # the drive is gone
    before = tree(root)
    with pytest.raises(undo.FolderMissing) as caught:
        undo.undo_run(root, run_id)
    assert str(caught.value) == f"{target} isn't there — is the drive plugged in?"
    assert tree(root) == before
    assert not drive.exists()
    assert not _undone(run_id)
    os.rename(tmp_path / "unplugged", drive)  # plugged back in
    undo.undo_run(root, run_id)
    assert (root / "a.jpg").read_bytes() == b"aaa"
    assert (root / "b.jpg").read_bytes() == b"bbb"
    assert not (root / "Trips").exists()
    assert list(target.iterdir()) == []
    assert _undone(run_id)


def test_an_empty_mount_point_for_the_target_refuses_too(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    _write(root / "b.jpg", b"bbb")
    target = tmp_path / "usb"
    target.mkdir()
    session = PreviewSession(root, False)
    session.move("a.jpg", target)
    session.move("b.jpg", root / "Trips")
    run_id = session.finish()
    os.rename(target / "a.jpg", tmp_path / "away.jpg")  # unplugged: the mount point is left
    before = tree(root)
    with pytest.raises(undo.FolderMissing) as caught:
        undo.undo_run(root, run_id)
    assert str(caught.value) == f"{target} isn't there — is the drive plugged in?"
    assert tree(root) == before
    assert list(target.iterdir()) == []
    assert not _undone(run_id)


def test_mixed_inside_and_outside_moves_with_put_backs_undo(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    _write(root / "sub" / "a.jpg", b"sub a")
    _write(root / "b.jpg", b"bbb")
    _write(root / "c.jpg", b"ccc")
    one, two = tmp_path / "One", tmp_path / "Two"
    one.mkdir()
    two.mkdir()
    before = tree(tmp_path)
    session = PreviewSession(root, True)
    assert session.move("a.jpg", one) == (one / "a.jpg").as_posix()
    assert session.move("sub/a.jpg", one) == (one / "a (1).jpg").as_posix()
    assert session.move("b.jpg", root / NOT_NEEDED) == "Not needed/b.jpg"
    assert session.move("c.jpg", two) == (two / "c.jpg").as_posix()
    assert session.put_back() == "c.jpg"
    assert session.move("c.jpg", one) == (one / "c.jpg").as_posix()
    assert session.put_back() == "c.jpg"
    assert session.put_back() == "b.jpg"
    assert session.move("b.jpg", two) == (two / "b.jpg").as_posix()
    assert session.put_back() == "b.jpg"
    assert session.put_back() == "sub/a.jpg"
    assert session.move("b.jpg", one) == (one / "b.jpg").as_posix()
    # The name freed by the put-back is taken again.
    assert session.move("sub/a.jpg", one) == (one / "a (1).jpg").as_posix()
    assert session.counts() == (0, 3)
    run_id = session.finish()
    assert set(load_runs(root)[0].also_roots) == {one, two}
    undo.undo_run(root, run_id)
    assert tree(tmp_path) == before


def test_name_clash_in_the_outside_target_never_overwrites(root, tmp_path):
    _write(root / "a.jpg", b"new")
    target = tmp_path / "Other"
    _write(target / "a.jpg", b"there")
    _write(target / "A (1).JPG", b"there too")
    before, before_target = tree(root), tree(target)
    session = PreviewSession(root, False)
    assert session.move("a.jpg", target) == (target / "a (2).jpg").as_posix()
    assert (target / "a.jpg").read_bytes() == b"there"
    assert (target / "A (1).JPG").read_bytes() == b"there too"
    assert (target / "a (2).jpg").read_bytes() == b"new"
    run_id = session.finish()
    undo.undo_run(root, run_id)
    assert tree(root) == before
    assert tree(target) == before_target


def test_undo_puts_an_outside_file_back_beside_a_new_one(root, tmp_path):
    _write(root / "a.jpg", b"mine")
    target = tmp_path / "Other"
    target.mkdir()
    session = PreviewSession(root, False)
    session.move("a.jpg", target)
    run_id = session.finish()
    _write(root / "a.jpg", b"a new one")  # the place is taken meanwhile
    result = undo.undo_run(root, run_id)
    assert result.renamed == [("a.jpg", "a (1).jpg")]
    assert (root / "a.jpg").read_bytes() == b"a new one"
    assert (root / "a (1).jpg").read_bytes() == b"mine"


def test_move_into_its_own_folder_does_nothing(root):
    _write(root / "sub" / "a.jpg", b"aaa")
    before = tree(root)
    session = PreviewSession(root, True)
    assert session.move("sub/a.jpg", root / "sub") == "sub/a.jpg"
    assert session.moved == []
    assert session.finish() is None
    assert tree(root) == before


def test_missing_source_raises_and_session_continues(root):
    _write(root / "a.jpg", b"aaa")
    _write(root / "b.jpg", b"bbb")
    before = tree(root)
    session = PreviewSession(root, False)
    with pytest.raises(FileNotFoundError):
        session.move("gone.jpg", root / NOT_NEEDED)
    assert not (root / "Not needed").exists()
    assert session.move("a.jpg", root / NOT_NEEDED) == "Not needed/a.jpg"
    with pytest.raises(FileNotFoundError):
        session.move("gone.jpg", root / "Trips")
    assert session.move("b.jpg", root / "Trips") == "Trips/b.jpg"
    assert session.counts() == (1, 1)
    run_id = session.finish()
    assert [m.src for m in load_moves(root, run_id)] == ["a.jpg", "b.jpg"]
    undo.undo_run(root, run_id)
    assert tree(root) == before


def test_put_back_of_a_file_gone_meanwhile(root):
    _write(root / "a.jpg", b"aaa")
    _write(root / "b.jpg", b"bbb")
    session = PreviewSession(root, False)
    session.move("a.jpg", root / NOT_NEEDED)
    session.move("b.jpg", root / NOT_NEEDED)
    (root / "Not needed" / "b.jpg").rename(root / "elsewhere.jpg")
    with pytest.raises(FileNotFoundError):
        session.put_back()
    assert session.moved == [("a.jpg", "Not needed/a.jpg")]  # the next Ctrl+Z goes on
    assert session.put_back() == "a.jpg"
    session.finish()


def test_slot_folder_deleted_is_recreated(root):
    _write(root / "a.jpg", b"aaa")
    (root / "Trips").mkdir()
    (root / "Trips").rmdir()  # listed as a slot, deleted since
    before = tree(root)
    session = PreviewSession(root, False)
    assert target_problem(root, str(root / "Trips")) is None
    assert session.move("a.jpg", root / "Trips") == "Trips/a.jpg"
    assert (root / "Trips" / "a.jpg").read_bytes() == b"aaa"
    run_id = session.finish()
    assert load_dirs(root, run_id)[0] == ["Trips"]
    undo.undo_run(root, run_id)
    assert tree(root) == before


def test_finish_without_moves_makes_no_run(root):
    _write(root / "a.jpg", b"aaa")
    session = PreviewSession(root, False)
    assert session.put_back() is None
    assert session.finish() is None
    assert session.finish() is None
    assert load_runs(root) == []


def test_finish_twice_is_safe(root):
    _write(root / "a.jpg", b"aaa")
    session = PreviewSession(root, False)
    session.move("a.jpg", root / NOT_NEEDED)
    run_id = session.finish()
    assert session.finish() == run_id
    assert [r.status for r in load_runs(root)] == ["done"]


def test_session_run_listed_in_history_as_preview(root):
    _write(root / "a.jpg", b"aaa")
    session = PreviewSession(root, False)
    session.move("a.jpg", root / NOT_NEEDED)
    run_id = session.finish()
    [run] = load_runs(root)
    assert (run.run_id, run.mode, run.status, run.moved) == (run_id, "preview", "done", 1)
    assert MODE_LABELS[run.mode] == "Preview"


def test_a_session_leaves_no_photosort_folder(root):
    _write(root / "a.jpg", b"aaa")
    _write(root / "sub" / "b.jpg", b"bbb")
    before = tree(root)
    session = PreviewSession(root, True)
    session.move("a.jpg", root / NOT_NEEDED)
    session.move("sub/b.jpg", root / "Trips")
    session.put_back()
    run_id = session.finish()
    undo.undo_run(root, run_id)
    assert not [p for p in root.rglob("*") if p.name.casefold() == ".photosort"]
    assert tree(root) == before


def test_moves_and_put_backs_with_names_reused_then_undo(root):
    _write(root / "a.jpg", b"top")
    _write(root / "sub" / "a.jpg", b"sub")
    _write(root / "b.jpg", b"bbb")
    before = tree(root)
    session = PreviewSession(root, True)
    assert session.move("a.jpg", root / NOT_NEEDED) == "Not needed/a.jpg"
    assert session.move("sub/a.jpg", root / NOT_NEEDED) == "Not needed/a (1).jpg"
    assert session.put_back() == "sub/a.jpg"
    # The name freed by the put-back is taken again.
    assert session.move("sub/a.jpg", root / NOT_NEEDED) == "Not needed/a (1).jpg"
    assert session.move("b.jpg", root / "Trips") == "Trips/b.jpg"
    assert session.put_back() == "b.jpg"
    assert session.move("b.jpg", root / "sub") == "sub/b.jpg"
    assert (root / "Not needed" / "a (1).jpg").read_bytes() == b"sub"
    assert session.counts() == (2, 1)
    run_id = session.finish()
    assert not (root / "Trips").exists()  # made by the session, empty again: tidied
    undo.undo_run(root, run_id)
    assert tree(root) == before


def test_a_move_failing_after_its_intent_leaves_the_session_going(root, monkeypatch):
    from photosort.modes import preview

    _write(root / "a.jpg", b"aaa")
    _write(root / "b.jpg", b"bbb")
    before = tree(root)
    real = preview.move_file
    calls = []

    def failing_once(src, dst):
        calls.append((src, dst))
        if len(calls) == 1:
            raise PermissionError(13, "Permission denied", str(dst))
        real(src, dst)

    monkeypatch.setattr(preview, "move_file", failing_once)
    session = PreviewSession(root, False)
    with pytest.raises(PermissionError):
        session.move("a.jpg", root / NOT_NEEDED)
    assert session.moved == []
    assert (root / "a.jpg").read_bytes() == b"aaa"
    assert session.move("b.jpg", root / "Trips") == "Trips/b.jpg"
    assert session.move("a.jpg", root / NOT_NEEDED) == "Not needed/a.jpg"
    assert session.counts() == (1, 1)
    run_id = session.finish()
    undo.undo_run(root, run_id)
    assert tree(root) == before


@pytest.mark.parametrize("rel", ["../outside.jpg", "sub/../../outside.jpg"])
def test_move_refuses_a_file_outside_the_root(root, tmp_path, rel):
    _write(tmp_path / "outside.jpg", b"out")
    (root / "sub").mkdir()
    session = PreviewSession(root, False)
    with pytest.raises(ValueError):
        session.move(rel, root / NOT_NEEDED)
    with pytest.raises(ValueError):
        session.move(str(tmp_path / "outside.jpg"), root / NOT_NEEDED)
    assert (tmp_path / "outside.jpg").read_bytes() == b"out"
    assert not (root / NOT_NEEDED).exists()
    assert session.finish() is None
    assert load_runs(root) == []


def test_move_refuses_a_file_through_a_link_out_of_the_root(root, tmp_path):
    _write(tmp_path / "elsewhere" / "x.jpg", b"xxx")
    (root / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    session = PreviewSession(root, False)
    with pytest.raises(ValueError):
        session.move("link/x.jpg", root / NOT_NEEDED)
    assert (tmp_path / "elsewhere" / "x.jpg").read_bytes() == b"xxx"
    assert session.finish() is None


# ----- days and groups -----------------------------------------------------------------------

def test_file_day_is_the_modified_date(root):
    path = _write(root / "a.jpg", b"aaa")
    when = datetime(2025, 2, 28, 23, 30).timestamp()  # local time, late in the day
    os.utime(path, (when, when))
    assert file_day(path) == date(2025, 2, 28)
    assert file_day(root / "missing.jpg") is None


def test_day_text():
    assert day_text(date(2025, 2, 28)) == "Fri 28 Feb 2025"
    assert day_text(date(2026, 11, 21)) == "Sat 21 Nov 2026"
    assert day_text(date(2024, 6, 3)) == "Mon 3 Jun 2024"


def test_group_moves_put_back_together(root):
    for name in ("a", "b", "c", "d"):
        _write(root / f"{name}.jpg", name.encode() * 3)
    before = tree(root)
    session = PreviewSession(root, False)
    for name in ("a", "b", "c"):
        session.move(f"{name}.jpg", root / NOT_NEEDED, group=7)
    session.move("d.jpg", root / "Trips", group=8)
    assert session.put_back_group() == ["d.jpg"]
    assert session.put_back_group() == ["c.jpg", "b.jpg", "a.jpg"]
    assert session.put_back_group() == []
    assert session.moved == []
    run_id = session.finish()
    assert tree(root) == before  # every file back, byte for byte; made folders tidied
    undo.undo_run(root, run_id)
    assert tree(root) == before


def test_untagged_moves_are_groups_of_one(root):
    for name in ("a", "b", "c"):
        _write(root / f"{name}.jpg", name.encode())
    session = PreviewSession(root, False)
    session.move("a.jpg", root / NOT_NEEDED, group=1)
    session.move("b.jpg", root / NOT_NEEDED)
    session.move("c.jpg", root / NOT_NEEDED)
    assert session.put_back_group() == ["c.jpg"]
    assert session.put_back_group() == ["b.jpg"]
    assert session.put_back() == "a.jpg"  # one file at a time still works
    assert session.put_back_group() == []
    session.finish()


def test_put_back_group_partial_failure(root):
    for name in ("a", "b", "c"):
        _write(root / f"{name}.jpg", name.encode() * 3)
    session = PreviewSession(root, False)
    for name in ("a", "b", "c"):
        session.move(f"{name}.jpg", root / NOT_NEEDED, group=7)
    (root / NOT_NEEDED / "b.jpg").unlink()
    with pytest.raises(PutBackError) as failed:
        session.put_back_group()
    assert failed.value.back == ["c.jpg"]
    assert isinstance(failed.value.error, FileNotFoundError)
    assert (root / "c.jpg").read_bytes() == b"ccc"  # already back, stays back
    assert session.moved == [("a.jpg", "Not needed/a.jpg")]  # b dropped, a still live
    assert session.put_back_group() == ["a.jpg"]
    assert (root / "a.jpg").read_bytes() == b"aaa"
    assert session.put_back_group() == []
    session.finish()
