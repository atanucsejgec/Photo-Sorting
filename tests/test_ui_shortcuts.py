"""Shortcuts are bound and recorded in one call; the strip shows the records;
the themed dialogs answer by keyboard."""

import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget

from photosort.ui import dialogs, shortcuts
from photosort.ui.shortcuts import ShortcutStrip


@pytest.fixture
def screen(qtbot):
    """A shown screen: a name field with keys of its own, a plain button and a strip.

    The screen binds Esc and describes its arrows.
    """
    page = QWidget()
    layout = QVBoxLayout(page)
    page.edit = QLineEdit()
    page.button = QPushButton("Plain")
    page.strip = ShortcutStrip()
    for widget in (page.edit, page.button, page.strip):
        layout.addWidget(widget)
    page.pressed = []
    shortcuts.bind(page, "Esc", "Esc", "back", lambda: page.pressed.append("Esc"))
    shortcuts.describe(page, "←→", "move")
    shortcuts.describe(page.edit, "Enter", "done")
    shortcuts.describe(page.edit, "Esc", "stop editing")
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)
    page.activateWindow()
    return page


def _focus(qtbot, widget):
    widget.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is widget)


def test_bind_makes_a_working_shortcut_and_records_it(qtbot, screen):
    shortcuts.bind(screen, "Ctrl+Return", "Ctrl+Enter", "continue",
                   lambda: screen.pressed.append("Ctrl+Enter"))
    _focus(qtbot, screen.button)
    qtbot.keyClick(screen.button, Qt.Key.Key_Escape)
    qtbot.keyClick(screen.button, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(screen.button, Qt.Key.Key_Enter,  # the keypad's Enter too
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier)
    assert screen.pressed == ["Esc", "Ctrl+Enter", "Ctrl+Enter"]
    assert shortcuts.keys_for(screen.button, screen) == [
        ("Esc", "back"), ("←→", "move"), ("Ctrl+Enter", "continue")]


def test_a_disabled_shortcut_is_not_listed(qtbot, screen):
    shortcut = shortcuts.bind(screen, "F2", "F2", "rename", lambda: None)
    assert ("F2", "rename") in shortcuts.keys_for(None, screen)
    shortcut.setEnabled(False)
    assert ("F2", "rename") not in shortcuts.keys_for(None, screen)


def test_the_focused_controls_keys_come_first_and_leave_with_focus(qtbot, screen):
    assert shortcuts.keys_for(screen.edit, screen) == [
        ("Enter", "done"), ("Esc", "stop editing"), ("←→", "move")]  # one Esc, the field's
    assert shortcuts.keys_for(screen.button, screen) == [("Esc", "back"), ("←→", "move")]
    assert shortcuts.keys_for(None, screen) == [("Esc", "back"), ("←→", "move")]


def test_the_nearest_recorded_ancestor_speaks_for_the_focus(qtbot, screen):
    box = QWidget(screen)
    inner = QPushButton("Inner", box)
    shortcuts.describe(box, "Space", "tick")
    assert shortcuts.keys_for(inner, screen) == [
        ("Space", "tick"), ("Esc", "back"), ("←→", "move")]


def test_focus_outside_the_screen_adds_nothing(qtbot, screen):
    other = QLineEdit()
    qtbot.addWidget(other)
    shortcuts.describe(other, "Enter", "elsewhere")
    assert shortcuts.keys_for(other, screen) == [("Esc", "back"), ("←→", "move")]


def test_the_strip_follows_focus(qtbot, screen):
    screen.strip.show_for(screen)
    _focus(qtbot, screen.edit)
    qtbot.waitUntil(lambda: screen.strip.text() == "Enter done · Esc stop editing · ←→ move")
    _focus(qtbot, screen.button)
    qtbot.waitUntil(lambda: screen.strip.text() == "Esc back · ←→ move")


def test_the_strip_wraps_rather_than_clips(qtbot, screen):
    for n in range(8):
        shortcuts.describe(screen, f"F{n + 1}", f"word{n}")
    screen.strip.show_for(screen)
    assert screen.strip.objectName() == "shortcutStrip"
    assert screen.strip.hasHeightForWidth()
    assert screen.strip.heightForWidth(120) > screen.strip.heightForWidth(4000)


def test_the_strip_is_smaller_than_the_body_text(qtbot, screen):
    body = QApplication.font().pointSizeF()
    assert screen.strip.font().pointSizeF() == pytest.approx(body * 0.85, abs=0.05)


def test_a_deleted_strip_ignores_focus_changes(qtbot, screen):
    strip = ShortcutStrip(screen)
    strip.show_for(screen)
    strip.deleteLater()
    qtbot.wait(10)
    with qtbot.captureExceptions() as raised:
        _focus(qtbot, screen.edit)
        _focus(qtbot, screen.button)
    assert raised == []  # no error from the strip's slot


# ----- dialogs -----------------------------------------------------------------

def _answer(qtbot, *keys):
    """Press keys in the next modal dialog, once it shows; return what it showed."""
    seen = {}

    def press():
        dialog = QApplication.activeModalWidget()
        if dialog is None or dialog.focusWidget() is None:
            QTimer.singleShot(10, press)
            return
        seen["focus"] = dialog.focusWidget().text()
        seen["strip"] = dialog.findChild(ShortcutStrip).text()
        seen["primary"] = [b.text() for b in dialog.findChildren(QPushButton)
                           if b.objectName() == "primaryButton"]
        for key in keys:
            qtbot.keyClick(dialog.focusWidget(), key)

    QTimer.singleShot(0, press)
    return seen


@pytest.mark.real_dialogs
def test_ask_enter_takes_the_default_no(qtbot):
    seen = _answer(qtbot, Qt.Key.Key_Return)
    assert dialogs.ask(None, "Forget", "Forget Zoe?", yes="Forget", no="Keep") is False
    assert seen["focus"] == "Keep"
    assert seen["strip"] == "←→ choose · Enter pick · Esc no/close"


@pytest.mark.real_dialogs
@pytest.mark.parametrize("key", [Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_a_held_key_does_not_answer(qtbot, key):
    """The repeats of a key held down (say Esc, held on the screen behind
    as the dialog opened) answer nothing; a fresh press does."""
    seen = {}

    def press():
        dialog = QApplication.activeModalWidget()
        if dialog is None or dialog.focusWidget() is None:
            QTimer.singleShot(10, press)
            return
        for _ in range(3):
            QApplication.sendEvent(dialog.focusWidget(), QKeyEvent(
                QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "", True))
        seen["open"] = dialog.isVisible()
        dialog.buttons[1].setFocus()  # Leave, so a fresh Enter says yes
        qtbot.keyClick(dialog.focusWidget(), Qt.Key.Key_Return)

    QTimer.singleShot(0, press)
    assert dialogs.ask(None, "Leave this review?", "Your answers so far will be lost.",
                       yes="Leave", no="Stay") is True
    assert seen == {"open": True}


@pytest.mark.real_dialogs
def test_ask_arrows_then_enter_picks_yes(qtbot):
    seen = _answer(qtbot, Qt.Key.Key_Right, Qt.Key.Key_Return)
    assert dialogs.ask(None, "Forget", "Forget Zoe?", yes="Forget", no="Keep") is True
    assert seen["focus"] == "Keep"
    seen = _answer(qtbot, Qt.Key.Key_Left, Qt.Key.Key_Return)  # either way round
    assert dialogs.ask(None, "Forget", "Forget Zoe?", yes="Forget", no="Keep") is True


@pytest.mark.real_dialogs
def test_ask_escape_is_no(qtbot):
    _answer(qtbot, Qt.Key.Key_Escape)
    assert dialogs.ask(None, "Forget", "Forget Zoe?", yes="Forget", no="Keep",
                       default="yes") is False


@pytest.mark.real_dialogs
def test_ask_default_yes_starts_on_yes(qtbot):
    seen = _answer(qtbot, Qt.Key.Key_Return)
    assert dialogs.ask(None, "Download", "Download now?", yes="Download", no="Not now",
                       default="yes") is True
    assert seen["focus"] == "Download"


@pytest.mark.real_dialogs
def test_ask_shows_the_title_and_text(qtbot):
    texts = {}

    def look():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(10, look)
            return
        texts["title"] = dialog.windowTitle()
        texts["labels"] = [label.text() for label in dialog.findChildren(type(dialog.text_label))]
        dialog.reject()

    QTimer.singleShot(0, look)
    dialogs.ask(None, "Leave this review?", "Your answers so far will be lost.",
                yes="Leave", no="Stay")
    assert texts["title"] == "Leave this review?"
    assert "Your answers so far will be lost." in texts["labels"]


CHOICES = ["Modified", "Created"]


@pytest.mark.real_dialogs
def test_choose_enter_takes_the_default(qtbot):
    seen = _answer(qtbot, Qt.Key.Key_Return)
    assert dialogs.choose(None, "Which date?", "Sort by?", CHOICES) == 0
    assert seen["focus"] == "Modified"
    assert seen["strip"] == "←→ choose · Enter pick · Esc cancel"
    assert seen["primary"] == []
    seen = _answer(qtbot, Qt.Key.Key_Return)
    assert dialogs.choose(None, "Which date?", "Sort by?", CHOICES, default=1) == 1
    assert seen["focus"] == "Created"


@pytest.mark.real_dialogs
def test_choose_right_then_enter_picks_the_next(qtbot):
    seen = _answer(qtbot, Qt.Key.Key_Right, Qt.Key.Key_Return)
    assert dialogs.choose(None, "Which date?", "Sort by?", CHOICES) == 1
    assert seen["focus"] == "Modified"
    _answer(qtbot, Qt.Key.Key_Left, Qt.Key.Key_Return)
    assert dialogs.choose(None, "Which date?", "Sort by?", ["A", "B", "C"]) == 2  # wraps


@pytest.mark.real_dialogs
def test_choose_escape_is_none(qtbot):
    _answer(qtbot, Qt.Key.Key_Escape)
    assert dialogs.choose(None, "Which date?", "Sort by?", CHOICES, default=1) is None


@pytest.mark.real_dialogs
def test_choose_shows_the_title_text_and_options_in_order(qtbot):
    texts = {}

    def look():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(10, look)
            return
        texts["title"] = dialog.windowTitle()
        texts["labels"] = [label.text() for label in dialog.findChildren(type(dialog.text_label))]
        texts["buttons"] = [b.text() for b in dialog.buttons]
        dialog.reject()

    QTimer.singleShot(0, look)
    dialogs.choose(None, "Which date?", "Sort files by?", CHOICES)
    assert texts["title"] == "Which date?"
    assert "Sort files by?" in texts["labels"]
    assert texts["buttons"] == CHOICES


def _held(qtbot, key):
    """In the next modal dialog, send key to the focused button as a held key's
    repeats (auto-repeat presses); record whether the dialog stayed open, then
    close it as Esc would."""
    seen = {}

    def press():
        dialog = QApplication.activeModalWidget()
        if dialog is None or dialog.focusWidget() is None:
            QTimer.singleShot(10, press)
            return
        for _ in range(3):
            QApplication.sendEvent(dialog.focusWidget(), QKeyEvent(
                QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "", True))
        seen["open"] = dialog.isVisible()
        dialog.reject()

    QTimer.singleShot(0, press)
    return seen


@pytest.mark.real_dialogs
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape])
def test_a_held_key_never_answers_choose(qtbot, key):
    # The Enter that opened the dialog, held down, must not pick unseen.
    seen = _held(qtbot, key)
    assert dialogs.choose(None, "Which date?", "Sort by?", CHOICES, default=1) is None
    assert seen["open"] is True


@pytest.mark.real_dialogs
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape])
def test_a_held_key_never_answers_ask(qtbot, key):
    seen = _held(qtbot, key)
    assert dialogs.ask(None, "Include subfolders?", "Continue?", yes="Continue", no="Cancel",
                       default="yes") is False
    assert seen["open"] is True


