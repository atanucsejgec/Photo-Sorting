"""No people, Extension and Date (all files) in the main window: the mode
list, the subfolders warning, the date choice, and each mode from Start to
Undo. Every message box is answered by the tests; none is ever shown."""

import hashlib
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox

from photosort.ai import models
from photosort.faces import Face
from photosort.modes import MODES
from photosort.people import PeopleStore
from photosort.settings import Settings, load_settings
from photosort.ui import main_window
from photosort.ui.main_window import MainWindow

WARNING = ("Include subfolders?",
           "Files inside folders may belong to programs or projects. "
           "Moving them can break them. Continue?")
CHOICE = ("Which date?",
          "Sort files by the date they were last modified, or the date they were created? "
          "Photos and videos use the date they were taken.")


@pytest.fixture
def settings_path(tmp_path):
    return tmp_path / "config" / "settings.json"


@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


class Boxes:
    """QMessageBox.exec, patched: each box is recorded, then answered by
    clicking the button named next in answers ("Esc": its escape button).
    A box with no answer left fails the test instead of opening."""

    def __init__(self) -> None:
        self.answers: list[str] = []
        self.shown: list[dict] = []

    def exec(self, box: QMessageBox) -> int:
        buttons = {b.text(): b for b in box.buttons()}
        self.shown.append({
            "title": box.windowTitle(),
            "text": box.text(),
            "buttons": sorted(buttons),
            "default": box.defaultButton().text() if box.defaultButton() else None,
            "escape": box.escapeButton().text() if box.escapeButton() else None,
        })
        assert self.answers, f"an unexpected message box: {box.text()!r}"
        answer = self.answers.pop(0)
        (box.escapeButton() if answer == "Esc" else buttons[answer]).click()
        return 0


@pytest.fixture
def boxes(monkeypatch):
    found = Boxes()
    monkeypatch.setattr(QMessageBox, "exec", lambda box: found.exec(box))
    return found


@pytest.fixture
def make_window(qtbot, tmp_path, settings_path, errors, boxes):
    windows = []

    def _make(settings: Settings | None = None) -> MainWindow:
        w = MainWindow(settings or Settings(), settings_path,
                       people_store=PeopleStore(tmp_path / "people"))
        qtbot.addWidget(w)
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "files"
    path.mkdir()
    return path


