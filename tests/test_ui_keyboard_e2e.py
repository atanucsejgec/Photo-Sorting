"""The whole app by keyboard alone, one run per family of flows: from the
folder typed on Home, through each review, the summary (Go has the focus),
Done (Home has the focus) and Undo; History's Undo; Settings and the
category editor; Face setup and the naming dialog. After the window is
shown, only keys are pressed (qtbot.keyClick(s), and held keys sent as
their auto-repeats); there is no .click(). The photos are tmp files and the
engines fakes.

Then: a held Enter on the summary moves the files once (Review Focus 3); a
held or modified Enter never presses Go; each page's and each in-app
dialog's strip lists its live keys; every dialog fits the smallest window.
"""

import math
import threading
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

from photosort.faces import Face
from photosort.groups import Candidate, Group
from photosort.journal import load_runs
from photosort.modes import content
from photosort.modes import events as events_mode
from photosort.modes._clip_scan import ClipScan
from photosort.modes.content import Verdict
from photosort.modes.events import Event
from photosort.modes.face_mode import FaceScan, Question
from photosort.people import PeopleStore
from photosort.settings import DEFAULT_CONTENT_CATEGORIES, Settings, load_settings
from photosort.ui import dialogs, main_window, model_download, shortcuts
from photosort.ui.category_editor import CategoryEditor, CategoryNameDialog
from photosort.ui.face_naming_dialog import FaceNamingDialog
from photosort.ui.flows import bad as bad_flow
from photosort.ui.flows import clip as clip_flow
from photosort.ui.flows import events as events_flow
from photosort.ui.flows import face as face_flow
from photosort.ui.flows import groups as groups_flow
from photosort.ui.fullscreen_viewer import FullscreenViewer
from photosort.ui.home_page import TILES, ModeTile
from photosort.ui.main_window import MainWindow
from photosort.ui.photo_preview import PhotoPreview
from photosort.ui.shortcuts import ShortcutStrip

CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier
EXIF = "2025:02:20 10:00:00"
TAB = "Tab/Shift+Tab area"
SMALLEST = (819, 480)  # 1024 x 600 at 125 %


# ----- the window and the keys ---------------------------------------------------------

@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def models_present(monkeypatch):
    monkeypatch.setattr(main_window, "ensure_models", lambda parent, keys: True)


@pytest.fixture
def settings_path(tmp_path) -> Path:
    return tmp_path / "config" / "settings.json"


@pytest.fixture
def make_window(qtbot, tmp_path, settings_path, errors, models_present):
    windows = []

    def _make(settings: Settings | None = None, size=(1280, 800)) -> MainWindow:
        w = MainWindow(settings or Settings(), settings_path,
                       people_store=PeopleStore(tmp_path / "people"))
        qtbot.addWidget(w)
        w.resize(*size)
        w.show()
        qtbot.waitExposed(w)
        windows.append(w)
        _activate(qtbot, w)
        qtbot.waitUntil(lambda: isinstance(_focused(), ModeTile))
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def photos(tmp_path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def _focused():
    return QApplication.focusWidget()


def _key(qtbot, key, modifier=NONE) -> None:
    """Press key where the focus is, as the owner would."""
    qtbot.keyClick(_focused(), key, modifier)


def _hold(key, times: int = 3, modifier=NONE) -> None:
    """Hold key down a while longer: its auto-repeats, where the focus is
    (through the shortcut map, as a real held key goes)."""
    text = "\r" if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) else ""
    for _ in range(times):
        QTest.simulateEvent(_focused(), True, key, modifier, text, True, -1)
        QTest.simulateEvent(_focused(), False, key, modifier, text, True, -1)


def _activate(qtbot, window) -> None:
    """Make window the active one, as a window manager does when a dialog
    opens or closes (offscreen, nothing does)."""
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window)


_waiting: list[dict] = []  # the _in_dialog calls of this test still waiting


@pytest.fixture(autouse=True)
def _no_waiting_after_the_test():
    """A dialog that never came: its waiting ends with the test (and so never
    answers the next test's dialogs)."""
    yield
    for waiting in _waiting:
        waiting["over"] = True
    _waiting.clear()


def _in_dialog(qtbot, act, kind=QDialog) -> None:
    """Once the next modal dialog of kind shows, activate it (as a window
    manager would) and call act(dialog) there; a failure closes it."""
    waiting = {"over": False}
    _waiting.append(waiting)

    def poll():
        if waiting["over"]:
            return
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, kind) or not dialog.isVisible():
            QTimer.singleShot(10, poll)
            return
        if QApplication.activeWindow() is not dialog:
            dialog.activateWindow()
            QTimer.singleShot(10, poll)
            return
        waiting["over"] = True
        try:
            act(dialog)
        except BaseException:
            dialog.reject()
            raise

    QTimer.singleShot(0, poll)


def _press_in_dialog(qtbot, seen: list, *keys, kind=QDialog) -> None:
    """In the next dialog: note its title, focused button and strip, then press keys."""

    def act(dialog):
        qtbot.waitUntil(lambda: _focused() is not None and _focused().window() is dialog)
        seen.append((dialog.windowTitle(), getattr(_focused(), "text", lambda: "")(),
                     dialog.findChild(ShortcutStrip).text()))
        for key in keys:
            _key(qtbot, key)

    _in_dialog(qtbot, act, kind)


def _type_folder(qtbot, w: MainWindow, folder: Path) -> None:
    """From the tiles, Shift+Tab back to the folder field, type folder and Enter."""
    for _ in range(8):
        if _focused() is w.folder_edit:
            break
        _key(qtbot, Qt.Key.Key_Backtab)
    assert _focused() is w.folder_edit
    w.folder_edit.selectAll()
    qtbot.keyClicks(w.folder_edit, str(folder))
    _key(qtbot, Qt.Key.Key_Return)  # the folder is used, and the focus is on the tiles
    assert isinstance(_focused(), ModeTile)


