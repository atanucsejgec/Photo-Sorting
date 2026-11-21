"""Preview's day grid: every file of one day at once, each with a tick (see
preview_page for the page that shows it, and moves the ticked files).

A DayGrid is a CardGrid of DayCards, in the order it is given (the page
orders a day's files by their modified time; insert() keeps that order with
the sort key the grid was made with). A photo's card shows its thumbnail
(made on the Preview's one-thread image pool, so a big day never decodes
more than one at a time; a day changed meanwhile drops the late ones);
another file's card shows its kind ("PDF", "Video", "ZIP file"…). Under it,
the tick with the file's name.

Keys (on the grid, while a card has the focus): the arrows move between the
cards, Space ticks or unticks the focused one (a click on the tick does
too), A ticks all, or unticks all when all are ticked, and Enter (or a
double-click) asks for the focused file alone (open_file). changed says
that the cards or their ticks changed.

The mouse ticks as a file manager selects (see _Cards): a box dragged from a
gap or a card's picture or name ticks only the cards it touches (with Ctrl,
flips them, against the ticks at its start), Ctrl+click flips a card, and
Shift+click gives the cards from the anchor (the card clicked last, else the
focused one) to this one the anchor's tick. A click on the tick's box flips
it; a click elsewhere (moved less than the drag distance) focuses the card.
Dragging past the top or bottom scrolls, faster the further out.
"""

import bisect
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QStyle, QStyleOptionButton, QVBoxLayout, QWidget,
)

from photosort.modes.preview import file_kind
from photosort.ui import preview_viewers as pv
from photosort.ui import shortcuts
from photosort.ui.card_grid import CardGrid, GridCard
from photosort.ui.theme import COLORS

_KIND_WORDS = {"pdf": "PDF", "video": "Video", "audio": "Sound", "text": "Text"}
_TICK_ROOM = 28  # the tick's box and its gap, beside the name
_BOX_SLACK = 3  # px round the tick's box that still click it
_SCROLL_MS = 30  # a drag past the edge scrolls this often…
_SCROLL_MIN, _SCROLL_MAX = 4, 60  # …by this many px at least and at most


def kind_word(path: Path) -> str | None:
    """What a card shows in place of a thumbnail ("PDF", "ZIP file", "File"):
    None for a photo, which shows its thumbnail."""
    kind = file_kind(path)
    if kind == "image":
        return None
    if kind in _KIND_WORDS:
        return _KIND_WORDS[kind]
    ext = Path(path).suffix.lstrip(".")
    return f"{ext.upper()} file" if ext else "File"


class _Tick(QCheckBox):
    """A card's tick: only its box (and a little round it) takes a click; a
    press on the name is the card's (a click focuses it, a drag boxes)."""

    def box_rect(self) -> QRect:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)

    def hitButton(self, pos: QPoint) -> bool:
        slack = _BOX_SLACK
        return self.box_rect().adjusted(-slack, -slack, slack, slack).contains(pos)


class _Box(QWidget):
    """The box being dragged: the accent, see-through, with a solid edge. The
    mouse goes through it."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _event) -> None:
        edge = QColor(COLORS["focus"])
        fill = QColor(edge)
        fill.setAlpha(50)
        painter = QPainter(self)
        painter.setPen(edge)
        painter.setBrush(fill)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class DayCard(GridCard):
    """One file of the day: its thumbnail (or its kind), then the tick with
    its name (cut in the middle to fit; the tooltip has it whole, with its
    folder). The card takes the focus; the tick is Space's."""

    def __init__(self, root: Path, rel: str, parent: QWidget | None = None) -> None:
        super().__init__(root, rel, parent)
        self._kind = kind_word(self.path())
        if self._kind is not None:
            self.thumb_label.setText(self._kind)
        self.thumb_label.setToolTip("Double-click to see it alone")
        self.tick = _Tick()
        self.tick.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space on the card ticks
        self.tick.setToolTip(rel)
        self.tick.setChecked(True)
        self.add_line(self.tick)
        self.finish_lines()
        self._show_name()

    def has_thumbnail(self) -> bool:
        return self._kind is None

    def name(self) -> str:
        return self.rel.rpartition("/")[2]

    def set_sizes(self, side: int, line_width: int) -> None:
        super().set_sizes(side, line_width)
        self._show_name()

    def _show_name(self) -> None:
        width = max(20, self.line_width() - _TICK_ROOM)
        self.tick.setText(self.tick.fontMetrics().elidedText(
            self.name(), Qt.TextElideMode.ElideMiddle, width))