def _write(path: Path, data: bytes, when: datetime | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if when is not None:
        os.utime(path, (when.timestamp(), when.timestamp()))


def _hashes(root: Path) -> dict[str, str]:
    """Every file under root (but the app's own .photosort), by path, to its sha256."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file() and ".photosort" not in p.relative_to(root).parts}


def _start(w: MainWindow, folder: Path, mode: str, subfolders: bool = False) -> None:
    w.folder_edit.setText(str(folder))
    w.subfolders_check.setChecked(subfolders)
    w.mode_buttons[mode].setChecked(True)
    w.start_button.click()


def _go_and_undo(qtbot, w: MainWindow, folder: Path, before: dict[str, str]) -> None:
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert _hashes(folder) != before
    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _hashes(folder) == before
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        {rel.split("/")[0] for rel in before} | {".photosort"})


# ----- the mode list -----------------------------------------------------------

def test_the_new_modes_are_listed(make_window):
    w = make_window()
    for key, label in (("no_people", "No people"), ("extension", "Extension"),
                       ("all_dates", "Date (all files)")):
        assert w.mode_buttons[key].isEnabled()
        assert w.mode_buttons[key].text() == label
    assert sorted(w.mode_buttons) == sorted(key for key, _, _ in MODES)


def test_every_mode_label_shows_its_ampersand(make_window):
    w = make_window()
    for key, label, _ in MODES:
        text = w.mode_buttons[key].text()
        assert QKeySequence.mnemonic(text).isEmpty(), text  # no letter underlined
        assert text.replace("&&", "&") == label
    assert w.mode_buttons["notes"].text() == "Notes && Pages"  # shown as "Notes & Pages"


# ----- Extension ---------------------------------------------------------------

def _extension_files(folder: Path) -> None:
    _write(folder / "a.pdf", b"a")
    _write(folder / "b.pdf", b"b")
    _write(folder / "c.apk", b"c")


def test_extension_without_subfolders_asks_nothing_and_undoes(qtbot, make_window, folder,
                                                             boxes, errors):
    _extension_files(folder)
    before = _hashes(folder)
    w = make_window()
    _start(w, folder, "extension")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert boxes.shown == []
    assert w.summary_label.text().splitlines()[0] == "3 files → 2 folders"
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert (folder / "PDF" / "a.pdf").is_file() and (folder / "APK" / "c.apk").is_file()
    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _hashes(folder) == before
    assert not (folder / "PDF").exists() and not (folder / "APK").exists()
    assert errors == []


def test_extension_with_subfolders_warns_and_cancel_goes_home(qtbot, make_window, folder,
                                                             boxes, errors):
    _extension_files(folder)
    _write(folder / "app" / "lib.dll", b"d")
    before = _hashes(folder)
    w = make_window()
    boxes.answers = ["Cancel"]
    _start(w, folder, "extension", subfolders=True)
    assert boxes.shown == [{"title": WARNING[0], "text": WARNING[1],
                            "buttons": ["Cancel", "Continue"],
                            "default": "Cancel", "escape": "Cancel"}]
    assert w.current_page() is w.home
    assert w._plan is None
    assert _hashes(folder) == before
    assert errors == []


def test_extension_with_subfolders_esc_goes_home(qtbot, make_window, folder, boxes):
    _extension_files(folder)
    w = make_window()
    boxes.answers = ["Esc"]
    _start(w, folder, "extension", subfolders=True)
    assert w.current_page() is w.home
    assert w._plan is None


def test_extension_with_subfolders_continue_sorts_them_too(qtbot, make_window, folder,
                                                          boxes, errors):
    _extension_files(folder)
    _write(folder / "app" / "lib.dll", b"d")
    before = _hashes(folder)
    w = make_window()
    boxes.answers = ["Continue"]
    _start(w, folder, "extension", subfolders=True)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert [box["title"] for box in boxes.shown] == [WARNING[0]]
    assert w.summary_label.text().splitlines()[0] == "4 files → 3 folders"
    _go_and_undo(qtbot, w, folder, before)
    assert errors == []


def test_back_on_the_extension_summary_goes_home(qtbot, make_window, folder):
    _extension_files(folder)
    w = make_window()
    _start(w, folder, "extension")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.back_button.click()
    assert w.current_page() is w.home


# ----- Date (all files) --------------------------------------------------------

MARCH_5 = datetime(2024, 3, 5, 12, 0)


def _dated_files(folder: Path) -> None:
    for name in ("a.txt", "b.apk", "c.pdf"):
        _write(folder / name, name.encode(), MARCH_5)


def test_all_dates_warns_then_asks_and_esc_goes_home(qtbot, make_window, folder, boxes,
                                                    settings_path, errors):
    _dated_files(folder)
    before = _hashes(folder)
    w = make_window()
    boxes.answers = ["Continue", "Esc"]
    _start(w, folder, "all_dates", subfolders=True)
    assert boxes.shown == [
        {"title": WARNING[0], "text": WARNING[1], "buttons": ["Cancel", "Continue"],
         "default": "Cancel", "escape": "Cancel"},
        {"title": CHOICE[0], "text": CHOICE[1], "buttons": ["Cancel", "Created", "Modified"],
         "default": "Modified", "escape": "Cancel"},
    ]
    assert w.current_page() is w.home
    assert w._plan is None
    assert _hashes(folder) == before
    assert load_settings(settings_path).file_date == "modified"
    assert errors == []


def test_all_dates_cancel_at_the_warning_asks_no_more(qtbot, make_window, folder, boxes):
    _dated_files(folder)
    w = make_window()
    boxes.answers = ["Cancel"]
    _start(w, folder, "all_dates", subfolders=True)
    assert [box["title"] for box in boxes.shown] == [WARNING[0]]
    assert w.current_page() is w.home
    assert w._plan is None


def test_all_dates_without_subfolders_only_asks_the_date(qtbot, make_window, folder, boxes,
                                                        errors):
    _dated_files(folder)
    before = _hashes(folder)
    w = make_window()
    boxes.answers = ["Modified"]
    _start(w, folder, "all_dates")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert [box["title"] for box in boxes.shown] == [CHOICE[0]]
    assert w.summary_label.text().splitlines()[0] == (
        "3 files → 1 month folders, 1 day folders")
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    day = folder / "2024" / "03-March" / "05-03-2024"
    assert sorted(p.name for p in day.iterdir()) == ["a.txt", "b.apk", "c.pdf"]
    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _hashes(folder) == before
    assert not (folder / "2024").exists()
    assert errors == []


def test_all_dates_remembers_the_date_chosen(qtbot, make_window, folder, boxes,
                                            settings_path, errors):
    _dated_files(folder)
    before = _hashes(folder)
    w = make_window()
    boxes.answers = ["Created"]
    _start(w, folder, "all_dates")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.settings.file_date == "created"
    assert load_settings(settings_path).file_date == "created"
    w.back_button.click()
    assert w.current_page() is w.home

    boxes.answers = ["Esc"]
    w.start_button.click()
    assert boxes.shown[-1]["title"] == CHOICE[0]
    assert boxes.shown[-1]["default"] == "Created"
    assert w.current_page() is w.home
    assert load_settings(settings_path).file_date == "created"  # Esc changes nothing

    # A new window, as the next time the app starts: Created comes first again.
    w2 = make_window(load_settings(settings_path))
    boxes.answers = ["Created"]
    _start(w2, folder, "all_dates")
    qtbot.waitUntil(lambda: w2.current_page() is w2.summary_page)
    assert boxes.shown[-1]["default"] == "Created"
    _go_and_undo(qtbot, w2, folder, before)
    assert errors == []


def test_all_dates_with_subfolders_sorts_and_undoes(qtbot, make_window, folder, boxes,
                                                   errors):
    _dated_files(folder)
    _write(folder / "project" / "notes.md", b"n", MARCH_5)
    before = _hashes(folder)
    w = make_window()
    boxes.answers = ["Continue", "Modified"]
    _start(w, folder, "all_dates", subfolders=True)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines()[0] == (
        "4 files → 1 month folders, 1 day folders")
    _go_and_undo(qtbot, w, folder, before)
    assert errors == []


# ----- No people ---------------------------------------------------------------

def _face() -> Face:
    v = np.zeros(512, np.float32)
    v[0] = 1.0
    return Face((10.0, 20.0, 50.0, 70.0), np.zeros((5, 2), np.float32), 0.9, v, (16, 16))


class FakeEngine:
    """A face engine that knows each photo's faces by file name (none if not
    named); an Exception there is raised instead."""

    def __init__(self, faces: dict[str, list[Face] | Exception]) -> None:
        self.faces = faces
        self.calls: list[str] = []

    def analyse(self, path: Path) -> list[Face]:
        self.calls.append(Path(path).name)
        found = self.faces.get(Path(path).name, [])
        if isinstance(found, Exception):
            raise found
        return list(found)


def test_no_people_asks_for_the_models_and_a_no_goes_home(qtbot, make_window, folder,
                                                         make_jpeg, tmp_path, monkeypatch,
                                                         errors):
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path / "models")  # none there
    asked = []

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    make_jpeg(folder / "a.jpg")
    made = []
    w = make_window()
    w.engine_factory = lambda use_gpu: made.append(use_gpu)
    _start(w, folder, "no_people")
    assert asked == [
        "Photo Sorter needs to download the face models once (180 MB). Download now?"]
    assert w.current_page() is w.home
    assert w._plan is None
    assert made == []
    assert (folder / "a.jpg").is_file()
    assert errors == []


def test_no_people_sorts_and_undoes(qtbot, make_window, folder, make_jpeg, monkeypatch,
                                    boxes, errors):
    monkeypatch.setattr(main_window, "ensure_models", lambda parent, keys: True)
    for name in ("view.jpg", "sky.jpg", "me.jpg"):
        make_jpeg(folder / name)
    _write(folder / "clip.txt", b"t")
    before = _hashes(folder)
    engine = FakeEngine({"me.jpg": [_face()]})
    w = make_window()
    w.engine_factory = lambda use_gpu: engine
    _start(w, folder, "no_people", subfolders=True)  # no warning for No people
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert boxes.shown == []
    assert w.summary_label.text().splitlines() == [
        "2 photos without faces → No people", "1 photos with people stay"]
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert sorted(p.name for p in (folder / "No people").iterdir()) == ["sky.jpg", "view.jpg"]
    assert (folder / "me.jpg").is_file()
    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _hashes(folder) == before
    assert not (folder / "No people").exists()

    w.home_button.click()
    w.start_button.click()  # again: the faces now come from the folder's cache
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert sorted(engine.calls) == ["me.jpg", "sky.jpg", "view.jpg"]
    w.back_button.click()
    assert w.current_page() is w.home
    assert errors == []


def test_no_people_with_a_broken_engine_plans_nothing(qtbot, make_window, folder, make_jpeg,
                                                      monkeypatch, errors):
    monkeypatch.setattr(main_window, "ensure_models", lambda parent, keys: True)
    names = [f"{n}.jpg" for n in range(7)]
    for name in names:
        make_jpeg(folder / name)
    before = _hashes(folder)
    w = make_window()
    w.engine_factory = lambda use_gpu: FakeEngine(
        {name: RuntimeError("model trouble") for name in names})
    _start(w, folder, "no_people")
    qtbot.waitUntil(lambda: bool(errors))
    assert errors == ["Could not check the files: model trouble"]
    assert w.current_page() is w.home
    assert w._plan is None
    assert _hashes(folder) == before