def _to_tile(qtbot, mode: str) -> None:
    """Arrow from the focused tile to mode's."""
    keys = [key for key, _ in TILES]
    step = keys.index(mode) - keys.index(_focused().key)
    for _ in range(abs(step)):
        _key(qtbot, Qt.Key.Key_Right if step > 0 else Qt.Key.Key_Left)
    assert _focused().key == mode


def _start_by_keyboard(qtbot, w: MainWindow, folder: Path, mode: str) -> None:
    _type_folder(qtbot, w, folder)
    _to_tile(qtbot, mode)
    _key(qtbot, Qt.Key.Key_Return)


def _on_summary(qtbot, w: MainWindow) -> None:
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    qtbot.waitUntil(lambda: _focused() is w.go_button)
    assert w.strip.text() == f"Enter go · ←→ switch buttons · Esc back · {TAB}"


def _go(qtbot, w: MainWindow) -> None:
    """Enter on the summary (Go focused): Done, Home focused."""
    _on_summary(qtbot, w)
    _key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    qtbot.waitUntil(lambda: _focused() is w.home_button)


def _files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                  if p.is_file() and ".photosort" not in p.parts)


def _jpeg(path: Path, size=(64, 48), colour="gray") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, "JPEG")
    return path


# ----- Date (a plain mode), Done and U ------------------------------------------------

@pytest.mark.real_dialogs
def test_date_by_keyboard_then_undo(qtbot, make_window, photos, make_jpeg, errors):
    names = ["a.jpg", "b.jpg"]
    for name in names:
        make_jpeg(photos / name, EXIF)
    w = make_window(Settings(last_mode="camera"))  # the focus starts on Camera
    assert _focused() is w.mode_buttons["camera"]
    _start_by_keyboard(qtbot, w, photos, "date")
    _go(qtbot, w)
    assert "Moved 2 files." in w.done_label.text()
    assert not any((photos / n).exists() for n in names)
    assert w.strip.text() == f"Enter home · ←→ switch buttons · U undo · Esc home · {TAB}"

    seen = []
    _press_in_dialog(qtbot, seen, Qt.Key.Key_Right, Qt.Key.Key_Return)  # Undo, not Cancel
    _key(qtbot, Qt.Key.Key_U)
    assert seen == [("Undo this sort?", "Cancel", "←→ choose · Enter pick · Esc no/close")]
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert all((photos / n).is_file() for n in names)
    assert not (photos / "2025").exists()
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is w.home_button)
    assert "U undo" not in w.strip.text()  # nothing left to undo
    _key(qtbot, Qt.Key.Key_Return)  # Home
    assert w.current_page() is w.home
    assert errors == []


@pytest.mark.real_dialogs
def test_u_asks_first_and_enter_keeps_the_sort(qtbot, make_window, photos, make_jpeg):
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "date")
    _go(qtbot, w)
    seen = []
    _press_in_dialog(qtbot, seen, Qt.Key.Key_Return)  # the default: Cancel
    _key(qtbot, Qt.Key.Key_U)
    assert [focus for _, focus, _ in seen] == ["Cancel"]
    qtbot.wait(50)
    assert not (photos / "a.jpg").exists()  # still sorted
    assert w.undo_button.isEnabled()
    _activate(qtbot, w)
    _key(qtbot, Qt.Key.Key_Escape)  # Esc on Done: Home
    assert w.current_page() is w.home


def test_the_summary_by_keyboard(qtbot, make_window, photos, make_jpeg):
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "date")
    _on_summary(qtbot, w)
    assert w.summary_page.heading_label.text() == "Ready to sort by Date"
    _key(qtbot, Qt.Key.Key_Left)
    assert _focused() is w.back_button
    assert w.strip.text().startswith("Enter back · ")
    _key(qtbot, Qt.Key.Key_Left)
    assert _focused() is w.show_moves_button
    _key(qtbot, Qt.Key.Key_Left)  # the first: stays
    assert _focused() is w.show_moves_button
    _key(qtbot, Qt.Key.Key_Return)  # Show all moves
    assert w.moves_table.isVisible()
    assert w.moves_table.item(0, 1).text() == "2025/02-February/a.jpg"
    _key(qtbot, Qt.Key.Key_Tab)
    _key(qtbot, Qt.Key.Key_Tab)
    _key(qtbot, Qt.Key.Key_Tab)  # Back, Go, then the list
    assert _focused() is w.moves_table
    assert w.strip.text() == f"↑↓ scroll · Esc back · {TAB}"
    _key(qtbot, Qt.Key.Key_Escape)  # back: Home, as Back
    assert w.current_page() is w.home
    assert (photos / "a.jpg").is_file()


def test_esc_cancels_the_progress(qtbot, make_window, photos, monkeypatch):
    from photosort.modes import date_mode

    started = threading.Event()

    def slow(root, include_subfolders, progress=None, cancel=None):
        started.set()
        cancel.wait(10)
        return None

    monkeypatch.setattr(date_mode, "prepare_date_plan", slow)
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "date")
    assert w.current_page() is w.progress_page
    qtbot.waitUntil(lambda: _focused() is w.cancel_button)
    assert w.strip.text() == "Esc cancel"
    assert started.wait(5)
    _key(qtbot, Qt.Key.Key_Return)  # Enter does not cancel
    assert w.cancel_button.isEnabled()
    _key(qtbot, Qt.Key.Key_Escape)
    assert w.progress_label.text() == "Stopping…"
    qtbot.waitUntil(lambda: w.current_page() is w.home)


# ----- held keys: Review Focus 3 and ruling 1 ----------------------------------------------

