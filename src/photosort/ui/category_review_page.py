"""The review screen of Content and Notes & Pages: the unsure photos in a grid,
each with a picker set to its likeliest category (or kind of paper).

The owner picks a category for each photo, or "Leave it where it is", and
double-clicks one to see it larger. Thumbnails are made on pool threads as on
the review screen (see review_page), tagged with the generation of cards they
were asked for; one for cards that are gone is ignored.

Keys: the arrows move between the cards (a GridNavigator); Enter opens the
focused card's picker (↑↓ choose, Enter set, Esc close, as a list does), L
leaves that photo where it is and Space shows it big. Shift+L leaves them
all, Ctrl+Enter continues to the summary and Esc asks whether to leave
(left()).
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from photosort.modes.content import Verdict
from photosort.thresholds import THUMB_PX
from photosort.ui import shortcuts
from photosort.ui.photo_preview import PhotoPreview
from photosort.ui.review_page import LOADING, render_thumbnail
from photosort.ui.shortcuts import GridNavigator
from photosort.ui.theme import styled
from photosort.ui.worker import Task

LEAVE = "Leave it where it is"
_SPACING = 12  # between the cards of the grid


class _Thumbnail(QLabel):
    """A thumbnail that says when it is double-clicked."""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        event.accept()


class Picker(QComboBox):
    """A card's category picker. It never takes the focus itself (the card
    has it; Enter there opens the list), and its list shows every choice:
    as tall as its rows, up to the screen."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for label, word in (("↑↓", "choose"), ("Enter", "set"), ("Esc", "close")):
            shortcuts.describe(self.view(), label, word)

    def showPopup(self) -> None:
        self.setMaxVisibleItems(max(1, self.count()))
        super().showPopup()
        view = self.view()
        container = view.window()
        rows = sum(view.sizeHintForRow(n) for n in range(self.count()))
        wanted = rows + (container.height() - view.viewport().height())
        room = container.screen().availableGeometry().bottom() - container.y()
        if container.height() < wanted:
            container.resize(container.width(), min(wanted, max(room, container.height())))

    def hidePopup(self) -> None:
        super().hidePopup()
        self.closed.emit()