@pytest.mark.real_dialogs
@pytest.mark.parametrize("show", [dialogs.warn, dialogs.info])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_warn_and_info_close_with_enter_or_esc(qtbot, show, key):
    seen = _answer(qtbot, key)
    assert show(None, "Settings", "Could not save settings: disk full") is None
    assert seen["focus"] == "OK"
    assert seen["strip"] == "Enter OK · Esc close"
    assert QApplication.activeModalWidget() is None


def test_a_window_of_its_own_is_not_on_the_screen(qtbot, screen):
    from PySide6.QtWidgets import QDialog

    dialog = QDialog(screen)  # its parent is the screen, but it is a window
    field = QLineEdit(dialog)
    shortcuts.describe(dialog, "Enter", "pick")
    shortcuts.describe(field, "Esc", "stop editing")
    assert shortcuts.keys_for(field, screen) == [("Esc", "back"), ("←→", "move")]


@pytest.mark.real_dialogs
def test_ask_buttons_are_both_plain(qtbot):
    seen = _answer(qtbot, Qt.Key.Key_Escape)
    dialogs.ask(None, "Leave this review?", "Your answers so far will be lost.",
                yes="Leave", no="Stay")
    assert seen["focus"] == "Stay"
    assert seen["primary"] == []  # the safe default is not "the primary action"


def test_follow_switches_each_key_with_its_button(qtbot, screen):
    one = shortcuts.bind(screen, "1", "1", "one", lambda: None)
    two = shortcuts.bind(screen, "2", "2", "two", lambda: None)
    other = QPushButton("Other", screen)
    pairs = [(one, screen.button), (two, other)]
    assert shortcuts.follow(pairs) is False  # all on already
    other.setEnabled(False)
    assert shortcuts.follow(pairs) is True
    assert one.isEnabled() and not two.isEnabled()
    assert shortcuts.follow(pairs) is False
    other.setEnabled(True)
    assert shortcuts.follow(pairs) is True and two.isEnabled()