def test_a_held_enter_on_the_summary_moves_the_files_once(qtbot, make_window, photos,
                                                         make_jpeg, monkeypatch):
    make_jpeg(photos / "a.jpg", EXIF)
    real_execute = main_window.execute
    calls = []
    release = threading.Event()

    def execute(root, plan, journal, progress=None, cancel=None):
        calls.append(plan)
        assert release.wait(10)
        return real_execute(root, plan, journal, progress=progress, cancel=cancel)

    monkeypatch.setattr(main_window, "execute", execute)
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "date")
    _on_summary(qtbot, w)
    _hold(Qt.Key.Key_Return)  # an Enter held since the screen before: nothing
    qtbot.wait(20)
    assert calls == [] and w.current_page() is w.summary_page
    _key(qtbot, Qt.Key.Key_Return)  # Enter goes down: Go ...
    _hold(Qt.Key.Key_Return)  # ... and is held
    qtbot.waitUntil(lambda: calls != [])
    assert w.current_page() is w.progress_page
    _hold(Qt.Key.Key_Return)  # ... while the files move (Cancel has the focus)
    assert w.cancel_button.isEnabled()
    release.set()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    qtbot.waitUntil(lambda: _focused() is w.home_button)
    _hold(Qt.Key.Key_Return)  # ... and still, on Done
    qtbot.wait(50)
    assert len(calls) == 1
    assert w.current_page() is w.done_page  # Home not pressed, so no new sort
    assert len(load_runs(photos)) == 1
    assert "Moved 1 files." in w.done_label.text()
    _key(qtbot, Qt.Key.Key_Return)  # a fresh Enter: Home ...
    assert w.current_page() is w.home
    assert isinstance(_focused(), ModeTile)
    _hold(Qt.Key.Key_Return)  # ... held: the tile starts nothing
    qtbot.wait(50)
    assert w.current_page() is w.home
    assert len(calls) == 1


def _two_pairs(photos: Path, make_pattern_jpeg) -> None:
    """a.jpg and b.jpg the same, and c.jpg and d.jpg (two other photos)."""
    for seed, (first, copy) in enumerate((("a.jpg", "b.jpg"), ("c.jpg", "d.jpg"))):
        make_pattern_jpeg(photos / first, seed=seed, size=(160, 120))
        (photos / copy).write_bytes((photos / first).read_bytes())


def _review_to_summary(qtbot, w: MainWindow, photos: Path, make_pattern_jpeg) -> None:
    """Duplicates found, the first group confirmed, and Ctrl+Enter on the
    second: the summary, with something to move."""
    _two_pairs(photos, make_pattern_jpeg)
    _start_by_keyboard(qtbot, w, photos, "duplicates")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    qtbot.waitUntil(lambda: _focused() is w.review_page.cards[0])
    _key(qtbot, Qt.Key.Key_Right, CTRL)  # confirm group 1
    _key(qtbot, Qt.Key.Key_Return, CTRL)  # stop reviewing


def test_a_held_ctrl_enter_never_reaches_go(qtbot, make_window, photos, make_pattern_jpeg):
    w = make_window()
    _review_to_summary(qtbot, w, photos, make_pattern_jpeg)
    _on_summary(qtbot, w)
    _hold(Qt.Key.Key_Return, modifier=CTRL)  # Ctrl+Enter, held since the review
    _key(qtbot, Qt.Key.Key_Return, CTRL)  # or pressed again
    _key(qtbot, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qtbot.wait(50)
    assert w.current_page() is w.summary_page
    assert load_runs(photos) == []
    _key(qtbot, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)  # the keypad's Enter
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)


# ----- Duplicates (a review grid) ----------------------------------------------------------

def test_duplicates_by_keyboard(qtbot, make_window, photos, make_pattern_jpeg, errors):
    _two_pairs(photos, make_pattern_jpeg)
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "duplicates")
    page = w.review_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    qtbot.waitUntil(lambda: _focused() is page.cards[0])
    assert page.heading_label.text().startswith("Group 1 of 2")
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)  # keep both of group 1
    _key(qtbot, Qt.Key.Key_Right, CTRL)  # confirm: on to group 2
    assert page.heading_label.text().startswith("Group 2 of 2")
    second = [card.candidate.rel for card in page.cards]
    kept = [card.candidate.rel for card in page.cards if card.keep_check.isChecked()]
    _key(qtbot, Qt.Key.Key_Right, CTRL)  # as it is: the last group, so the summary
    _go(qtbot, w)
    [set_aside] = [rel for rel in second if rel not in kept]
    assert [rel for rel in _files(photos) if "/" in rel] == [f"Duplicates/{set_aside}"]
    assert errors == []


# ----- Face: setup, questions, summary, Go; naming -------------------------------------------

