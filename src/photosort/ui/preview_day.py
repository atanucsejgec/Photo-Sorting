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
"""

import bisect
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

from photosort.modes.preview import file_kind
from photosort.ui import preview_viewers as pv
from photosort.ui import shortcuts
from photosort.ui.card_grid import CardGrid, GridCard

_KIND_WORDS = {"pdf": "PDF", "video": "Video", "audio": "Sound", "text": "Text"}
_TICK_ROOM = 28  # the tick's box and its gap, beside the name


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
        self.tick = QCheckBox()
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
    """The CardGrid of a DayGrid: a double-click asks for the file alone."""

    open_file = Signal(str)

    def card_double_clicked(self, card: GridCard) -> None:
        self.open_file.emit(card.rel)


class DayGrid(QWidget):
    """The files of one day (show_day), each with a tick (see the module doc)."""

    open_file = Signal(str)  # the file to see alone, relative to root
    changed = Signal()  # the cards or their ticks changed

    def __init__(self, sort_key: Callable[[str], object] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path()
        self._sort_key = sort_key or (lambda rel: rel)
        self.scroll = _Cards(key=lambda card: card.rel, pool=pv.image_pool())
        self.scroll.open_file.connect(self.open_file)
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

    def focus(self) -> None:
        """Focus the current card, scrolled into view once the cards are laid out."""
        if self.isVisible():
            self.scroll.navigator.focus()
            self.scroll.navigator.reveal_soon()

    def remove(self, rels) -> None:
        """Take the cards of rels out; the focus stays on the current card, or
        goes to the one now in its place."""
        gone = set(rels)
        self.scroll.remove_cards([card for card in self.scroll.cards if card.rel in gone])
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
