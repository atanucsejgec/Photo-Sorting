"""Extension mode: every file into a folder named after its extension."""

import threading

import pytest

from photosort.modes import MODE_SPECS, MODES
from photosort.modes.extension_mode import (
    NO_EXTENSION, extension_folder, plan_extension, prepare_extension_plan,
)


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
])
def test_extension_folder(name, folder):
    assert extension_folder(name) == folder


def test_no_extension_name():
    assert NO_EXTENSION == "No extension"


def test_the_mode_is_listed_after_the_others():
    assert MODES[-1] == ("extension", "Extension", True)
    spec = MODE_SPECS["extension"]
    assert spec.kind == "files"
    assert spec.warn_subfolders is True
    assert not any(s.warn_subfolders for key, s in MODE_SPECS.items() if key != "extension")


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