def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _towards(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    v = (1 - t) * a + t * b
    return (v / np.linalg.norm(v)).astype(np.float32)


def _face(embedding: np.ndarray, x: float = 20) -> Face:
    return Face((x, 20.0, x + 60, 80.0), np.zeros((5, 2), np.float32), 0.9,
                embedding, (200, 200))


class FakeEngine:
    """The faces in each photo, by file name."""

    def __init__(self, faces: dict[str, list[Face]]) -> None:
        self.faces = faces

    def analyse(self, path: Path) -> list[Face]:
        return list(self.faces.get(Path(path).name, []))


def test_face_by_keyboard(qtbot, make_window, photos, make_jpeg, errors):
    w = make_window()
    atanu = w.people_store.add("Atanu", _unit(1), Image.new("RGB", (40, 40), "blue"))
    make_jpeg(photos / "a.jpg", size=(200, 200))
    make_jpeg(photos / "b.jpg", size=(200, 200))
    unsure = _towards(atanu.embeddings[0], _unit(7), 0.7)
    w.engine_factory = lambda use_gpu: FakeEngine({"a.jpg": [_face(unsure)],
                                                   "b.jpg": [_face(atanu.embeddings[0])]})
    _start_by_keyboard(qtbot, w, photos, "face")
    setup = w.face_setup_page
    assert w.current_page() is setup
    qtbot.waitUntil(lambda: _focused() is setup.people_list)
    assert w.strip.text() == (f"↑↓ choose · Space tick · A add person · Delete forget · "
                              f"Esc back · {TAB}")  # no Enter: nobody ticked
    _key(qtbot, Qt.Key.Key_Space)  # tick Atanu
    assert setup.ticked_ids() == [atanu.id]
    assert w.strip.text() == (f"↑↓ choose · Space tick · Enter start · A add person · "
                              f"Delete forget · Esc back · {TAB}")
    _key(qtbot, Qt.Key.Key_Return)  # start sorting
    page = w.face_question_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    qtbot.waitUntil(lambda: _focused() is page.photo_view)
    assert page.heading_label.text().startswith("Is this Atanu? ")
    _key(qtbot, Qt.Key.Key_Y)  # the last question: the summary
    _go(qtbot, w)
    assert _files(photos) == ["Atanu/a.jpg", "Atanu/b.jpg"]
    assert errors == []


@pytest.mark.real_dialogs
def test_face_naming_by_keyboard(qtbot, make_window, photos, tmp_path, make_jpeg,
                                 monkeypatch, errors):
    """Nobody is remembered: A, the photo picked (the system picker, faked),
    the name typed and Enter: saved, and the sort starts (as a click would)."""
    zoe = _unit(3)
    picked = make_jpeg(tmp_path / "picked" / "zoe.jpg", size=(200, 200))
    make_jpeg(photos / "a.jpg", size=(200, 200))
    monkeypatch.setattr(face_flow, "pick_photo", lambda parent, title: picked)
    w = make_window()
    w.engine_factory = lambda use_gpu: FakeEngine({"zoe.jpg": [_face(zoe)],
                                                   "a.jpg": [_face(zoe)]})
    _start_by_keyboard(qtbot, w, photos, "face")
    setup = w.face_setup_page
    qtbot.waitUntil(lambda: _focused() is setup.add_button)  # nobody to list
    assert w.strip.text() == f"Enter add person · A add person · Esc back · {TAB}"
    seen = []

    def name_the_face(dialog: FaceNamingDialog):
        qtbot.waitUntil(lambda: dialog.rows and _focused() is dialog.rows[0].name_edit)
        seen.append(dialog.strip.text())
        qtbot.keyClicks(_focused(), "Zoe")
        _key(qtbot, Qt.Key.Key_Return)  # Save

    _in_dialog(qtbot, name_the_face, FaceNamingDialog)
    _key(qtbot, Qt.Key.Key_A)
    assert seen == [f"Enter save · Esc cancel · {TAB}"]
    assert [p.name for p in w.people_store.people()] == ["Zoe"]
    _activate(qtbot, w)
    _go(qtbot, w)  # a.jpg is surely Zoe: no questions
    assert _files(photos) == ["Zoe/a.jpg"]
    assert errors == []


def test_face_naming_tab_between_faces_and_esc_cancels(qtbot, tmp_path, make_jpeg):
    store = PeopleStore(tmp_path / "people")
    photo = make_jpeg(tmp_path / "two.jpg", size=(200, 200))
    dialog = FaceNamingDialog(photo, FakeEngine({"two.jpg": [_face(_unit(1)),
                                                            _face(_unit(2), 110)]}), store)
    qtbot.addWidget(dialog)
    dialog.show()
    _activate(qtbot, dialog)
    qtbot.waitUntil(lambda: len(dialog.rows) == 2 and _focused() is dialog.rows[0].name_edit)
    qtbot.keyClicks(_focused(), "Atanu")
    _key(qtbot, Qt.Key.Key_Tab)
    assert _focused() is dialog.rows[0].skip_check
    assert dialog.strip.text() == f"Space tick · Esc cancel · {TAB}"
    _key(qtbot, Qt.Key.Key_Tab)
    assert _focused() is dialog.rows[1].name_edit
    _key(qtbot, Qt.Key.Key_Backtab)
    _key(qtbot, Qt.Key.Key_Space)  # skip the first face
    assert dialog.rows[0].skip_check.isChecked()
    _key(qtbot, Qt.Key.Key_Escape)  # cancel: nothing saved
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert store.people() == []


@pytest.mark.real_dialogs
def test_face_setup_delete_asks_and_esc_goes_back(qtbot, make_window, photos):
    w = make_window()
    w.people_store.add("Atanu", _unit(1), Image.new("RGB", (40, 40), "blue"))
    _start_by_keyboard(qtbot, w, photos, "face")
    setup = w.face_setup_page
    qtbot.waitUntil(lambda: _focused() is setup.people_list)
    seen = []
    _press_in_dialog(qtbot, seen, Qt.Key.Key_Return)  # Keep, the default
    _key(qtbot, Qt.Key.Key_Delete)
    assert [(title, focus) for title, focus, _ in seen] == [("Forget person", "Keep")]
    assert [p.name for p in w.people_store.people()] == ["Atanu"]
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is setup.people_list)
    _key(qtbot, Qt.Key.Key_Escape)
    assert w.current_page() is w.home


# ----- Events: F2 rename, M merge, Ctrl+Enter, Go ----------------------------------------

GOA = datetime(2025, 1, 10, 9, 0)
PARTY = datetime(2025, 2, 20, 18, 0)


def test_events_by_keyboard(qtbot, make_window, photos, monkeypatch, errors):
    times: dict[str, datetime] = {}
    for n in range(10):
        times[f"goa{n}.jpg"] = GOA + timedelta(minutes=20 * n)
        times[f"party{n}.jpg"] = PARTY + timedelta(minutes=5 * n)
    for name in times:
        _jpeg(photos / name)
    monkeypatch.setattr(events_mode, "find_datetime", lambda path: times[path.name])
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "events")
    page = w.events_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    qtbot.waitUntil(lambda: _focused() is page.rows[0])
    _key(qtbot, Qt.Key.Key_F2)
    assert _focused() is page.rows[0].name_edit
    qtbot.keyClicks(_focused(), "Goa trip")
    _key(qtbot, Qt.Key.Key_Return)  # done
    assert _focused() is page.rows[0]
    _key(qtbot, Qt.Key.Key_M)  # merge the trip with the party
    assert len(page.rows) == 1
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    _on_summary(qtbot, w)
    assert w.summary_label.text().splitlines()[0] == "20 files → 1 event folders"
    _go(qtbot, w)
    assert _files(photos) == sorted(f"Goa trip/{name}" for name in times)
    assert errors == []


