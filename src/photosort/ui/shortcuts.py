"""Keyboard shortcuts, and the strip that lists them.

A shortcut is bound and recorded in one call, bind(), so the strip, built
from the records, can never disagree with the keys that work. Keys a widget
handles itself (arrows in its keyPressEvent, say) are recorded with
describe(). A record is a (label, word) pair, such as ("Esc", "back").

keys_for(focus, screen) is what the strip shows on screen: the records of
the focused control (the focus widget or its nearest ancestor with records,
below screen), then the screen's own, one per label, the control's first.
A bound shortcut that is disabled is not listed.

ShortcutStrip is the small strip along the bottom of a screen or dialog. It
follows focus around the app and redraws from keys_for; it wraps onto more
lines when narrow, so it is never clipped. It is reference text: nothing in
it takes focus or clicks.
"""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLayout, QLayoutItem, QWidget,
)
from shiboken6 import isValid

STRIP_SCALE = 0.85  # of the body font
_ATTR = "_photosort_keys"  # the records, on the widget they belong to


@dataclass(frozen=True)
class _Record:
    label: str
    word: str
    shortcut: QShortcut | None  # None: a key the widget handles itself


def _add(owner: QWidget, record: _Record) -> None:
    records = getattr(owner, _ATTR, None)
    if records is None:
        records = []
        setattr(owner, _ATTR, records)
    records.append(record)


def bind(owner: QWidget, keys: str, label: str, word: str, slot: Callable[[], None],
         context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut
         ) -> QShortcut:
    """A shortcut for keys (a QKeySequence string, e.g. "Ctrl+Return") on
    owner that calls slot, shown in the strip as label and word.

    A key with Return also answers to the keypad's Enter.
    """
    sequences = [QKeySequence(keys)]
    if "Return" in keys:
        sequences.append(QKeySequence(keys.replace("Return", "Enter")))
    shortcut = QShortcut(owner)
    shortcut.setKeys(sequences)
    shortcut.setContext(context)
    shortcut.activated.connect(slot)
    _add(owner, _Record(label, word, shortcut))
    return shortcut


def describe(owner: QWidget, label: str, word: str) -> None:
    """Record a key that owner handles itself, so the strip shows it."""
    _add(owner, _Record(label, word, None))


def _live(widget: QWidget) -> list[tuple[str, str]]:
    return [(r.label, r.word) for r in getattr(widget, _ATTR, ())
            if r.shortcut is None or (isValid(r.shortcut) and r.shortcut.isEnabled())]


def _control_keys(focus: QWidget | None, screen: QWidget) -> list[tuple[str, str]]:
    """The records of focus or of its nearest recorded ancestor below screen;
    none when focus is not inside screen (a window of its own, such as a
    dialog parented to screen, is not inside it)."""
    holder = None
    widget = focus
    while widget is not None and widget is not screen:
        if widget.isWindow():
            return []
        if holder is None and _live(widget):
            holder = widget
        widget = widget.parentWidget()
    if widget is None or holder is None:
        return []
    return _live(holder)


def keys_for(focus: QWidget | None, screen: QWidget) -> list[tuple[str, str]]:
    """The keys to show on screen while focus has the focus (see the module doc)."""
    shown: list[tuple[str, str]] = []
    labels: set[str] = set()
    for label, word in _control_keys(focus, screen) + _live(screen):
        if label not in labels:
            labels.add(label)
            shown.append((label, word))
    return shown


class _FlowLayout(QLayout):
    """Lays its items out in rows, left to right, starting a new row when the
    next item does not fit; its height follows its width."""

    def __init__(self, parent: QWidget, h_spacing: int, v_spacing: int) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h, self._v = h_spacing, v_spacing

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, move: bool) -> int:
        """Place the items in rect (if move); return the height they take."""
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():  # a hidden widget
                continue
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y = area.x(), y + row_height + self._v
                row_height = 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + m.bottom()


class ShortcutStrip(QWidget):
    """The keys that work on a screen, as key chips each with a word."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("shortcutStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # its top line
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        font = QApplication.font()
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * STRIP_SCALE)
        self.setFont(font)
        self._flow = _FlowLayout(self, h_spacing=12, v_spacing=4)
        self._flow.setContentsMargins(0, 8, 0, 0)
        self._screen: QWidget | None = None
        self._shown: list[tuple[str, str]] = []
        QApplication.instance().focusChanged.connect(self._focus_moved)

    def show_for(self, screen: QWidget) -> None:
        """Show screen's keys from now on (with its focused control's)."""
        self._screen = screen
        self.refresh()

    def refresh(self) -> None:
        """Redraw, e.g. after a shortcut is enabled or disabled."""
        if not isValid(self):
            return
        keys = [] if self._screen is None else keys_for(QApplication.focusWidget(),
                                                        self._screen)
        if keys == self._shown:
            return
        self._shown = keys
        while (item := self._flow.takeAt(0)) is not None:
            if item.widget() is not None:
                item.widget().hide()  # now: the deletion waits for the event loop
                item.widget().deleteLater()
        for label, word in keys:
            entry = self._entry(label, word)
            self._flow.addWidget(entry)
            entry.show()  # else it waits for the layout's queued show, unmeasured
        self._flow.invalidate()
        self.updateGeometry()

    def _entry(self, label: str, word: str) -> QWidget:
        entry = QWidget(self)
        row = QHBoxLayout(entry)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for text, name in ((label, "keyChip"), (word, "keyWord")):
            part = QLabel(text, entry)
            part.setObjectName(name)
            part.setTextFormat(Qt.TextFormat.PlainText)
            row.addWidget(part)
        return entry

    def text(self) -> str:
        """What the strip shows, as plain text: "Enter start · Esc back"."""
        return " · ".join(f"{label} {word}" for label, word in self._shown)

    def _focus_moved(self, old: QWidget | None, new: QWidget | None) -> None:
        self.refresh()
