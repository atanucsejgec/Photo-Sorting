"""The app's own message boxes, in the theme and with a shortcut strip.

ask() is a yes/no question; choose() offers a few choices; warn() and
info() say something and close. The focus starts on the default button: ←
and → move between the buttons, Enter presses the focused one and Esc
answers no (or cancels, or closes); a held key's repeats do nothing, so a
dialog is only ever answered by a fresh press. Every button is plain, the
focused one ringed: the accent gradient means "the primary action", and
the safe default of a question is not one.
"""

from collections.abc import Sequence
from functools import partial
from typing import Literal

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from photosort.ui import icons, shortcuts
from photosort.ui.labels import literal
from photosort.ui.shortcuts import ShortcutStrip

_ASK_KEYS = (("←→", "choose"), ("Enter", "pick"), ("Esc", "no/close"))
_CHOOSE_KEYS = (("←→", "choose"), ("Enter", "pick"), ("Esc", "cancel"))
_TELL_KEYS = (("Enter", "OK"), ("Esc", "close"))
_ANSWER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape)
_ICON_PX = 28


class MessageDialog(QDialog):
    """title as a heading over text, a row of buttons, and the strip.

    Each button accepts the dialog if it is a yes button, else rejects it;
    picked is then the index of the button pressed (None for Esc).
    """

    def __init__(self, parent: QWidget | None, title: str, text: str,
                 buttons: list[tuple[str, bool]], default: int,
                 keys: tuple[tuple[str, str], ...], icon: str | None = None) -> None:
        """buttons: (text, is_yes) left to right; default: the index of the
        one focused first; keys: the strip's (label, word) records."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.setMaximumWidth(620)

        heading = QLabel(title)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        font = heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.2)
        font.setWeight(QFont.Weight.Bold)
        heading.setFont(font)
        self.text_label = QLabel(text)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        body = QHBoxLayout()
        body.setSpacing(14)
        if icon is not None:
            mark = QLabel()
            mark.setPixmap(icons.icon(icon, "danger").pixmap(_ICON_PX, _ICON_PX))
            body.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(8)
        words.addWidget(heading)
        words.addWidget(self.text_label)
        body.addLayout(words, 1)

        row = QHBoxLayout()
        row.addStretch()
        self.buttons: list[QPushButton] = []
        self.picked: int | None = None
        for n, (label, is_yes) in enumerate(buttons):
            button = QPushButton(literal(label))
            button.setMinimumWidth(100)
            button.setAutoDefault(False)  # Enter is handled below, on the focused button
            button.clicked.connect(partial(self._pressed, n, is_yes))
            button.installEventFilter(self)
            self.buttons.append(button)
            row.addWidget(button)

        self.strip = ShortcutStrip(self)
        for label, word in keys:
            shortcuts.describe(self, label, word)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(18)
        layout.addLayout(body)
        layout.addLayout(row)
        layout.addWidget(self.strip)
        self.buttons[default].setFocus()
        self.strip.show_for(self)

    def _pressed(self, n: int, is_yes: bool) -> None:
        self.picked = n
        if is_yes:
            self.accept()
        else:
            self.reject()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and watched in self.buttons:
            key = event.key()
            if key in _ANSWER_KEYS and event.isAutoRepeat():
                # A held key (the Enter that opened this dialog, say) never
                # answers it: that takes a fresh press.
                return True
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                step = 1 if key == Qt.Key.Key_Right else -1
                n = self.buttons.index(watched)
                self.buttons[(n + step) % len(self.buttons)].setFocus()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                watched.click()
                return True
        return super().eventFilter(watched, event)


def ask(parent: QWidget | None, title: str, text: str, yes: str, no: str,
        default: Literal["yes", "no"] = "no") -> bool:
    """Ask a yes/no question; True only for the yes button. Esc answers no."""
    dialog = MessageDialog(parent, title, text, [(no, False), (yes, True)],
                           default=1 if default == "yes" else 0, keys=_ASK_KEYS)
    answer = dialog.exec() == QDialog.DialogCode.Accepted
    dialog.deleteLater()
    return answer


def choose(parent: QWidget | None, title: str, text: str, options: Sequence[str],
           default: int = 0) -> int | None:
    """Offer options as buttons, left to right, the focus on options[default];
    the index of the one picked, or None for Esc."""
    dialog = MessageDialog(parent, title, text, [(option, True) for option in options],
                           default=default, keys=_CHOOSE_KEYS)
    answer = dialog.picked if dialog.exec() == QDialog.DialogCode.Accepted else None
    dialog.deleteLater()
    return answer


def _tell(parent: QWidget | None, title: str, text: str, icon: str | None) -> None:
    dialog = MessageDialog(parent, title, text, [("OK", True)], default=0,
                           keys=_TELL_KEYS, icon=icon)
    dialog.exec()
    dialog.deleteLater()


def warn(parent: QWidget | None, title: str, text: str) -> None:
    """Say something went wrong, with a warning mark; OK or Esc closes."""
    _tell(parent, title, text, icon="triangle-alert")


def info(parent: QWidget | None, title: str, text: str) -> None:
    """Say something; OK or Esc closes."""
    _tell(parent, title, text, icon=None)
