"""The review screen of Content and Notes & Pages: the unsure photos in a grid,
each with a picker set to its likeliest category (or kind of paper).

The owner picks a category for each photo, or "Leave it where it is", and
double-clicks one to see it larger. Thumbnails are made on pool threads as on
the review screen (see review_page), tagged with the generation of cards they
were asked for; one for cards that are gone is ignored.
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from photosort.modes.content import Verdict
from photosort.thresholds import THUMB_PX
from photosort.ui.fullscreen_viewer import _render_image
from photosort.ui.review_page import LOADING, render_thumbnail
from photosort.ui.worker import Task

LEAVE = "Leave it where it is"
_SPACING = 12  # between the cards of the grid
_PREVIEW_SHARE = 0.8  # the preview opens this share of the screen wide and high


class _Thumbnail(QLabel):
    """A thumbnail that says when it is double-clicked."""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        event.accept()


class CategoryCard(QFrame):
    """One unsure photo: its thumbnail, name, the guess, and the category picker."""

    def __init__(self, root: Path, verdict: Verdict, choices: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.verdict = verdict
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.thumb_label = _Thumbnail(LOADING)
        self.thumb_label.setFixedSize(THUMB_PX, THUMB_PX)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet("background: #d8d8d8; color: #555;")
        self.thumb_label.setToolTip("Double-click to see it larger")

        self.name_label = QLabel(verdict.rel.rpartition("/")[2])
        self.name_label.setToolTip(verdict.rel)
        # Only the best's probability: the others' (beside the background
        # prompts) do not add up to 100 %.
        self.guess_label = QLabel(f"{verdict.best}? {round(verdict.prob * 100)}% sure")
        for label in (self.name_label, self.guess_label):
            label.setFixedWidth(THUMB_PX)
            label.setWordWrap(True)

        self.choice_combo = QComboBox()
        self.choice_combo.addItems([*choices, LEAVE])
        self.choice_combo.setFixedWidth(THUMB_PX)

        layout = QVBoxLayout(self)
        layout.addWidget(self.thumb_label)
        layout.addWidget(self.name_label)
        layout.addWidget(self.guess_label)
        layout.addWidget(self.choice_combo)
        layout.addStretch()

    def path(self) -> Path:
        return self.root / self.verdict.rel

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


class _PhotoPreview(QDialog):
    """One photo, large and fitted to the window; Esc closes it. The photo is
    decoded on a pool thread; the dialog deletes itself once closed and that
    decode has reported. (Stage 4's ui/photo_preview.PhotoPreview does the
    same and replaces it when the two are merged.)"""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.photo: QPixmap | None = None  # once loaded
        self._closed = False
        self._loading = True

        self.image_label = QLabel(LOADING)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image_label.setStyleSheet("background: #202020; color: #dddddd;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image_label)
        screen = self.screen().availableGeometry()
        self.resize(int(screen.width() * _PREVIEW_SHARE), int(screen.height() * _PREVIEW_SHARE))

        self._task = Task(_render_image, 0, Path(path))  # kept alive until it reports
        self._task.signals.result.connect(self._ready)
        self._task.start()

    def _ready(self, result: tuple[int, QImage | str | None]) -> None:
        self._loading = False
        value = result[1]
        if self._closed:
            self.deleteLater()
        elif isinstance(value, QImage):
            self.photo = QPixmap.fromImage(value)
            self.image_label.setText("")
            self._fit()
        elif value is not None:
            self.image_label.setText(value)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        """Show the photo as large as fits, never larger than it is."""
        photo, size = self.photo, self.image_label.size()
        if photo is None:
            return
        if photo.width() > size.width() or photo.height() > size.height():
            photo = photo.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(photo)

    def done(self, result: int) -> None:
        super().done(result)
        self._closed = True
        self._task.cancel_event.set()  # not decoded if still waiting for a thread
        if not self._loading:
            self.deleteLater()


class CategoryReviewPage(QWidget):
    """Shows the unsure photos; emits finished(answers) or cancelled().

    answers maps each photo's rel to the category picked for it, or to None
    to leave it where it is.
    """

    finished = Signal(object)  # dict[str, str | None]
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._generation = 0  # bumped whenever the cards are replaced
        self._thumb_tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self._columns = 0  # how many columns the cards are laid out in
        self.cards: list[CategoryCard] = []
        self.preview: _PhotoPreview | None = None  # while one is open

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
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
        self.scroll.viewport().installEventFilter(self)  # re-flows the grid on resize
        layout.addWidget(self.scroll, 1)

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

    def load(self, root: Path, verdicts: list[Verdict], choices: list[str],
             answers: dict[str, str | None] | None = None) -> None:
        """Show verdicts' photos in root, each picker offering choices and set
        to the photo's answer in answers if it has one, else to its best."""
        self.stop_thumbnails()
        self._generation += 1
        root = Path(root)
        answers = answers or {}
        n = len(verdicts)
        self.heading_label.setText(f"{n} photo{'' if n == 1 else 's'} to check")
        self.cards = []
        for i, verdict in enumerate(verdicts):
            card = CategoryCard(root, verdict, choices)
            card.set_answer(answers.get(verdict.rel, verdict.best))
            card.thumb_label.double_clicked.connect(
                lambda path=card.path(): self._open_preview(path))
            self.cards.append(card)
            self._request_thumbnail(i, card.path())
        self._lay_out(self._fitting_columns())

    # ----- the grid --------------------------------------------------------

    def _fitting_columns(self) -> int:
        if not self.cards:
            return 1
        card_width = self.cards[0].sizeHint().width() + _SPACING
        return max(1, self.scroll.viewport().width() // card_width)

    def _lay_out(self, columns: int) -> None:
        """Put the cards in a fresh grid of columns; the old grid is deleted."""
        self._columns = columns
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(_SPACING)
        for n, card in enumerate(self.cards):
            grid.addWidget(card, n // columns, n % columns)
        grid.setRowStretch(grid.rowCount(), 1)
        grid.setColumnStretch(columns, 1)
        self.scroll.setWidget(grid_widget)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and self.cards:
            columns = self._fitting_columns()
            if columns != self._columns:
                self._lay_out(columns)
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

    def _open_preview(self, path: Path) -> None:
        self.preview = _PhotoPreview(path, self)
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