class _Cards(CardGrid):
    """The CardGrid of a DayGrid: a double-click asks for the file alone, and
    the mouse ticks (see the module doc; ticked says it did).

    The viewport sees every press the cards leave (all but those on a tick's
    box), as the cards' parents' parent; the box is kept in the grid's
    coordinates, so it grows as the view scrolls under it."""

    open_file = Signal(str)
    ticked = Signal()  # the mouse changed ticks

    def __init__(self, key, pool=None) -> None:
        super().__init__(key, pool=pool)
        self.box = _Box(self.viewport())
        self._start: QPoint | None = None  # the press, in the grid (None: no press)
        self._start_view = QPoint()  # the press, in the viewport
        self._modifiers = Qt.KeyboardModifier.NoModifier  # held at the press
        self._dragging = False
        self._pointer = QPoint()  # in the viewport, the last move
        self._before: dict[str, bool] = {}  # the ticks when the drag began, by rel
        self._anchor: str | None = None  # Shift+click's (the card clicked last)
        self._scroller = QTimer(self)
        self._scroller.setInterval(_SCROLL_MS)
        self._scroller.timeout.connect(self._autoscroll)
        self.navigator.current_changed.connect(self._focus_moved)

    def card_double_clicked(self, card: GridCard) -> None:
        self.open_file.emit(card.rel)

    def set_cards(self, cards: list[GridCard], keep_place: bool = False,
                  place: int = 0) -> None:
        self._end()  # a drag ends with the cards it began on
        self._anchor = None
        super().set_cards(cards, keep_place, place)

    def insert_cards(self, placed: list[tuple[int, GridCard]]) -> None:
        if placed:
            self._end()
        super().insert_cards(placed)

    def remove_cards(self, cards: list[GridCard]) -> None:
        if cards:
            self._end()
        super().remove_cards(cards)

    def hideEvent(self, event) -> None:
        self._end()  # the page left, or the file opened alone, mid-drag
        super().hideEvent(event)

    def _focus_moved(self) -> None:
        """The keys moved the focus (not a press: its release says): the
        focused card is Shift+click's anchor."""
        card = self.current()
        if card is not None and QApplication.mouseButtons() == Qt.MouseButton.NoButton:
            self._anchor = card.rel

    # ----- the mouse --------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport():
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress:
                return self._pressed(event)
            if kind == QEvent.Type.MouseMove and self._start is not None:
                return self._moved(event)
            if (kind == QEvent.Type.MouseButtonRelease and self._start is not None
                    and event.button() == Qt.MouseButton.LeftButton):
                return self._released(event)
        return super().eventFilter(watched, event)

    def _in_grid(self, pos: QPoint) -> QPoint:
        return self.widget().mapFrom(self.viewport(), pos)

    def _card_at(self, pos: QPoint) -> int | None:
        """The place of the card at pos (in the viewport), if any."""
        at = self._in_grid(pos)
        return next((n for n, card in enumerate(self.cards) if card.geometry().contains(at)),
                    None)

    def _on_tick_box(self, pos: QPoint) -> bool:
        n = self._card_at(pos)
        if n is None:
            return False
        tick = self.cards[n].tick
        return tick.hitButton(tick.mapFrom(self.viewport(), pos))

    def _pressed(self, event) -> bool:
        pos = event.position().toPoint()
        if event.button() != Qt.MouseButton.LeftButton or self._on_tick_box(pos):
            return False
        self._end()
        self._start, self._start_view = self._in_grid(pos), pos
        self._modifiers = event.modifiers()
        return True

    def _moved(self, event) -> bool:
        if not event.buttons() & Qt.MouseButton.LeftButton:
            self._end()  # its release went elsewhere
            return False
        self._pointer = event.position().toPoint()
        if not self._dragging:
            if ((self._pointer - self._start_view).manhattanLength()
                    < QApplication.startDragDistance()):
                return True  # a click, so far
            self._dragging = True
            self._before = {card.rel: card.tick.isChecked() for card in self.cards}
            self.box.show()
            self.box.raise_()
            self._scroller.start()
        self._drag_to(self._pointer)
        return True

    def _released(self, event) -> bool:
        pos = event.position().toPoint()
        if self._dragging:
            self._drag_to(pos)
        elif self.isVisible():
            self._clicked(pos)
        self._end()
        return True

    def _end(self) -> None:
        self._start, self._dragging, self._before = None, False, {}
        self._scroller.stop()
        self.box.hide()

    def _drag_to(self, pos: QPoint) -> None:
        """The box from the press to pos (in the viewport): its cards ticked,
        the others not; with Ctrl, its cards flipped, the others as they were."""
        if self._start is None:
            return
        area = QRect(self._start, self._in_grid(pos)).normalized()
        self.box.setGeometry(QRect(self.widget().mapTo(self.viewport(), area.topLeft()),
                                   area.size()))
        flip = bool(self._modifiers & Qt.KeyboardModifier.ControlModifier)
        cards = [card for card in self.cards if card.rel in self._before]  # only the drag's
        states = []
        for card in cards:
            hit = card.geometry().intersects(area)
            states.append((hit != self._before[card.rel]) if flip else hit)
        self._set_ticks(cards, states)

    def _clicked(self, pos: QPoint) -> None:
        """A click (no drag) at pos: Ctrl flips the card, Shift ticks the
        cards from the anchor like it; the card is focused, the new anchor."""
        n = self._card_at(pos)
        if n is None:
            return
        if self._modifiers & Qt.KeyboardModifier.ShiftModifier:
            rels = [card.rel for card in self.cards]
            anchor = (rels.index(self._anchor) if self._anchor in rels
                      else self.navigator.current_index())
            on = self.cards[anchor].tick.isChecked()
            low, high = sorted((anchor, n))
            self._set_ticks(self.cards[low:high + 1], [on] * (high + 1 - low))
        elif self._modifiers & Qt.KeyboardModifier.ControlModifier:
            tick = self.cards[n].tick
            self._set_ticks([self.cards[n]], [not tick.isChecked()])
        self._anchor = self.cards[n].rel
        self.navigator.focus(n)

    def _set_ticks(self, cards: list[GridCard], states: list[bool]) -> None:
        """Tick each card as its state says; ticked once if any changed."""
        changed = False
        for card, on in zip(cards, states):
            if card.tick.isChecked() != on:
                card.tick.blockSignals(True)
                card.tick.setChecked(on)
                card.tick.blockSignals(False)
                changed = True
        if changed:
            self.ticked.emit()

    def _autoscroll(self) -> None:
        """The pointer past the top or bottom: scroll that way, faster the
        further out, and the box grows with it."""
        y, height = self._pointer.y(), self.viewport().height()
        out = y - height if y > height else min(y, 0)
        if out == 0:
            return
        step = min(_SCROLL_MAX, _SCROLL_MIN + abs(out) // 2)
        bar = self.verticalScrollBar()
        old = bar.value()
        bar.setValue(old + (step if out > 0 else -step))
        if bar.value() != old:
            self.navigator.unpin()  # the view stays where the drag took it
            self._drag_to(self._pointer)


class DayGrid(QWidget):
    """The files of one day (show_day), each with a tick (see the module doc)."""

    open_file = Signal(str)  # the file to see alone, relative to root
    changed = Signal()  # the cards or their ticks changed

    def __init__(self, sort_key: Callable[[str], object] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path()
        self._sort_key = sort_key or (lambda rel: rel)
        # An empty grid holds the focus itself, so its keys (and the page's) still work.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.scroll = _Cards(key=lambda card: card.rel, pool=pv.image_pool())
        self.scroll.open_file.connect(self.open_file)
        self.scroll.ticked.connect(self.changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)
        shortcuts.describe(self, "←↑↓→", "move")
        self.keys: list[QShortcut] = [
            shortcuts.bind(self, "Space", "Space", "tick", self.toggle_focused, repeat=False),
            shortcuts.bind(self, "A", "A", "tick all/none", self.tick_all_or_none,
                           repeat=False),
            shortcuts.bind(self, "Return", "Enter", "open alone", self._open_focused,
                           repeat=False),
        ]

    # ----- the cards --------------------------------------------------------------------------

    def show_day(self, root: Path, rels: list[str], ticked: set[str] | None = None,
                 focus: str | None = None) -> None:
        """Show rels (relative to root, in order), those in ticked ticked (all
        if None), the focus on focus (the first card if None or not there).
        The cards shown before go, and their late thumbnails with them."""
        self.root = Path(root)
        cards = [self._card(rel, ticked is None or rel in ticked) for rel in rels]
        place = next((n for n, rel in enumerate(rels) if rel == focus), 0)
        self.scroll.set_cards(cards, place=place)
        self.focus()
        self.changed.emit()

    def clear(self) -> None:
        """No cards (their thumbnails still to come are not made)."""
        if self.scroll.cards:
            self.scroll.stop_thumbnails()
            self.scroll.set_cards([])
            self.changed.emit()

    def stop_thumbnails(self) -> None:
        self.scroll.stop_thumbnails()

    def _card(self, rel: str, ticked: bool) -> DayCard:
        card = DayCard(self.root, rel)
        card.tick.setChecked(ticked)
        card.tick.toggled.connect(lambda _on: self.changed.emit())
        return card

    def cards(self) -> list[DayCard]:
        return list(self.scroll.cards)

    def card(self, rel: str) -> DayCard | None:
        return next((card for card in self.scroll.cards if card.rel == rel), None)

    def rels(self) -> list[str]:
        """The files shown, in order."""
        return [card.rel for card in self.scroll.cards]

    def ticked(self) -> list[str]:
        """The files ticked, in order."""
        return [card.rel for card in self.scroll.cards if card.tick.isChecked()]

    def focused(self) -> str | None:
        """The current card's file (the one focused, or last focused)."""
        card = self.scroll.current()
        return None if card is None else card.rel

    def focus(self, rel: str | None = None) -> None:
        """Focus rel's card (the current card if None or not shown), scrolled
        into view once the cards are laid out; an empty grid takes the focus
        itself."""
        if not self.isVisible():
            return
        if not self.scroll.cards:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        rels = self.rels()
        self.scroll.navigator.focus(rels.index(rel) if rel in rels else None)
        self.scroll.navigator.reveal_soon()

    def has_focus_inside(self) -> bool:
        """Whether the focus is on the grid or one of its cards."""
        focus = QApplication.focusWidget()
        return focus is not None and (focus is self or self.isAncestorOf(focus))

    def remove(self, rels) -> None:
        """Take the cards of rels out; the focus stays on the current card, or
        goes to the one now in its place."""
        gone = set(rels)
        had_focus = self.has_focus_inside()
        self.scroll.remove_cards([card for card in self.scroll.cards if card.rel in gone])
        if had_focus and not self.scroll.cards:
            self.focus()  # the grid itself: Ctrl+Z, PgUp/PgDn still work
        self.changed.emit()

    def insert(self, rel: str, ticked: bool) -> None:
        """Add a card for rel in its place (by the sort key), ticked or not."""
        self.insert_many([rel], ticked)

    def insert_many(self, rels: list[str], ticked: bool) -> None:
        """Add a card for each of rels in its place (by the sort key), ticked
        or not, laid out once (a big group put back)."""
        shown = set(self.rels())
        new = sorted({rel for rel in rels if rel not in shown}, key=self._sort_key)
        if not new:
            return
        keys = [self._sort_key(card.rel) for card in self.scroll.cards]
        placed = []
        for rel in new:
            key = self._sort_key(rel)
            place = bisect.bisect_left(keys, key)
            keys.insert(place, key)
            placed.append((place, self._card(rel, ticked)))
        self.scroll.insert_cards(placed)
        self.changed.emit()

    # ----- ticks ------------------------------------------------------------------------------

    def toggle_focused(self) -> None:
        card = self.scroll.current()
        if card is not None:
            card.tick.toggle()

    def tick_all_or_none(self) -> None:
        """Tick every card, or untick them all when all are ticked."""
        cards = self.scroll.cards
        on = not all(card.tick.isChecked() for card in cards)
        for card in cards:
            card.tick.blockSignals(True)
            card.tick.setChecked(on)
            card.tick.blockSignals(False)
        self.changed.emit()

    def _open_focused(self) -> None:
        rel = self.focused()
        if rel is not None:
            self.open_file.emit(rel)