# ----- Content: the picker, Ctrl+Enter, Go -------------------------------------------------

class _Clip:
    """A CLIP engine knowing each prompt as an axis, and each photo, by name,
    as its similarity to some prompts."""

    def __init__(self, prompts: list[str], photos: dict[str, dict[str, float]]) -> None:
        self.axis = {text: n for n, text in enumerate(prompts)}
        self.photos = photos

    def image_embedding(self, path: Path) -> np.ndarray:
        v = np.zeros(512, np.float32)
        for text, similarity in self.photos.get(Path(path).name, {}).items():
            v[self.axis[text]] = similarity
        v[-1] = np.sqrt(1 - float(v @ v))
        return v

    def text_embeddings(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), 512), np.float32)
        for row, text in enumerate(texts):
            out[row, self.axis[text]] = 1
        return out


def _content_engine() -> _Clip:
    prompts = [text for parts in content.prompt_sets(list(DEFAULT_CONTENT_CATEGORIES))
               for text in parts]
    building = 0.298 / math.sqrt(2)
    return _Clip(prompts + content.background_prompts(), {
        "food.jpg": {"a photo of food": 0.3},  # sure
        "street.jpg": {"a photo of vehicles": 0.3, "a photo of buildings": building,
                       "a photo of city": building},  # unsure
    })


def test_content_by_keyboard(qtbot, make_window, photos, errors):
    for name in ("food.jpg", "street.jpg"):
        _jpeg(photos / name)
    w = make_window()
    engine = _content_engine()
    w.clip_engine_factory = lambda use_gpu: engine
    _start_by_keyboard(qtbot, w, photos, "content")
    page = w.category_review_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    [card] = page.cards
    qtbot.waitUntil(lambda: _focused() is card)
    assert card.answer() == "Vehicles"
    _key(qtbot, Qt.Key.Key_Return)  # the picker
    view = card.choice_combo.view()
    qtbot.waitUntil(view.isVisible)
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Return)  # the one after Vehicles
    qtbot.waitUntil(lambda: not view.isVisible())
    chosen = card.answer()
    assert chosen not in (None, "Vehicles")
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is card)
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    _on_summary(qtbot, w)
    street = next(m.dst for m in w._plan.moves if m.src == "street.jpg")
    assert not street.startswith("Vehicles/")
    _go(qtbot, w)
    assert (photos / street).is_file()
    assert (photos / "Food" / "food.jpg").is_file()
    assert errors == []


# ----- History's Undo and Settings, through the sidebar ------------------------------------

@pytest.mark.real_dialogs
def test_history_undo_by_keyboard(qtbot, make_window, photos, make_jpeg, errors):
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "date")
    _go(qtbot, w)
    _key(qtbot, Qt.Key.Key_Tab)  # Done's last button: on to the sidebar
    assert _focused() is w.sidebar.items["sort"]
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Return)
    page = w.history_page
    assert w.current_page() is page
    _key(qtbot, Qt.Key.Key_Tab)
    assert _focused() is page.runs_table
    assert page.runs_table.item(0, 3).text() == "Done"
    seen = []
    _press_in_dialog(qtbot, seen, Qt.Key.Key_Right, Qt.Key.Key_Return)
    _key(qtbot, Qt.Key.Key_Return)
    assert [(title, focus) for title, focus, _ in seen] == [("Undo this sort?", "Cancel")]
    qtbot.waitUntil(lambda: page.runs_table.item(0, 3).text() == "Undone")
    assert _files(photos) == ["a.jpg"]
    assert errors == []


@pytest.mark.real_dialogs
def test_settings_and_categories_by_keyboard(qtbot, make_window, settings_path):
    w = make_window()
    _key(qtbot, Qt.Key.Key_Tab)  # from the tiles to the sidebar
    assert _focused() is w.sidebar.items["sort"]
    for _ in range(3):
        _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Return)
    page = w.settings_page
    assert w.current_page() is page
    _key(qtbot, Qt.Key.Key_Tab)
    assert _focused() is page.gpu_check
    before = page.gpu_check.isChecked()
    _key(qtbot, Qt.Key.Key_Space)
    assert load_settings(settings_path).use_gpu is (not before)
    _key(qtbot, Qt.Key.Key_Tab)
    assert _focused() is page.gap_spin
    _key(qtbot, Qt.Key.Key_Up)
    assert load_settings(settings_path).event_gap_hours == 7
    _key(qtbot, Qt.Key.Key_Tab)
    _key(qtbot, Qt.Key.Key_Tab)
    assert _focused() is page.categories_button
    strips = []

    def edit(editor: CategoryEditor):
        qtbot.waitUntil(lambda: _focused() is editor.list_widget)
        strips.append(editor.strip.text())
        _in_dialog(qtbot, name_it, CategoryNameDialog)
        _key(qtbot, Qt.Key.Key_Insert)  # add (the name dialog, answered below)
        _activate(qtbot, editor)
        qtbot.waitUntil(lambda: _focused() is editor.list_widget)
        assert editor.categories()[-1] == "Cars"
        _key(qtbot, Qt.Key.Key_Up, ALT)  # one up
        _key(qtbot, Qt.Key.Key_Return)  # OK

    def name_it(dialog: CategoryNameDialog):
        qtbot.waitUntil(lambda: _focused() is dialog.name_edit)
        strips.append(dialog.strip.text())
        qtbot.keyClicks(dialog.name_edit, "Cars")
        _key(qtbot, Qt.Key.Key_Return)

    _in_dialog(qtbot, edit, CategoryEditor)
    _key(qtbot, Qt.Key.Key_Return)  # Content categories…
    defaults = list(DEFAULT_CONTENT_CATEGORIES)
    assert load_settings(settings_path).content_categories == [
        *defaults[:-1], "Cars", defaults[-1]]
    assert strips == [
        f"↑↓ choose · Enter OK · Insert/A add · F2 rename · Delete remove · Alt+↓ move down · "
        f"R reset · Esc cancel · {TAB}",
        "Enter OK · Esc cancel"]


