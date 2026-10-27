"""The grid of photo cards that Bad photos and Content / Notes review show.

A CardGrid lays its cards out in as many columns as fit, the cards
stretched evenly so that the columns fill the width (a thumbnail shrinks
from THUMB_PX down to MIN_THUMB_PX before a column goes). In a short window
the thumbnails shrink further (to SHORTEST_THUMB_PX at least) so that a
whole card, its answers too, fits the view. It re-flows when
the window is resized; the cards stay in the same grid, so the focused one
keeps the focus, and its GridNavigator (the arrow keys) follows the
columns shown.

Thumbnails are made on pool threads (see review_page.render_thumbnail),
tagged with the generation of cards they were asked for; one for cards that
are gone is ignored. A card's photo is shown big (a PhotoPreview) when its
thumbnail is double-clicked, or by preview_current().
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
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

    def set_thumbnail(self, value: QImage | str) -> None:
        """Show the thumbnail, or a placeholder text such as "Can't show". GUI thread only."""
        if isinstance(value, QImage):
            self.thumb_label.set_image(value)
        else:
            self.thumb_label.setText(value)


class CardGrid(QScrollArea):
    """The cards in columns that fill the width, scrolling down (see the
    module doc). key(card) is a card's identity across set_cards() calls."""

    def __init__(self, key, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the cards take it
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # they fit
        self.viewport().installEventFilter(self)  # re-flows on resize
        self.cards: list[GridCard] = []
        self._columns = 0
        self._generation = 0  # bumped whenever the cards are replaced
        self._thumb_tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self.preview: PhotoPreview | None = None  # while one is open
        self.navigator = GridNavigator([], self.columns, key=key, parent=self)
        self._new_grid()
        self.setWidget(self._holder)

    def set_cards(self, cards: list[GridCard], keep_place: bool = False) -> None:
        """Show cards in place of those shown before (which are deleted), and
        ask for their thumbnails. The focus starts on the first card, or with
        keep_place on the one with the current card's key."""
        self.stop_thumbnails()
        self._generation += 1
        self._new_grid()
        self.cards = list(cards)
        self.navigator.set_cards(self.cards, None if keep_place else 0)
        self._columns = 0
        self._fit()
        self.setWidget(self._holder)  # the cards are in it already: they show at once
        for n, card in enumerate(self.cards):
            card.thumb_label.double_clicked.connect(
                lambda path=card.path(): self.open_preview(path))
            self._request_thumbnail(n, card.path())
        if self.isVisible():
            self.navigator.focus()

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
        task.start()

    def _thumbnail_ready(self, result: tuple[int, int, QImage | str | None]) -> None:
        generation, n, value = result
        self._thumb_tasks.pop((generation, n), None)
        if generation != self._generation or value is None:
            return  # asked for cards that are gone
        self.cards[n].set_thumbnail(value)

    def stop_thumbnails(self) -> None:
        """Let the thumbnails still waiting for a pool thread finish without decoding."""
        for task in self._thumb_tasks.values():
            task.cancel_event.set()
