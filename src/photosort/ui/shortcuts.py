"""Keyboard shortcuts, and the strip that lists them.

A shortcut is bound and recorded in one call, bind(), so the strip, built
from the records, can never disagree with the keys that work. Keys a widget
handles itself (arrows in its keyPressEvent, say) are recorded with
describe(). A record is a (label, word) pair, such as ("Esc", "back").

keys_for(focus, screen) is what the strip shows on screen: the records of
the focused control (the focus widget or its nearest ancestor with records,
below screen), then the screen's own, one per label, the control's first.
A bound shortcut that is disabled is not listed. While a popup opened from
the screen has the focus (a picker's list, say), only the popup's keys work,
so only its records are shown.

pause_while_focused() switches shortcuts off while a field has the focus,
where they would clash with typing or with the field's own keys.

GridNavigator moves the focus between cards laid out in rows with the
arrow keys; confirm_leave() asks whether to leave a review.

ShortcutStrip is the small strip along the bottom of a screen or dialog. It
follows focus around the app and redraws from keys_for; it wraps onto more
lines when narrow, so it is never clipped. It is reference text: nothing in
it takes focus or clicks.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLayout, QLayoutItem, QScrollArea, QWidget,
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


def bind(owner: QWidget, keys: str | Sequence[str], label: str, word: str,
         slot: Callable[[], None],
         context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
         repeat: bool = True) -> QShortcut:
    """A shortcut for keys (a QKeySequence string, e.g. "Ctrl+Return", or a
    list of them for one action) on owner that calls slot, shown in the strip
    as label and word. With repeat False a held key acts once.

    A key with Return also answers to the keypad's Enter.
    """
    sequences = []
    for key in [keys] if isinstance(keys, str) else keys:
        sequences.append(QKeySequence(key))
        if "Return" in key:
            sequences.append(QKeySequence(key.replace("Return", "Enter")))
    shortcut = QShortcut(owner)
    shortcut.setKeys(sequences)
    shortcut.setContext(context)
    shortcut.setAutoRepeat(repeat)
    shortcut.activated.connect(slot)
    _add(owner, _Record(label, word, shortcut))
    return shortcut


def describe(owner: QWidget, label: str, word: str) -> None:
    """Record a key that owner handles itself, so the strip shows it."""
    _add(owner, _Record(label, word, None))


TAB_LABEL, TAB_WORD = "Tab/Shift+Tab", "area"


def describe_tab(page: QWidget) -> None:
    """Record Tab and Shift+Tab on a review page: the next or the last area
    (the cards, then the buttons); Qt's focus chain does it. The page's
    last record, so it ends the strip."""
    describe(page, TAB_LABEL, TAB_WORD)


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


def _popup_keys(focus: QWidget | None, screen: QWidget) -> list[tuple[str, str]] | None:
    """The records of focus or its nearest recorded ancestor in its popup, if
    focus is in a popup opened from inside screen; else None."""
    if focus is None:
        return None
    popup = focus.window()
    if popup.windowType() != Qt.WindowType.Popup:
        return None
    owner = popup.parentWidget()
    if owner is None or not (owner is screen or screen.isAncestorOf(owner)):
        return None
    widget = focus
    while widget is not None:
        if keys := _live(widget):
            return keys
        if widget is popup:
            break
        widget = widget.parentWidget()
    return []


def keys_for(focus: QWidget | None, screen: QWidget) -> list[tuple[str, str]]:
    """The keys to show on screen while focus has the focus (see the module doc)."""
    popup = _popup_keys(focus, screen)
    if popup is not None:
        return popup
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
        self._drawn_for: QWidget | None = None  # the screen whose keys are shown
        self._shown: list[tuple[str, str]] = []
        QApplication.instance().focusChanged.connect(self._focus_moved)

    def show_for(self, screen: QWidget) -> None:
        """Show screen's keys from now on (with its focused control's)."""
        self._screen = screen
        self.refresh()

    def refresh(self) -> None:
        """Redraw, e.g. after a shortcut is enabled or disabled. While the
        focus is away from the window (another app, a dialog) the screen's
        keys stay as they were, so the page's layout does not change; they
        are redrawn when the focus comes back (see _away)."""
        if not isValid(self):
            return
        focus = QApplication.focusWidget()
        if (self._screen is not None and self._screen is self._drawn_for
                and self._away(focus)):
            return
        keys = [] if self._screen is None else keys_for(focus, self._screen)
        self._drawn_for = self._screen
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

    def _away(self, focus: QWidget | None) -> bool:
        """focus is outside this window (none in the app while another app
        or window is active, or in another window), and not in a popup
        opened from the screen. No focus in the active window is not away."""
        if focus is None:
            return not self.window().isActiveWindow()
        return (focus.window() is not self.window()
                and _popup_keys(focus, self._screen) is None)

    def _focus_moved(self, old: QWidget | None, new: QWidget | None) -> None:
        self.refresh()


class _Pause(QObject):
    """Switches keys off while field has the focus, and on again after."""

    def __init__(self, field: QWidget, keys: list[QShortcut],
                 resume: Callable[[], None] | None) -> None:
        super().__init__(field)
        self._keys = keys
        self._resume = resume
        field.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            for key in self._keys:
                if isValid(key):
                    key.setEnabled(kind == QEvent.Type.FocusOut)
            if kind == QEvent.Type.FocusOut and self._resume is not None:
                self._resume()
        return False


def pause_while_focused(field: QWidget, keys: list[QShortcut],
                        resume: Callable[[], None] | None = None) -> None:
    """Switch keys off while field has the focus (they would clash with typing
    in it, or with its own Enter and Esc), so the strip leaves them out too.
    When the focus leaves, they are switched on, then resume() is called (to
    switch off again any that do not apply)."""
    _Pause(field, keys, resume)


def confirm_leave(parent: QWidget | None) -> bool:
    """Ask whether to leave a review, Stay focused; True for Leave."""
    from photosort.ui import dialogs  # dialogs uses this module

    return dialogs.ask(parent, "Leave this review?", "Your answers so far will be lost.",
                       yes="Leave", no="Stay", default="no")


# Focus reasons for the focus coming back to where it was (the window
# activated again, a popup closed), not moved by the owner.
_COMING_BACK = (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason)

_STEPS = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: 0, Qt.Key.Key_Down: 0}


class GridNavigator(QObject):
    """The arrow keys between cards laid out left to right, then down, in
    rows of columns() (asked at each press, so a reflow is followed).

    The current card is the one focused last; it is the only one Tab stops at
    (the others take focus by click), so the cards are one stop for Tab. ↓
    into a short last row lands on its last card. A card given focus is
    scrolled into view (its bottom, with its answers, when it is taller than
    the view). The view stays pinned to it, scrolled into view again when the
    view changes size (reveal_soon), until the owner scrolls it (the wheel, a
    scrollbar) away; a card taking the focus pins it again (not the focus
    coming back to the current card: the window activated, a popup closed),
    and so do new cards (set_cards()).

    set_cards() swaps in the cards of a reload: the current card stays the
    one with the same key(card) (its identity; by default its place), unless
    a place is given. The navigator does not move the focus on a reload;
    focus() does. With vertical_only, ← and → do nothing (a list).
    """

    current_changed = Signal()

    def __init__(self, cards: list[QWidget], columns: Callable[[], int],
                 key: Callable[[QWidget], object] | None = None,
                 vertical_only: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._key = key
        self._vertical_only = vertical_only
        self.cards: list[QWidget] = []
        self._current = 0
        self._current_key: object = None
        self._pinned = False  # the view follows the focused card (see reveal_soon)
        self._areas: list[QScrollArea] = []  # whose scrollbars unpin it
        self.set_cards(cards, 0)

    def set_cards(self, cards: list[QWidget], current: int | None = None) -> None:
        """Navigate cards from now on; current is the place of the current
        card (None: the card with the current one's key, else its place)."""
        self.cards = list(cards)
        if current is None and self._key is not None:
            current = next((n for n, card in enumerate(self.cards)
                            if self._key(card) == self._current_key), None)
        if current is None:
            current = self._current
        for card in self.cards:
            card.installEventFilter(self)
        self._make_current(max(0, min(current, len(self.cards) - 1)))
        self._pinned = True  # new cards: the view follows the focused one

    def current(self) -> QWidget | None:
        return self.cards[self._current] if self.cards else None

    def current_index(self) -> int:
        return self._current

    def focus(self, n: int | None = None) -> None:
        """Focus card n (the current card if None)."""
        if not self.cards:
            return
        self._make_current(self._current if n is None else max(0, min(n, len(self.cards) - 1)))
        self.cards[self._current].setFocus(Qt.FocusReason.OtherFocusReason)

    def reveal_soon(self) -> None:
        """Scroll the focused card into view again once the layout has
        settled: the view has changed size (a scrollbar came or went, the
        window was resized) since it took the focus. Not once the owner has
        scrolled the view elsewhere: it stays where they put it."""
        QTimer.singleShot(0, self, self._reveal)

    def _reveal(self) -> None:
        card = self.current()
        if self._pinned and card is not None and isValid(card) and card.hasFocus():
            self._scroll_to(card)

    def _unpin(self, _action: int = 0) -> None:
        """The owner scrolled (a scrollbar action: the wheel, a drag, a click
        on the bar), not a setValue() or a range shrinking under the value."""
        self._pinned = False

    def _scroll_to(self, card: QWidget) -> None:
        """Into view in its scroll area; a card taller than the view shows
        its bottom (the answers), not its middle. The view is pinned to it."""
        area = card.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is None:
            return
        if not any(known is area for known in self._areas):
            self._areas = [known for known in self._areas if isValid(known)] + [area]
            for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
                bar.actionTriggered.connect(self._unpin)
        area.ensureWidgetVisible(card, 12, 12)
        if card.height() > area.viewport().height():
            bottom = card.mapTo(area.widget(), QPoint(0, card.height())).y()
            area.verticalScrollBar().setValue(bottom + 4 - area.viewport().height())
        self._pinned = True

    def _make_current(self, n: int) -> None:
        self._current = max(n, 0)
        card = self.current()
        self._current_key = None if card is None or self._key is None else self._key(card)
        for other in self.cards:
            other.setFocusPolicy(Qt.FocusPolicy.StrongFocus if other is card
                                 else Qt.FocusPolicy.ClickFocus)

    def _target(self, n: int, key: int) -> int | None:
        last, columns = len(self.cards) - 1, max(1, self._columns())
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            target = None if self._vertical_only else n + _STEPS[key]
        elif key == Qt.Key.Key_Up:
            target = n - columns
        else:
            target = n + columns
            if target > last and n // columns < last // columns:
                target = last  # the row below is short: its last card
        return target if target is not None and 0 <= target <= last else None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched not in self.cards:
            return False
        kind = event.type()
        if kind == QEvent.Type.FocusIn:
            back = (watched is self.current() and not self._pinned
                    and event.reason() in _COMING_BACK)
            self._make_current(self.cards.index(watched))
            if not back:  # the focus coming back leaves the owner's scroll be
                self._scroll_to(watched)
            self.current_changed.emit()
        elif kind == QEvent.Type.KeyPress and event.key() in _STEPS:
            modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            if modifiers == Qt.KeyboardModifier.NoModifier:
                target = self._target(self.cards.index(watched), event.key())
                if target is not None:
                    self.focus(target)
                return True  # an arrow at the edge stays put, and never scrolls
        return False