@pytest.mark.real_dialogs
def test_category_editor_keys(qtbot, monkeypatch):
    names = []
    monkeypatch.setattr(CategoryEditor, "_ask_name",
                        lambda self, title, name, others: names.append((title, name)) or "Zed")
    editor = CategoryEditor(["Food", "Pets", "Cars"])
    qtbot.addWidget(editor)
    editor.show()
    _activate(qtbot, editor)
    qtbot.waitUntil(lambda: _focused() is editor.list_widget)
    _key(qtbot, Qt.Key.Key_Down, ALT)  # Food down
    assert editor.categories() == ["Pets", "Food", "Cars"]
    _key(qtbot, Qt.Key.Key_F2)  # rename Food
    assert names == [("Rename the category", "Food")]
    _key(qtbot, Qt.Key.Key_Delete)  # remove it
    assert editor.categories() == ["Pets", "Cars"]
    _key(qtbot, Qt.Key.Key_A)  # add
    assert editor.categories() == ["Pets", "Cars", "Zed"]
    _key(qtbot, Qt.Key.Key_R)  # reset
    assert editor.categories() == list(DEFAULT_CONTENT_CATEGORIES)
    _key(qtbot, Qt.Key.Key_Escape)  # cancel
    assert editor.result() == QDialog.DialogCode.Rejected


# ----- the model download question ------------------------------------------------------------

@pytest.mark.real_dialogs
def test_the_download_question_needs_a_deliberate_yes(qtbot, monkeypatch):
    from photosort.ai import models

    monkeypatch.setattr(models, "missing", lambda keys: list(keys))
    downloads = []
    monkeypatch.setattr(model_download._Downloads, "run", lambda self: downloads.append(1))
    seen = []
    _press_in_dialog(qtbot, seen, Qt.Key.Key_Return)  # Enter alone ...
    assert model_download.ensure_models(None, ["face_detection", "face_recognition"]) is False
    assert seen == [(model_download.TITLE, "Not now", "←→ choose · Enter pick · Esc no/close")]
    assert downloads == []  # ... downloads nothing
    _press_in_dialog(qtbot, seen, Qt.Key.Key_Right, Qt.Key.Key_Return)  # Download
    model_download.ensure_models(None, ["face_detection", "face_recognition"])
    assert downloads == [1]


def test_the_download_dialog_cancels_on_esc_not_enter(qtbot):
    dialog = model_download.DownloadDialog("Face models", "Downloading the face models…", 10)
    qtbot.addWidget(dialog)
    cancelled = []
    dialog.canceled.connect(lambda: cancelled.append(1))
    dialog.show()
    _activate(qtbot, dialog)
    qtbot.waitUntil(lambda: _focused() is dialog.cancel_button)
    assert dialog.strip.text() == "Esc cancel"
    _key(qtbot, Qt.Key.Key_Return)
    assert cancelled == []
    _key(qtbot, Qt.Key.Key_Escape)
    _key(qtbot, Qt.Key.Key_Escape)
    assert cancelled == [1]  # once
    assert dialog.isVisible()  # until the downloads end


# ----- every page's and dialog's strip --------------------------------------------------------

def _live(widget: QWidget) -> list[str]:
    return [f"{label} {word}" for label, word in shortcuts._live(widget)]


def _records(focus: QWidget | None, screen: QWidget) -> str:
    """The live keys of focus's nearest recorded holder below screen, then
    screen's, one per key."""
    holder = focus
    while holder is not None and holder is not screen and not _live(holder):
        holder = holder.parentWidget()
    entries = (_live(holder) if holder is not None and holder is not screen else []) + _live(
        screen)
    shown, keys = [], set()
    for entry in entries:
        key = entry.split(" ")[0]
        if key not in keys:
            keys.add(key)
            shown.append(entry)
    return " · ".join(shown)


def _group(root: Path) -> Group:
    members = []
    for rel in ("ga.jpg", "gb.jpg"):
        path = _jpeg(root / rel)
        members.append(Candidate(rel, "photo", 64, 48, path.stat().st_size, None, 50.0, ""))
    return Group("exact", members, {"ga.jpg"})


def _bad(root: Path) -> list[Candidate]:
    path = _jpeg(root / "bad.jpg")
    return [Candidate("bad.jpg", "photo", 64, 48, path.stat().st_size, None, 0.0, "blurry")]


def _events(root: Path) -> list[Event]:
    for rel in ("e0.jpg", "e1.jpg"):
        _jpeg(root / rel)
    return [Event(("e0.jpg", "e1.jpg"), (GOA, GOA + timedelta(hours=2)))]


def _face_scan(w: MainWindow, root: Path):
    _jpeg(root / "f.jpg", (200, 200))
    atanu = w.people_store.add("Atanu", _unit(1), Image.new("RGB", (40, 40), "blue"))
    question = Question("f.jpg", atanu.id, "Atanu", (20.0, 40.0, 80.0, 120.0), (200, 200),
                        0.40, 63)
    return [atanu], FaceScan({}, [question], photos=1, unreadable=0)


def _clip_sort(root: Path) -> clip_flow._ClipSort:
    _jpeg(root / "c.jpg")
    unsure = [Verdict("c.jpg", "Food", 0.6, (("Food", 0.6), ("Pets", 0.2)))]
    scan = ClipScan({"c.jpg": np.zeros(512, np.float32)}, photos=1, unreadable=0)
    return clip_flow._ClipSort(["Food", "Pets"], scan, {}, unsure)