class CategoryCard(QFrame):
    """One unsure photo: its thumbnail, name, the guess, and the category
    picker. The card takes the focus; Enter on it opens the picker."""

    def __init__(self, root: Path, verdict: Verdict, choices: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.verdict = verdict
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.thumb_label = _Thumbnail(LOADING)
        self.thumb_label.setFixedSize(THUMB_PX, THUMB_PX)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setObjectName("thumb")  # see theme
        self.thumb_label.setToolTip("Double-click to see it larger")

        self.name_label = QLabel(verdict.rel.rpartition("/")[2])
        self.name_label.setToolTip(verdict.rel)
        # Only the best's probability: the others' (beside the background
        # prompts) do not add up to 100 %.
        self.guess_label = QLabel(f"{verdict.best}? {round(verdict.prob * 100)}% sure")
        self.guess_label.setObjectName("muted")
        for label in (self.name_label, self.guess_label):
            label.setFixedWidth(THUMB_PX)
            label.setWordWrap(True)

        self.choice_combo = Picker()
        self.choice_combo.addItems([*choices, LEAVE])
        self.choice_combo.setFixedWidth(THUMB_PX)
        self.choice_combo.closed.connect(self._picked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addWidget(self.thumb_label)
        layout.addWidget(self.name_label)
        layout.addWidget(self.guess_label)
        layout.addWidget(self.choice_combo)
        layout.addStretch()

    def path(self) -> Path:
        return self.root / self.verdict.rel

    def open_picker(self) -> None:
        self.choice_combo.showPopup()

    def _picked(self) -> None:
        if self.isVisible():
            self.setFocus(Qt.FocusReason.PopupFocusReason)  # back from the list

    def set_answer(self, category: str | None) -> None:
        """Pick category, or LEAVE for None (or a category no longer offered)."""
        index = -1 if category is None else self.choice_combo.findText(category)
        self.choice_combo.setCurrentIndex(index if index >= 0 else self.choice_combo.count() - 1)

    def answer(self) -> str | None:
        """The category picked, or None to leave the photo where it is."""
        text = self.choice_combo.currentText()
        return None if text == LEAVE else text

    def set_thumbnail(self, value: QImage | str) -> None:
        """Show the thumbnail, or a placeholder text such as "Can't show". GUI thread only."""
        if isinstance(value, QImage):
            self.thumb_label.setText("")
            self.thumb_label.setPixmap(QPixmap.fromImage(value))
        else:
            self.thumb_label.setText(value)


class CategoryReviewPage(QWidget):
    """Shows the unsure photos; emits finished(answers) or cancelled().

    answers maps each photo's rel to the category picked for it, or to None
    to leave it where it is.
    """

    finished = Signal(object)  # dict[str, str | None]
    cancelled = Signal()  # Back
    left = Signal()  # Esc, then Leave

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._generation = 0  # bumped whenever the cards are replaced
        self._thumb_tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self._columns = 1  # how many columns the cards are laid out in
        self.cards: list[CategoryCard] = []
        self.preview: PhotoPreview | None = None  # while one is open
        self.navigator = GridNavigator([], lambda: self._columns,
                                       key=lambda card: card.verdict.rel, parent=self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 12)
        layout.setSpacing(16)
        self.heading_label = QLabel()
        font = self.heading_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setWeight(QFont.Weight.Bold)
        self.heading_label.setFont(font)
        self.subtitle_label = QLabel(
            "Pick where each one goes, or leave it where it is. "
            "Double-click a photo to see it larger. Nothing moves until you press Go.")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.heading_label)
        layout.addWidget(self.subtitle_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the cards take it
        self.scroll.viewport().installEventFilter(self)  # re-flows the grid on resize
        layout.addWidget(self.scroll, 1)
        self._new_grid()
        self._show_grid()

        self.back_button = QPushButton("Back")
        self.leave_all_button = QPushButton("Leave all where they are")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        row = QHBoxLayout()
        row.addWidget(self.back_button)
        row.addStretch()
        row.addWidget(self.leave_all_button)
        row.addWidget(self.continue_button)
        layout.addLayout(row)

        self.back_button.clicked.connect(self._cancel)
        self.leave_all_button.clicked.connect(self._leave_all)
        self.continue_button.clicked.connect(self._finish)

        # Keys: the cards' own (while one has focus), then the page's.
        shortcuts.describe(self.scroll, "←↑↓→", "move")
        shortcuts.bind(self.scroll, "Return", "Enter", "pick", self._pick_current,
                       repeat=False)
        shortcuts.bind(self.scroll, "L", "L", "leave this one", self._leave_current)
        shortcuts.bind(self.scroll, "Space", "Space", "view big", self._preview_current,
                       repeat=False)
        shortcuts.bind(self, "Shift+L", "Shift+L", "leave all", self._leave_all)
        shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "continue", self._finish,
                       repeat=False)
        shortcuts.bind(self, "Escape", "Esc", "back", self._leave, repeat=False)

    def load(self, root: Path, verdicts: list[Verdict], choices: list[str],
             answers: dict[str, str | None] | None = None, title: str | None = None,
             keep_place: bool = False) -> None:
        """Show verdicts' photos in root under title ("N photos to check" if
        None), each picker offering choices and set to the photo's answer in
        answers if it has one, else to its best. The focus starts on the
        first card, or with keep_place on the card it was on (coming back
        from the summary)."""
        self.stop_thumbnails()
        self._generation += 1
        root = Path(root)
        answers = answers or {}
        n = len(verdicts)
        if title is None:
            title = f"{n} photo{'' if n == 1 else 's'} to check"
        self.heading_label.setText(title)
        self._new_grid()
        self.cards = []
        for i, verdict in enumerate(verdicts):
            card = CategoryCard(root, verdict, choices)
            card.set_answer(answers.get(verdict.rel, verdict.best))
            card.thumb_label.double_clicked.connect(
                lambda path=card.path(): self._open_preview(path))
            self.cards.append(card)
            self._request_thumbnail(i, card.path())
        self.navigator.set_cards(self.cards, None if keep_place else 0)
        self._columns = 0
        self._lay_out(self._fitting_columns())
        self._show_grid()  # the cards shown before are deleted
        if self.isVisible():
            self.navigator.focus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.navigator.focus()  # the first card, or the one the owner was on

    # ----- the grid --------------------------------------------------------

    def _new_grid(self) -> None:
        """An empty grid, not shown yet (see _show_grid)."""
        self._holder = QWidget()
        self._grid = QGridLayout(self._holder)
        self._grid.setSpacing(_SPACING)
        self._grid.setContentsMargins(4, 4, 4, 4)  # room for the focus glow

    def _show_grid(self) -> None:
        """Show the grid, with its cards, in place of the one shown before
        (which is deleted, cards and all). The cards are in it already, so
        they show at once and can take the focus."""
        self.scroll.setWidget(self._holder)

    def _fitting_columns(self) -> int:
        if not self.cards:
            return 1
        self.cards[0].ensurePolished()  # its size with the theme's padding and border
        card_width = self.cards[0].sizeHint().width() + _SPACING
        return max(1, (self.scroll.viewport().width() - 8) // card_width)

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
        grid.setRowStretch(-(-len(self.cards) // columns), 1)
        grid.setColumnStretch(columns, 1)

    def columns(self) -> int:
        return self._columns

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and self.cards:
            self._lay_out(self._fitting_columns())
        return super().eventFilter(watched, event)

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

    # ----- answers ---------------------------------------------------------

    def _leave_all(self) -> None:
        for card in self.cards:
            card.set_answer(None)

    def _pick_current(self) -> None:
        card = self.navigator.current()
        if card is not None:
            card.open_picker()

    def _leave_current(self) -> None:
        card = self.navigator.current()
        if card is not None:
            card.set_answer(None)

    def _preview_current(self) -> None:
        card = self.navigator.current()
        if card is not None:
            self._open_preview(card.path())

    def _open_preview(self, path: Path) -> None:
        self.preview = PhotoPreview(path, self)
        self.preview.finished.connect(self._preview_closed)
        self.preview.open()

    def _preview_closed(self) -> None:
        self.preview = None  # it deletes itself

    def _finish(self) -> None:
        self.stop_thumbnails()
        self.finished.emit({card.verdict.rel: card.answer() for card in self.cards})

    def _cancel(self) -> None:
        self.stop_thumbnails()
        self.cancelled.emit()

    def _leave(self) -> None:
        if shortcuts.confirm_leave(self):
            self.stop_thumbnails()
            self.left.emit()
