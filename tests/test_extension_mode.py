"""Extension mode: every file into a folder named after its extension."""

import os
import threading

import pytest
from fstree import tree

from photosort.executor import execute
from photosort.journal import RunJournal
from photosort.modes import MODE_SPECS, MODES
from photosort.modes.extension_mode import (
    NO_EXTENSION, extension_folder, plan_extension, prepare_extension_plan,
)
from photosort.undo import undo_run


def _touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _dsts(plan):
    return {m.src: m.dst for m in plan.moves}


@pytest.mark.parametrize("name, folder", [
    ("a.apk", "APK"),
    ("B.Exe", "EXE"),
    ("x.tar.gz", "GZ"),
    ("photo.JPG", "JPG"),
    ("c++.h-pp", "H-PP"),
    ("x.7z", "7Z"),
    ("a.0123456789", "0123456789"),
    ("README", "No extension"),
    ("file.", "No extension"),
    ("report.final version", "No extension"),
    ("café.ñ", "No extension"),
    ("a.verylongextension", "No extension"),
    ("a.01234567890", "No extension"),
    # Windows' reserved names, and the folders the scanner skips, are made safe.
    ("thesis.aux", "_AUX"),
    ("a.con", "_CON"),
    ("a.Prn", "_PRN"),
    ("a.nul", "_NUL"),
    ("a.com1", "_COM1"),
    ("a.lpt9", "_LPT9"),
    ("x.duplicates", "_DUPLICATES"),
    ("a.com0", "COM0"),
])
def test_extension_folder(name, folder):
    assert extension_folder(name) == folder


def test_no_extension_name():
    assert NO_EXTENSION == "No extension"


def test_the_mode_is_listed_after_the_others():
    assert MODES[-2] == ("extension", "Extension", True)
    spec = MODE_SPECS["extension"]
    assert spec.kind == "files"
    assert spec.warn_subfolders is True
    assert not any(s.warn_subfolders for key, s in MODE_SPECS.items()
                   if key not in ("extension", "all_dates"))


def test_plan_and_summary(tmp_path):
    for name in ("a.apk", "b.APK", "setup.exe", "notes", "doc.pdf"):
        _touch(tmp_path / name)
    plan = prepare_extension_plan(tmp_path, False)
    assert plan.mode == "extension"
    assert _dsts(plan) == {
        "a.apk": "APK/a.apk", "b.APK": "APK/b.APK", "setup.exe": "EXE/setup.exe",
        "notes": "No extension/notes", "doc.pdf": "PDF/doc.pdf"}
    assert plan.summary == ["5 files → 4 folders"]
    assert plan.unchanged == 0


def test_case_twins_share_one_folder_and_never_collide(tmp_path, case_sensitive_fs):
    # Review Focus 1
    _touch(tmp_path / "PHOTO.JPG", b"1")
    _touch(tmp_path / "photo.jpg", b"2")
    plan = prepare_extension_plan(tmp_path, False)
    dsts = sorted(m.dst for m in plan.moves)
    assert len(dsts) == 2
    assert {d.split("/")[0] for d in dsts} == {"JPG"}
    assert len({d.casefold() for d in dsts}) == 2
    assert dsts == ["JPG/PHOTO.JPG", "JPG/photo (1).jpg"]
    assert plan.summary == ["2 files → 1 folders"]


def test_same_name_from_two_subfolders_never_collides(tmp_path):
    _touch(tmp_path / "a" / "x.apk", b"1")
    _touch(tmp_path / "b" / "X.APK", b"2")
    plan = prepare_extension_plan(tmp_path, True)
    assert _dsts(plan) == {"a/x.apk": "APK/x.apk", "b/X.APK": "APK/X (1).APK"}


def test_a_folder_in_another_case_counts_as_in_place(tmp_path):
    # Review Focus 2
    _touch(tmp_path / "apk" / "x.apk")
    plan = prepare_extension_plan(tmp_path, True)
    assert plan.moves == []
    assert plan.unchanged == 1
    assert plan.summary == ["1 already in place"]


def test_a_new_file_joins_the_folder_spelt_as_on_disk(tmp_path):
    _touch(tmp_path / "apk" / "x.apk")
    _touch(tmp_path / "y.apk")
    plan = prepare_extension_plan(tmp_path, True)
    assert _dsts(plan) == {"y.apk": "apk/y.apk"}  # no second APK folder
    assert plan.summary == ["1 files → 1 folders", "1 already in place"]