def _each_page(qtbot, w: MainWindow, root: Path):
    """Open each page of the stack in turn, as it is reached; yield it."""
    w.folder_edit.setText(str(root))
    w.home.focus_tiles()
    yield w.home
    for key in ("history", "people", "settings"):
        w.sidebar.items[key].setFocus()
        _key(qtbot, Qt.Key.Key_Return)
        _key(qtbot, Qt.Key.Key_Tab)  # into the page
        yield w.current_page()
    w.sidebar.items["sort"].setFocus()
    _key(qtbot, Qt.Key.Key_Return)
    w.begin_sort()
    w.root, w.mode = root, "date"
    w.begin_progress("Checking files…")
    yield w.progress_page
    w.mode = "duplicates"
    groups_flow._on_found(w, [_group(root)])
    yield w.review_page
    w.mode = "bad"
    bad_flow._on_found(w, _bad(root))
    yield w.bad_page
    w.mode = "events"
    events_flow._on_found(w, (_events(root), 0))
    yield w.events_page
    w.mode = "face"
    face_flow.start(w, root, False, None)
    yield w.face_setup_page
    people, scan = _face_scan(w, root)
    face_flow._on_scan(w, people, scan)
    yield w.face_question_page
    w.mode = "content"
    w.flow_state = _clip_sort(root)
    clip_flow._ask(w, {})
    yield w.category_review_page
    w.show_summary(["1 files → Duplicates"], can_go=True)
    yield w.summary_page
    w._show_done(["Moved 1 files."], can_undo=True)
    yield w.done_page


def test_every_pages_strip_is_its_live_keys(qtbot, make_window, photos):
    w = make_window()
    shown = []
    for page in _each_page(qtbot, w, photos):
        assert w.current_page() is page
        qtbot.waitUntil(lambda: _focused() is not None)
        qtbot.wait(10)
        focus = _focused()
        screen = w.sidebar if w.sidebar.isAncestorOf(focus) else page
        text = w.strip.text()
        assert text and text == _records(focus, screen), type(page).__name__
        assert "Tab" in text or page is w.progress_page, type(page).__name__
        shown.append(page)
    assert len(shown) == w.stack.count() and set(shown) == {
        w.stack.widget(n) for n in range(w.stack.count())}  # every page


def _dialogs(qtbot, tmp_path: Path):
    """Each in-app dialog, shown (not run); yield it with its strip as expected."""
    store = PeopleStore(tmp_path / "people")
    photo = _jpeg(tmp_path / "face.jpg", (200, 200))
    naming = FaceNamingDialog(photo, FakeEngine({"face.jpg": [_face(_unit(1))]}), store)
    yield naming, f"Enter save · Esc cancel · {TAB}", lambda: (
        naming.rows[0].name_edit if naming.rows else naming.save_button)  # once it has looked
    editor = CategoryEditor(list(DEFAULT_CONTENT_CATEGORIES))
    yield editor, (f"↑↓ choose · Enter OK · Insert/A add · F2 rename · Delete remove · "
                   f"Alt+↓ move down · R reset · Esc cancel · {TAB}"), lambda: editor.list_widget
    name = CategoryNameDialog("Add a category", "", ["Food"])
    yield name, "Enter OK · Esc cancel", lambda: name.name_edit
    preview = PhotoPreview(photo)
    yield preview, "Esc close", lambda: None
    group = Group("repeated", [Candidate("face.jpg", "photo", 200, 200, 1, None, 1.0, "")],
                  {"face.jpg"})
    viewer = FullscreenViewer(tmp_path, group, {"face.jpg"})
    yield viewer, ("←→ flip · S side by side · +− zoom · 0 fit · Space keep · Enter done · "
                   "Esc close"), lambda: None
    download = model_download.DownloadDialog("Face models", "Downloading the face models…", 9)
    yield download, "Esc cancel", lambda: download.cancel_button
    ask = dialogs.MessageDialog(None, "Undo this sort?", "Put back the files?",
                                [("Cancel", False), ("Undo", True)], 0, dialogs._ASK_KEYS)
    yield ask, "←→ choose · Enter pick · Esc no/close", lambda: ask.buttons[0]


def _show(qtbot, dialog: QDialog, focus) -> None:
    dialog.show()
    _activate(qtbot, dialog)
    qtbot.waitUntil(lambda: focus() is None or _focused() is focus())
    qtbot.wait(10)


def _close(dialog: QDialog) -> None:
    dialog.done(0)
    if not isinstance(dialog, PhotoPreview):  # which deletes itself
        dialog.deleteLater()


def test_every_dialogs_strip_is_its_live_keys(qtbot, tmp_path):
    for dialog, expected, focus in _dialogs(qtbot, tmp_path):
        _show(qtbot, dialog, focus)
        strip = dialog.findChild(ShortcutStrip)
        assert strip.text() == expected, type(dialog).__name__
        assert strip.text() == _records(_focused(), dialog), type(dialog).__name__
        _close(dialog)
    QThreadPool.globalInstance().waitForDone()


def test_every_dialog_fits_the_smallest_window(qtbot, real_font, tmp_path):
    for dialog, _, focus in _dialogs(qtbot, tmp_path):
        _show(qtbot, dialog, focus)
        name = type(dialog).__name__
        layout = dialog.layout()
        width = min(dialog.width(), SMALLEST[0])
        assert dialog.minimumSizeHint().width() <= SMALLEST[0], name
        height = (layout.totalMinimumHeightForWidth(width) if layout.hasHeightForWidth()
                  else dialog.minimumSizeHint().height())
        assert height <= SMALLEST[1], (name, height)
        dialog.resize(width, min(dialog.height(), SMALLEST[1]))
        qtbot.wait(10)
        for button in dialog.findChildren(QPushButton):
            if button.isVisible():  # none crushed below its text
                assert button.width() >= button.minimumSizeHint().width(), (name, button.text())
        _close(dialog)
    QThreadPool.globalInstance().waitForDone()


# ----- a focused button lists Enter, on every screen with buttons --------------------------------

def _strips_on(qtbot, strip: ShortcutStrip, buttons) -> list[str]:
    """The strip while each of buttons has the focus."""
    shown = []
    for button in buttons:
        button.setFocus(Qt.FocusReason.TabFocusReason)
        qtbot.waitUntil(lambda: _focused() is button)
        shown.append(strip.text())
    return shown


