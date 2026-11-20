"""Preview's core: which files are listed, and moving, putting back and journalling them."""

from pathlib import Path

import pytest

from fstree import tree
from photosort import undo
from photosort.journal import load_dirs, load_moves, load_runs
from photosort.modes import MODE_LABELS, MODE_SPECS, MODES
from photosort.modes.preview import (
    NOT_NEEDED,
    SLOTS,
    PreviewSession,
    file_kind,
    inside_root,
    preview_files,
    slot_problem,
)

OUTSIDE = ("Pick a folder inside photos — moves stay inside the folder you opened, "
           "so Undo can put them back.")


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

def test_slot_problem_and_inside_root(root, tmp_path):
    assert slot_problem(root, None) == "Slot is empty"
    assert slot_problem(root, str(root / "Trips")) is None  # missing is fine: made on use
    assert slot_problem(root, str(root)) is None
    assert slot_problem(root, str(tmp_path / "Trips")) == "Trips isn't inside this folder"
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


def test_move_outside_root_refused(root, tmp_path):
    _write(root / "a.jpg", b"aaa")
    (tmp_path / "other").mkdir()
    before = tree(tmp_path)
    session = PreviewSession(root, False)
    with pytest.raises(ValueError) as caught:
        session.move("a.jpg", tmp_path / "other")
    assert str(caught.value) == OUTSIDE
    (root / "out").symlink_to(tmp_path / "other", target_is_directory=True)
    with pytest.raises(ValueError):
        session.move("a.jpg", root / "out")
    (root / "out").unlink()
    assert tree(tmp_path) == before
    assert session.moved == []
    assert session.finish() is None
    assert load_runs(root) == []


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
    assert slot_problem(root, str(root / "Trips")) is None
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
    session = PreviewSession(root, True)
    session.move("a.jpg", root / NOT_NEEDED)
    session.move("sub/b.jpg", root / "Trips")
    session.put_back()
    run_id = session.finish()
    undo.undo_run(root, run_id)
    assert not [p for p in root.rglob("*") if p.name.casefold() == ".photosort"]
