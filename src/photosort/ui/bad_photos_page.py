"""The bad photos screen: a grid of the photos found (a CardGrid), each
with why it is bad.

The owner ticks the ones to move to Bad Photos/ (all are ticked at first)
and double-clicks one, or presses Enter on it, to see it larger.

Keys: the arrows move between the cards (a GridNavigator), Space ticks the
focused one and Enter shows it big; A ticks all, U unticks all, Ctrl+Enter
continues to the summary and Esc asks whether to leave (left()). Tab and
Shift+Tab go between the cards and the buttons.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from photosort.groups import Candidate
from photosort.modes import MODE_SPECS
from photosort.naming import resolve_existing_dir
from photosort.ui import shortcuts
from photosort.ui.card_grid import CardGrid, GridCard
from photosort.ui.photo_preview import PhotoPreview
from photosort.ui.theme import page_spacing
from photosort.ui.widgets import ButtonBar

MOVE_TEXT = "Move to Bad Photos"


class BadPhotoCard(GridCard):
    """One bad photo: its thumbnail, why it is bad, its name and folder, and
    the Move tick. The card takes the focus; the tick is Space's."""

    def __init__(self, root: Path, candidate: Candidate, parent: QWidget | None = None) -> None:
        super().__init__(root, candidate.rel, parent)
        self.candidate = candidate

        self.tag_label = QLabel(candidate.note)
        self.tag_label.setObjectName("tag")  # see theme
        self.tag_label.setTextFormat(Qt.TextFormat.PlainText)
        tags = QWidget()
        row = QHBoxLayout(tags)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.tag_label)
        row.addStretch()
        folder, _, name = candidate.rel.rpartition("/")
        self.name_label = QLabel(name)
        self.name_label.setToolTip(candidate.rel)
        self.folder_label = QLabel(folder or "(top folder)")  # the folder it is in
        self.folder_label.setObjectName("muted")
        for label in (self.name_label, self.folder_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)

        self.move_check = QCheckBox(MOVE_TEXT)
        self.move_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space on the card ticks

        for line in (tags, self.name_label, self.folder_label, self.move_check):
            self.add_line(line)
        self.finish_lines()


class BadPhotosPage(QWidget):
    """Shows the bad photos found; emits finished(rels ticked to move),
    cancelled() (Back) or left() (Esc, then Leave)."""

    finished = Signal(object)  # set[str]
    cancelled = Signal()
    left = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cards: list[BadPhotoCard] = []

        layout = QVBoxLayout(self)
        page_spacing(layout)
        top = QHBoxLayout()
        self.heading_label = QLabel()
        font = self.heading_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setWeight(QFont.Weight.Bold)
        self.heading_label.setFont(font)
        self.ticked_label = QLabel()
        self.ticked_label.setObjectName("muted")
        top.addWidget(self.heading_label, 1)
        top.addWidget(self.ticked_label)
        self.subtitle_label = QLabel()
        self.subtitle_label.setWordWrap(True)
        layout.addLayout(top)
        layout.addWidget(self.subtitle_label)

        self.scroll = CardGrid(key=lambda card: card.candidate.rel)
        self.navigator = self.scroll.navigator
        layout.addWidget(self.scroll, 1)

        self.back_button = QPushButton("Back")
        self.tick_all_button = QPushButton("Tick all")
        self.untick_all_button = QPushButton("Untick all")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        layout.addWidget(ButtonBar(
            [self.back_button],
            [self.tick_all_button, self.untick_all_button, self.continue_button]))

        self.back_button.clicked.connect(self._cancel)
        self.tick_all_button.clicked.connect(lambda: self._tick_all(True))
        self.untick_all_button.clicked.connect(lambda: self._tick_all(False))
        self.continue_button.clicked.connect(self._finish)

        # Keys: the cards' own (while one has focus), then the page's.
        shortcuts.describe(self.scroll, "←↑↓→", "move")
        shortcuts.bind(self.scroll, "Space", "Space", "tick", self._toggle_current,
                       repeat=False)
        shortcuts.bind(self.scroll, "Return", "Enter", "view big", self.scroll.preview_current,
                       repeat=False)
        shortcuts.bind(self, "A", "A", "tick all", lambda: self._tick_all(True))
        shortcuts.bind(self, "U", "U", "untick all", lambda: self._tick_all(False))
        shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "continue", self._finish,
                       repeat=False)
        shortcuts.bind(self, "Escape", "Esc", "back", self._leave, repeat=False)
        shortcuts.describe_tab(self)

    def load(self, root: Path, bad: list[Candidate], move: set[str] | None = None,
             keep_place: bool = False) -> None:
        """Show the bad photos found in root, those in move ticked (all if
        None). The focus starts on the first card, or with keep_place on the
        card it was on (coming back from the summary)."""
        root = Path(root)
        ticked = {c.rel for c in bad} if move is None else move
        self.heading_label.setText(f"{len(bad)} bad photo{'' if len(bad) == 1 else 's'}")
        folder = resolve_existing_dir(root, MODE_SPECS["bad"].set_aside_folder)
        self.subtitle_label.setText(
            f"Tick the ones to move. They go to {folder}/ (nothing is deleted). "
            "Double-click a photo to see it larger.")
        self.cards = []
        for candidate in bad:
            card = BadPhotoCard(root, candidate)
            card.move_check.setChecked(candidate.rel in ticked)
            card.move_check.toggled.connect(self._update_ticked)
            self.cards.append(card)
        self.scroll.set_cards(self.cards, keep_place)  # the cards shown before are deleted
        self._update_ticked()

    @property
    def preview(self) -> PhotoPreview | None:
        """The photo shown big, while it is open."""
        return self.scroll.preview

    def columns(self) -> int:
        return self.scroll.columns()

    def stop_thumbnails(self) -> None:
        self.scroll.stop_thumbnails()

    # ----- answers ---------------------------------------------------------

    def _update_ticked(self, *_) -> None:
        n = sum(card.move_check.isChecked() for card in self.cards)
        self.ticked_label.setText(f"{n} will be set aside")

    def _tick_all(self, ticked: bool) -> None:
        for card in self.cards:
            card.move_check.setChecked(ticked)

    def _toggle_current(self) -> None:
        card = self.navigator.current()
        if card is not None:
            card.move_check.toggle()

    def _finish(self) -> None:
        self.stop_thumbnails()
        self.finished.emit({c.candidate.rel for c in self.cards if c.move_check.isChecked()})

    def _cancel(self) -> None:
        self.stop_thumbnails()
        self.cancelled.emit()

    def _leave(self) -> None:
        if shortcuts.confirm_leave(self):
            self.stop_thumbnails()
            self.left.emit()