def test_the_naming_dialogs_buttons_list_enter(qtbot, tmp_path, make_jpeg):
    photo = make_jpeg(tmp_path / "one.jpg", size=(200, 200))
    dialog = FaceNamingDialog(photo, FakeEngine({"one.jpg": [_face(_unit(1))]}),
                              PeopleStore(tmp_path / "people"))
    qtbot.addWidget(dialog)
    dialog.show()
    _activate(qtbot, dialog)
    qtbot.waitUntil(lambda: dialog.rows and dialog.choose_button.isEnabled())
    assert _strips_on(qtbot, dialog.strip, (dialog.choose_button, dialog.cancel_button,
                                            dialog.save_button)) == [
        f"Enter choose photo · Esc cancel · {TAB}",
        f"Enter cancel · Esc cancel · {TAB}",
        f"Enter save · Esc cancel · {TAB}"]


def test_face_setups_buttons_list_enter(qtbot, make_window, photos):
    w = make_window()
    w.people_store.add("Atanu", _unit(1), Image.new("RGB", (40, 40), "blue"))
    _start_by_keyboard(qtbot, w, photos, "face")
    setup = w.face_setup_page
    qtbot.waitUntil(lambda: _focused() is setup.people_list)
    _key(qtbot, Qt.Key.Key_Space)  # tick Atanu: Start sorting can be pressed
    keys = f"A add person · Delete forget · Esc back · {TAB}"
    assert _strips_on(qtbot, w.strip, (setup.add_button, setup.more_button,
                                       setup.delete_button, setup.back_button,
                                       setup.start_button)) == [
        f"Enter add person · {keys}", f"Enter add photos · {keys}", f"Enter forget · {keys}",
        f"Enter back · {keys}", f"Enter start · {keys}"]


def test_the_people_pages_forget_lists_enter(qtbot, make_window):
    w = make_window()
    w.people_store.add("Atanu", _unit(1), Image.new("RGB", (40, 40), "blue"))
    w.sidebar.items["people"].setFocus()
    _key(qtbot, Qt.Key.Key_Return)
    people = w.people_page
    qtbot.waitUntil(lambda: w.current_page() is people)
    _key(qtbot, Qt.Key.Key_Tab)  # into the page
    assert _strips_on(qtbot, w.strip, [people.delete_button]) == [
        f"Enter forget · Delete forget · Esc sidebar · {TAB}"]


@pytest.mark.real_dialogs
def test_the_category_editors_buttons_list_enter(qtbot, monkeypatch):
    names = []
    monkeypatch.setattr(CategoryEditor, "_ask_name",
                        lambda self, title, name, others: names.append(title) or "Zed")
    editor = CategoryEditor(["Food", "Pets", "Cars", "Sky"])
    qtbot.addWidget(editor)
    editor.show()
    _activate(qtbot, editor)
    editor.list_widget.setCurrentRow(1)  # Move up and Move down can both be pressed
    keys = (f"Insert/A add · F2 rename · Delete remove · Alt+↑ move up · Alt+↓ move down · "
            f"R reset · Esc cancel · {TAB}")
    buttons = (editor.add_button, editor.rename_button, editor.remove_button,
               editor.up_button, editor.down_button, editor.reset_button, editor.ok_button,
               editor.cancel_button)
    words = ("add", "rename", "remove", "move up", "move down", "reset", "OK", "cancel")
    assert _strips_on(qtbot, editor.strip, buttons) == [f"Enter {word} · {keys}"
                                                        for word in words]
    editor.remove_button.setFocus()
    qtbot.waitUntil(lambda: _focused() is editor.remove_button)
    _key(qtbot, Qt.Key.Key_Return)  # presses Remove, once even if held
    _hold(Qt.Key.Key_Return)
    assert editor.categories() == ["Food", "Cars", "Sky"]
    assert names == []
    editor.add_button.setFocus()
    qtbot.waitUntil(lambda: _focused() is editor.add_button)
    _key(qtbot, Qt.Key.Key_Return)
    assert names == ["Add a category"] and editor.categories()[-1] == "Zed"
    editor.cancel_button.setFocus()
    qtbot.waitUntil(lambda: _focused() is editor.cancel_button)
    _key(qtbot, Qt.Key.Key_Return)
    assert editor.result() == QDialog.DialogCode.Rejected


# ----- the strip while the window is away (Task 4 carry-overs) ---------------------------------

def test_no_focus_in_the_active_window_is_not_away(qtbot):
    screen = QWidget()
    qtbot.addWidget(screen)
    strip = ShortcutStrip(screen)
    key = shortcuts.bind(screen, "X", "X", "ex", lambda: None)
    shortcuts.describe(screen, "Esc", "back")
    screen.show()
    _activate(qtbot, screen)
    strip.show_for(screen)
    assert strip.text() == "X ex · Esc back"
    screen.setFocus()
    screen.clearFocus()
    assert _focused() is None and screen.isActiveWindow()
    key.setEnabled(False)
    strip.refresh()
    assert strip.text() == "Esc back"


def test_the_sidebars_keys_stay_while_the_window_is_away(qtbot, make_window):
    w = make_window()
    _key(qtbot, Qt.Key.Key_Tab)
    assert _focused() is w.sidebar.items["sort"]
    sidebar_keys = w.strip.text()
    assert sidebar_keys == "↑↓ move · Enter open · Tab leave"
    other = QWidget()
    qtbot.addWidget(other)
    other.show()
    _activate(qtbot, other)  # another window: the focus leaves this one
    qtbot.wait(10)
    assert w.strip.text() == sidebar_keys
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is w.sidebar.items["sort"])
    assert w.strip.text() == sidebar_keys


def test_a_held_key_shortcut_check(qtbot):
    """_hold sends real auto-repeats: a shortcut that must not repeat ignores them."""
    screen = QWidget()
    qtbot.addWidget(screen)
    pressed = []
    shortcuts.bind(screen, "U", "U", "undo", lambda: pressed.append(1), repeat=False)
    screen.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    screen.show()
    _activate(qtbot, screen)
    screen.setFocus()
    qtbot.waitUntil(lambda: _focused() is screen)
    _key(qtbot, Qt.Key.Key_U)
    _hold(Qt.Key.Key_U)
    assert pressed == [1]
