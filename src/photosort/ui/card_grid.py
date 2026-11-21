"""The grid of photo cards that Bad photos and Content / Notes review show.

A CardGrid lays its cards out in as many columns as fit, the cards
stretched evenly so that the columns fill the width (a thumbnail shrinks
from THUMB_PX down to MIN_THUMB_PX before a column goes). In a short window
the thumbnails shrink further (to SHORTEST_THUMB_PX at least) so that a
whole card, its answers too, fits the view. It re-flows when
the window is resized; the cards stay in the same grid, so the focused one
keeps the focus, and its GridNavigator (the arrow keys) follows the
columns shown.

Thumbnails are made on pool threads (see review_page.render_thumbnail; the
global pool, or the one given), tagged with the generation of cards they
were asked for and the card's number; one for cards that are gone is
ignored. A card that has no thumbnail to make (has_thumbnail) keeps its
text. A card's photo is shown big (a PhotoPreview) when its thumbnail is
double-clicked (card_double_clicked), or by preview_current().

insert_card() and remove_cards() change the cards in place: the others keep
their thumbnails, and the focus stays on the current card (or the one now
in its place).
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QThreadPool
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFrame, QGridLayout, QScrollArea, QVBoxLayout, QWidget

from photosort.thresholds import THUMB_PX
from photosort.ui.photo_preview import PhotoPreview
from photosort.ui.review_page import LOADING, render_thumbnail
from photosort.ui.shortcuts import GridNavigator
from photosort.ui.theme import CARD_LINE_SPACING, styled
from photosort.ui.widgets import Thumbnail, fitting_thumb_side, needed_height
from photosort.ui.worker import Task

MIN_THUMB_PX = 200  # a thumbnail shrinks to this before a column goes
SPACING = 12  # between the cards
_MARGIN = 4  # round the grid: room for the focus glow


class GridCard(QFrame):
    """A card of a CardGrid: the photo's thumbnail on top, then the lines a
    subclass adds (add_line), as wide as the card's inside. The card takes
    the focus."""

    def __init__(self, root: Path, rel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.rel = rel
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.thumb_label = Thumbnail(LOADING, THUMB_PX)
        self.thumb_label.setToolTip("Double-click to see it larger")
        self._lines: list[QWidget] = []
        self._line_width = THUMB_PX
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(CARD_LINE_SPACING)
        self._layout.addWidget(self.thumb_label, 0, Qt.AlignmentFlag.AlignHCenter)

    def add_line(self, widget: QWidget) -> None:
        """Add widget under the thumbnail (and the lines added before)."""
        widget.setFixedWidth(self._line_width)
        self._lines.append(widget)
        self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)

    def finish_lines(self) -> None:
        """No more lines: the rest of the card is empty."""
        self._layout.addStretch()

    def side(self) -> int:
        return self.thumb_label.minimumWidth()

    def line_width(self) -> int:
        return self._line_width

    def set_sizes(self, side: int, line_width: int) -> None:
        """The thumbnail's side, and the lines' width, in px."""
        self.thumb_label.set_side(side)
        if line_width != self._line_width:
            self._line_width = line_width
            for line in self._lines:
                line.setFixedWidth(line_width)

    def path(self) -> Path:
        return self.root / self.rel

    def has_thumbnail(self) -> bool:
        """Whether a thumbnail is made for the card (else its text stays)."""
        return True

    def set_thumbnail(self, value: QImage | str) -> None:
        """Show the thumbnail, or a placeholder text such as "Can't show". GUI thread only."""
        if isinstance(value, QImage):
            self.thumb_label.set_image(value)
        else:
            self.thumb_label.setText(value)


class CardGrid(QScrollArea):
    """The cards in columns that fill the width, scrolling down (see the
    module doc). key(card) is a card's identity across set_cards() calls."""

    def __init__(self, key, parent: QWidget | None = None,
                 pool: QThreadPool | None = None) -> None:
        super().__init__(parent)
        self._pool = pool  # where thumbnails are made (None: the global pool)
        self.setWidgetResizable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the cards take it
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # they fit
        self.viewport().installEventFilter(self)  # re-flows on resize
        self.cards: list[GridCard] = []
        self._columns = 0
        self._generation = 0  # bumped whenever the cards are replaced
        self._thumb_tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self._thumb_cards: dict[int, GridCard] = {}  # by number, waiting for a thumbnail
        self._numbers = 0  # the next card's number
        self.preview: PhotoPreview | None = None  # while one is open
        self.navigator = GridNavigator([], self.columns, key=key, parent=self)
        self._new_grid()
        self.setWidget(self._holder)

    def set_cards(self, cards: list[GridCard], keep_place: bool = False,
                  place: int = 0) -> None:
        """Show cards in place of those shown before (which are deleted), and
        ask for their thumbnails. The focus starts on the card at place (the
        first), or with keep_place on the one with the current card's key."""
        self.stop_thumbnails()
        self._generation += 1
        self._new_grid()
        self.cards = list(cards)
        self.navigator.set_cards(self.cards, None if keep_place else place)
        self._columns = 0
        self._fit()
        self.setWidget(self._holder)  # the cards are in it already: they show at once
        self._thumb_cards = {}
        self._numbers = 0
        for card in self.cards:
            self._wire(card)
        if self.isVisible():
            self.navigator.focus()

    def _wire(self, card: GridCard) -> None:
        card.thumb_label.double_clicked.connect(lambda card=card: self.card_double_clicked(card))
        n, self._numbers = self._numbers, self._numbers + 1
        if card.has_thumbnail():
            self._thumb_cards[n] = card
            self._request_thumbnail(n, card.path())

    def card_double_clicked(self, card: GridCard) -> None:
        """Its photo, big."""
        self.open_preview(card.path())

    def insert_card(self, place: int, card: GridCard) -> None:
        """Add card at place among the cards; its thumbnail is asked for."""
        self.insert_cards([(place, card)])

    def insert_cards(self, placed: list[tuple[int, GridCard]]) -> None:
        """Add each (place, card), in turn (a place counts the cards added
        before it), then lay the cards out once."""
        for place, card in placed:
            self.cards.insert(max(0, min(place, len(self.cards))), card)
            self._wire(card)
        if placed:
            self._changed()

    def remove_cards(self, cards: list[GridCard]) -> None:
        """Take cards out (they are deleted); a thumbnail still to come for
        one is not made."""
        gone = {id(card) for card in cards}
        if not gone:
            return
        had_focus = any(card.hasFocus() for card in cards)
        self.cards = [card for card in self.cards if id(card) not in gone]
        for n, card in list(self._thumb_cards.items()):
            if id(card) in gone:
                del self._thumb_cards[n]
                task = self._thumb_tasks.get((self._generation, n))
                if task is not None:
                    task.cancel_event.set()
        self._changed(focus=had_focus)
        for card in cards:
            self._grid.removeWidget(card)
            card.hide()
            card.deleteLater()

    def _changed(self, focus: bool = False) -> None:
        """The cards changed in place: lay them out again; the current card
        stays (by its key), or the one now in its place. With focus, the
        current card takes the focus (the focused one has gone)."""
        self.navigator.set_cards(self.cards)
        self._columns = 0
        self._fit()
        if focus and self.isVisible():
            self.navigator.focus()
        self.navigator.reveal_soon()  # once laid out again

    def current(self) -> GridCard | None:
        return self.navigator.current()

    def columns(self) -> int:
        return max(1, self._columns)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.navigator.focus()  # the first card, or the one the owner was on

    # ----- the layout ------------------------------------------------------

    def _new_grid(self) -> None:
        self._holder = QWidget()
        self._grid = QGridLayout(self._holder)
        self._grid.setSpacing(SPACING)
        self._grid.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)

    def _fit(self) -> None:
        """As many columns as fit the width, the cards stretched to fill it;
        the thumbnails as big as the card's width and the view's height let
        them be."""
        if not self.cards:
            return
        first = self.cards[0]
        first.ensurePolished()  # its size with the theme's padding and border
        chrome = first.sizeHint().width() - first.line_width()
        room = self.viewport().width() - 2 * _MARGIN
        columns = max(1, (room + SPACING) // (MIN_THUMB_PX + chrome + SPACING))
        width = max(MIN_THUMB_PX + chrome, (room - (columns - 1) * SPACING) // columns)
        inside = width - chrome
        for card in self.cards:
            card.setFixedWidth(width)
            card.set_sizes(card.side(), inside)
        rest = max(needed_height(card) - card.side() for card in self.cards)
        side = fitting_thumb_side(min(THUMB_PX, inside), rest,
                                  self.viewport().height() - 2 * _MARGIN)
        for card in self.cards:
            card.set_sizes(side, inside)
        self._lay_out(columns)

    def _lay_out(self, columns: int) -> None:
        """Lay the cards out in columns. They stay in the same grid, so the
        focused card keeps the focus."""
        if columns == self._columns:
            return
        self._columns = columns
        grid = self._grid
        for card in self.cards:
            grid.removeWidget(card)
        for n in range(grid.rowCount()):
            grid.setRowStretch(n, 0)
        for n in range(grid.columnCount()):
            grid.setColumnStretch(n, 0)
        for n, card in enumerate(self.cards):
            grid.addWidget(card, n // columns, n % columns)
        grid.setRowStretch(-(-len(self.cards) // columns), 1)  # the cards at the top
        grid.setColumnStretch(columns, 1)  # what the rounding leaves

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self._fit()
            self.navigator.reveal_soon()  # the focused card's answers, in the new view
        return super().eventFilter(watched, event)

    # ----- the photo big ---------------------------------------------------

    def preview_current(self) -> None:
        card = self.current()
        if card is not None:
            self.open_preview(card.path())

    def open_preview(self, path: Path) -> None:
        self.preview = PhotoPreview(path, self)
        self.preview.finished.connect(self._preview_closed)
        self.preview.open()

    def _preview_closed(self) -> None:
        self.preview = None  # it deletes itself

    # ----- thumbnails ------------------------------------------------------

    def _request_thumbnail(self, n: int, path: Path) -> None:
        token = (self._generation, n)
        task = Task(render_thumbnail, token, path)
        task.signals.result.connect(self._thumbnail_ready)
        self._thumb_tasks[token] = task
        task.start(self._pool)

    def _thumbnail_ready(self, result: tuple[int, int, QImage | str | None]) -> None:
        generation, n, value = result
        self._thumb_tasks.pop((generation, n), None)
        if generation != self._generation or value is None:
            return  # asked for cards that are gone
        card = self._thumb_cards.pop(n, None)
        if card is not None:
            card.set_thumbnail(value)

    def stop_thumbnails(self) -> None:
        """Let the thumbnails still waiting for a pool thread finish without decoding."""
        for task in self._thumb_tasks.values():
            task.cancel_event.set()
