"""Keyboard helpers for the tests that drive the whole window by keys alone
(test_ui_keyboard_e2e, test_ui_file_modes_keyboard): a shown window, the key
presses, answering the next modal dialog, and the live keys a strip should list.

make_window and the fixtures it needs are fixtures: a test module imports
them by name. A dialog wait left over at the end of a test ends with it
(see conftest's _no_waiting_dialog_answers).
"""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from photosort.people import PeopleStore
from photosort.settings import Settings
from photosort.ui import main_window, shortcuts
from photosort.ui.home_page import TILES, ModeTile
from photosort.ui.main_window import MainWindow
from photosort.ui.shortcuts import ShortcutStrip

NONE = Qt.KeyboardModifier.NoModifier
TAB = "Tab/Shift+Tab area"


# ----- the window --------------------------------------------------------------------------

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


# ----- keys and dialogs --------------------------------------------------------------------

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
    """On the summary, Go focused and armed: an Enter now presses it, as
    the owner's deliberate one does (summary_page.GO_ARM_SECONDS)."""
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    qtbot.waitUntil(lambda: _focused() is w.go_button)
    assert w.strip.text() == f"Enter go · ←→ switch buttons · Esc back · {TAB}"
    qtbot.waitUntil(w.summary_page.go_armed)


def _go(qtbot, w: MainWindow) -> None:
    """Enter on the summary (Go focused): Done, Home focused."""
    _on_summary(qtbot, w)
    _key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    qtbot.waitUntil(lambda: _focused() is w.home_button)


def end_waiting() -> None:
    """End every _in_dialog wait still going (at the end of a test), so none
    answers the next test's dialogs."""
    for waiting in _waiting:
        waiting["over"] = True
    _waiting.clear()


# ----- strips ------------------------------------------------------------------------------

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