def test_a_name_taken_in_the_folder_gets_another(tmp_path):
    _touch(tmp_path / "APK" / "x.apk", b"old")
    _touch(tmp_path / "x.apk", b"new")
    plan = prepare_extension_plan(tmp_path, False)  # APK/ itself is not scanned
    assert _dsts(plan) == {"x.apk": "APK/x (1).apk"}


def test_no_extension_files_in_place(tmp_path):
    _touch(tmp_path / "No extension" / "README")
    _touch(tmp_path / "LICENSE")
    plan = prepare_extension_plan(tmp_path, True)
    assert _dsts(plan) == {"LICENSE": "No extension/LICENSE"}
    assert plan.summary == ["1 files → 1 folders", "1 already in place"]


def test_a_file_in_a_deeper_folder_of_that_name_moves_to_the_top(tmp_path):
    _touch(tmp_path / "sub" / "APK" / "x.apk")
    plan = prepare_extension_plan(tmp_path, True)
    assert _dsts(plan) == {"sub/APK/x.apk": "APK/x.apk"}


def test_files_without_extension_move_first(tmp_path):
    # A file "PDF" is in the way of the PDF folder until it has gone to No extension.
    _touch(tmp_path / "PDF")
    _touch(tmp_path / "a.pdf")
    _touch(tmp_path / "Z.pdf")
    plan = prepare_extension_plan(tmp_path, False)
    assert plan.moves[0].src == "PDF"
    assert _dsts(plan) == {"PDF": "No extension/PDF", "Z.pdf": "PDF/Z.pdf",
                           "a.pdf": "PDF/a.pdf"}


def test_plan_extension_takes_the_files_given(tmp_path):
    _touch(tmp_path / "a.zip")
    _touch(tmp_path / "b.zip")
    plan = plan_extension(tmp_path, ["a.zip"])
    assert _dsts(plan) == {"a.zip": "ZIP/a.zip"}


def test_clutter_is_left_alone(tmp_path):
    _touch(tmp_path / "keep.iso")
    _touch(tmp_path / "desktop.ini")
    _touch(tmp_path / "film.mkv.part")
    plan = prepare_extension_plan(tmp_path, False)
    assert _dsts(plan) == {"keep.iso": "ISO/keep.iso"}


def test_nothing_to_sort(tmp_path):
    plan = prepare_extension_plan(tmp_path, True)
    assert plan.moves == [] and plan.summary == [] and plan.unchanged == 0


def test_cancel(tmp_path):
    _touch(tmp_path / "a.zip")
    cancel = threading.Event()
    cancel.set()
    assert prepare_extension_plan(tmp_path, False, cancel=cancel) is None


def test_a_safe_folder_counts_as_in_place(tmp_path):
    _touch(tmp_path / "_AUX" / "a.aux")
    _touch(tmp_path / "b.AUX")
    plan = prepare_extension_plan(tmp_path, True)
    assert _dsts(plan) == {"b.AUX": "_AUX/b.AUX"}
    assert plan.unchanged == 1


