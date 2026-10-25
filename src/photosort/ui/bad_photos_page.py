"""The bad photos screen: a grid of the photos found, each with why it is bad.

The owner ticks the ones to move to Bad Photos/ (all are ticked at first)
and double-clicks one to see it larger. Thumbnails are made on pool threads
as on the review screen (see review_page), tagged with the generation of
cards they were asked for; one for cards that are gone is ignored.
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from photosort.groups import Candidate
from photosort.modes import MODE_SPECS
from photosort.naming import resolve_existing_dir
from photosort.thresholds import THUMB_PX
from photosort.ui.photo_preview import PhotoPreview
from photosort.ui.review_page import LOADING, render_thumbnail
from photosort.ui.worker import Task

MOVE_TEXT = "Move to Bad Photos"
_SPACING = 12  # between the cards of the grid


class _Thumbnail(QLabel):
    """A thumbnail that says when it is double-clicked."""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        event.accept()


class BadPhotoCard(QFrame):
    """One bad photo: its thumbnail, name, reason and folder, and the Move tick."""

    def __init__(self, root: Path, candidate: Candidate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.candidate = candidate
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.thumb_label = _Thumbnail(LOADING)
        self.thumb_label.setFixedSize(THUMB_PX, THUMB_PX)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setObjectName("thumb")  # see theme
        self.thumb_label.setToolTip("Double-click to see it larger")

        folder, _, name = candidate.rel.rpartition("/")
        self.details_label = QLabel("\n".join([name, candidate.note, folder or "(top folder)"]))
        self.details_label.setFixedWidth(THUMB_PX)
        self.details_label.setWordWrap(True)
        self.details_label.setToolTip(candidate.rel)

        self.move_check = QCheckBox(MOVE_TEXT)

        layout = QVBoxLayout(self)
        layout.addWidget(self.thumb_label)
        layout.addWidget(self.details_label)
        layout.addWidget(self.move_check)
        layout.addStretch()

    def path(self) -> Path:
        return self.root / self.candidate.rel

    def set_thumbnail(self, value: QImage | str) -> None:
        """Show the thumbnail, or a placeholder text such as "Can't show". GUI thread only."""
        if isinstance(value, QImage):
            self.thumb_label.setText("")
            self.thumb_label.setPixmap(QPixmap.fromImage(value))
        else:
            self.thumb_label.setText(value)


class BadPhotosPage(QWidget):
    """Shows the bad photos found; emits finished(rels ticked to move) or cancelled()."""

    finished = Signal(object)  # set[str]
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._generation = 0  # bumped whenever the cards are replaced
        self._thumb_tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self._columns = 0  # how many columns the cards are laid out in
        self.cards: list[BadPhotoCard] = []
        self.preview: PhotoPreview | None = None  # while one is open

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)
        self.heading_label = QLabel()
        font = self.heading_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setWeight(QFont.Weight.Bold)
        self.heading_label.setFont(font)
        self.subtitle_label = QLabel()
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.heading_label)
        layout.addWidget(self.subtitle_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().installEventFilter(self)  # re-flows the grid on resize
        layout.addWidget(self.scroll, 1)

        self.back_button = QPushButton("Back")
        self.tick_all_button = QPushButton("Tick all")
        self.untick_all_button = QPushButton("Untick all")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        row = QHBoxLayout()
        row.addWidget(self.back_button)
        row.addStretch()
        for button in (self.tick_all_button, self.untick_all_button, self.continue_button):
            row.addWidget(button)
        layout.addLayout(row)

        self.back_button.clicked.connect(self._cancel)
        self.tick_all_button.clicked.connect(lambda: self._tick_all(True))
        self.untick_all_button.clicked.connect(lambda: self._tick_all(False))
        self.continue_button.clicked.connect(self._finish)

    def load(self, root: Path, bad: list[Candidate], move: set[str] | None = None) -> None:
        """Show the bad photos found in root, those in move ticked (all if None)."""
        self.stop_thumbnails()
        self._generation += 1
        root = Path(root)
        ticked = {c.rel for c in bad} if move is None else move
        self.heading_label.setText(f"{len(bad)} bad photo{'' if len(bad) == 1 else 's'}")
        folder = resolve_existing_dir(root, MODE_SPECS["bad"].set_aside_folder)
        self.subtitle_label.setText(
            f"Tick the ones to move. They go to {folder}/ (nothing is deleted). "
            "Double-click a photo to see it larger.")
        self.cards = []
        for n, candidate in enumerate(bad):
            card = BadPhotoCard(root, candidate)
            card.move_check.setChecked(candidate.rel in ticked)
            card.thumb_label.double_clicked.connect(
                lambda path=card.path(): self._open_preview(path))
            self.cards.append(card)
            self._request_thumbnail(n, card.path())
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

    def _tick_all(self, ticked: bool) -> None:
        for card in self.cards:
            card.move_check.setChecked(ticked)

    def _open_preview(self, path: Path) -> None:
        self.preview = PhotoPreview(path, self)
        self.preview.finished.connect(self._preview_closed)
        self.preview.open()

    def _preview_closed(self) -> None:
        self.preview = None  # it deletes itself

    def _finish(self) -> None:
        self.stop_thumbnails()
        self.finished.emit({c.candidate.rel for c in self.cards if c.move_check.isChecked()})

    def _cancel(self) -> None:
        self.stop_thumbnails()
        self.cancelled.emit()