def test_progress_while_planning(tmp_path):
    for name in ("a.zip", "b.exe", "c"):
        _touch(tmp_path / name)
    calls = []
    prepare_extension_plan(tmp_path, False, progress=lambda i, total: calls.append((i, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]


def _contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


def _sort(root, include_subfolders=False):
    plan = prepare_extension_plan(root, include_subfolders)
    journal = RunJournal.create(root, "extension", include_subfolders, len(plan.moves))
    result = execute(root, plan, journal)
    return plan, result, journal.run_id


@pytest.mark.parametrize("names", [["PDF", "a.pdf", "Z.pdf"], ["pdf", "a.pdf"]])
def test_undo_puts_a_file_back_where_its_folder_was_made(tmp_path, names):
    for name in names:
        _touch(tmp_path / name, name.encode())
    before = _contents(tmp_path)
    plan, result, run_id = _sort(tmp_path)
    assert result.moved == len(names)
    assert not result.skipped and not result.cancelled
    undone = undo_run(tmp_path, run_id)
    assert undone.renamed == [] and undone.failed == []
    assert _contents(tmp_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*names, ".photosort"])


def test_a_root_file_called_no_extension_stays(tmp_path):
    _touch(tmp_path / "no Extension", b"in the way")
    _touch(tmp_path / "README", b"r")
    _touch(tmp_path / "a.txt", b"t")
    plan, result, _ = _sort(tmp_path)
    # The No extension folder can't be made, so the files with no extension stay.
    assert _dsts(plan) == {"a.txt": "TXT/a.txt"}
    assert plan.unchanged == 0
    assert plan.summary == ["1 files → 1 folders",
                            "2 files stay: a file or link with their folder's name is in the way"]
    assert result.moved == 1
    assert (tmp_path / "no Extension").read_bytes() == b"in the way"
    assert (tmp_path / "README").is_file()


def test_a_no_extension_folder_in_another_case_is_used(tmp_path):
    _touch(tmp_path / "no extension" / "LICENSE")
    _touch(tmp_path / "README")
    plan = prepare_extension_plan(tmp_path, False)
    assert _dsts(plan) == {"README": "no extension/README"}


def test_a_file_that_stays_in_the_way_of_a_folder_keeps_its_files_in_place(tmp_path):
    # "No extension" can't be made, so the file "PDF" stays; the PDF folder
    # can't be made either, so a.pdf stays too. The run never stops half-way.
    for name in ("No extension", "PDF", "a.pdf", "b.zip"):
        _touch(tmp_path / name, name.encode())
    before = _contents(tmp_path)
    plan, result, run_id = _sort(tmp_path)
    assert _dsts(plan) == {"b.zip": "ZIP/b.zip"}
    assert plan.unchanged == 0
    assert plan.summary == ["1 files → 1 folders",
                            "3 files stay: a file or link with their folder's name is in the way"]
    assert result.moved == 1 and not result.skipped
    undone = undo_run(tmp_path, run_id)
    assert undone.renamed == [] and undone.failed == []
    assert _contents(tmp_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [".photosort", "No extension", "PDF", "a.pdf", "b.zip"])


def test_a_symlink_in_the_way_of_a_folder_keeps_its_files_in_place(tmp_path):
    (tmp_path / "elsewhere.txt").write_bytes(b"e")
    os.symlink(tmp_path / "elsewhere.txt", tmp_path / "pdf")  # the scan skips symlinks
    _touch(tmp_path / "a.pdf")
    plan = prepare_extension_plan(tmp_path, False)
    assert "a.pdf" not in _dsts(plan)
    assert "1 files stay: a file or link with their folder's name is in the way" in plan.summary


@pytest.mark.parametrize("link", ["PDF", "pdf"])
def test_nothing_moves_through_a_link_to_a_folder(tmp_path, link):
    root, outside = tmp_path / "root", tmp_path / "outside"
    _touch(outside / "kept.txt", b"k")
    _touch(root / "a.pdf", b"a")
    _touch(root / "b.zip", b"b")
    os.symlink(outside, root / link, target_is_directory=True)
    before, outside_before = tree(root), tree(outside)
    plan, result, run_id = _sort(root)
    assert _dsts(plan) == {"b.zip": "ZIP/b.zip"}
    assert plan.summary == ["1 files → 1 folders", "1 files stay: a file or link with their folder's name is in the way"]
    assert result.moved == 1 and not result.skipped
    assert tree(outside) == outside_before
    undone = undo_run(root, run_id)
    assert undone.renamed == [] and undone.failed == []
    assert tree(root) == before
    assert tree(outside) == outside_before


def test_a_link_beside_a_real_folder_of_that_name_still_stops_it(tmp_path,
                                                                  case_sensitive_fs):
    # A real "pdf" folder and a link "PDF": the link could be taken for the folder.
    (tmp_path / "outside").mkdir()
    (tmp_path / "root" / "pdf").mkdir(parents=True)
    os.symlink(tmp_path / "outside", tmp_path / "root" / "PDF", target_is_directory=True)
    _touch(tmp_path / "root" / "a.pdf")
    plan = prepare_extension_plan(tmp_path / "root", False)
    assert plan.moves == []
    assert plan.summary == ["1 files stay: a file or link with their folder's name is in the way"]


def test_a_real_folder_of_that_exact_name_is_used_beside_a_link(tmp_path, case_sensitive_fs):
    # A real "PDF" folder and a link "pdf": the real folder is the one used,
    # as in Date (all files), and nothing goes through the link.
    root, outside = tmp_path / "root", tmp_path / "outside"
    _touch(outside / "kept.txt", b"k")
    (root / "PDF").mkdir(parents=True)
    os.symlink(outside, root / "pdf", target_is_directory=True)
    _touch(root / "a.pdf", b"a")
    before, outside_before = tree(root), tree(outside)
    plan, result, run_id = _sort(root)
    assert _dsts(plan) == {"a.pdf": "PDF/a.pdf"}
    assert plan.summary == ["1 files → 1 folders"]
    assert result.moved == 1 and not result.skipped
    assert (root / "PDF" / "a.pdf").read_bytes() == b"a"
    assert tree(outside) == outside_before
    undone = undo_run(root, run_id)
    assert undone.renamed == [] and undone.failed == []
    assert tree(root) == before
