"""The review screen of Content and Notes & Pages: the unsure photos in a grid,
each with a picker set to its likeliest category (or kind of paper).

The owner picks a category for each photo, or "Leave it where it is", and
double-clicks one to see it larger. The cards are in a CardGrid.

Keys: the arrows move between the cards (a GridNavigator); Enter opens the
focused card's picker (↑↓ choose, Enter set, Esc close, as a list does), L
leaves that photo where it is and Space shows it big. Shift+L leaves them
all, Ctrl+Enter continues to the summary and Esc asks whether to leave
(left()). Tab and Shift+Tab go between the cards and the buttons.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget

from photosort.modes.content import Verdict
from photosort.ui import shortcuts
from photosort.ui.card_grid import CardGrid, GridCard
from photosort.ui.photo_preview import PhotoPreview
from photosort.ui.theme import page_spacing
from photosort.ui.widgets import ButtonBar

LEAVE = "Leave it where it is"


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


class CategoryCard(GridCard):
    """One unsure photo: its thumbnail, name, the guess, and the category
    picker. The card takes the focus; Enter on it opens the picker."""

    def __init__(self, root: Path, verdict: Verdict, choices: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(root, verdict.rel, parent)
        self.verdict = verdict

        self.name_label = QLabel(verdict.rel.rpartition("/")[2])
        self.name_label.setToolTip(verdict.rel)
        # Only the best's probability: the others' (beside the background
        # prompts) do not add up to 100 %.
        self.guess_label = QLabel(f"{verdict.best}? {round(verdict.prob * 100)}% sure")
        self.guess_label.setObjectName("muted")
        for label in (self.name_label, self.guess_label):
            label.setWordWrap(True)

        self.choice_combo = Picker()
        self.choice_combo.addItems([*choices, LEAVE])
        self.choice_combo.closed.connect(self._picked)

        for line in (self.name_label, self.guess_label, self.choice_combo):
            self.add_line(line)
        self.finish_lines()

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
        self.cards: list[CategoryCard] = []

        layout = QVBoxLayout(self)
        page_spacing(layout)
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

        self.scroll = CardGrid(key=lambda card: card.verdict.rel)
        self.navigator = self.scroll.navigator
        layout.addWidget(self.scroll, 1)

        self.back_button = QPushButton("Back")
        self.leave_all_button = QPushButton("Leave all where they are")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        layout.addWidget(ButtonBar([self.back_button],
                                   [self.leave_all_button, self.continue_button]))

        self.back_button.clicked.connect(self._cancel)
        self.leave_all_button.clicked.connect(self._leave_all)
        self.continue_button.clicked.connect(self._finish)

        # Keys: the cards' own (while one has focus), then the page's.
        shortcuts.describe(self.scroll, "←↑↓→", "move")
        shortcuts.bind(self.scroll, "Return", "Enter", "pick", self._pick_current,
                       repeat=False)
        shortcuts.bind(self.scroll, "L", "L", "leave this one", self._leave_current)
        shortcuts.bind(self.scroll, "Space", "Space", "view big", self.scroll.preview_current,
                       repeat=False)
        shortcuts.bind(self, "Shift+L", "Shift+L", "leave all", self._leave_all)
        shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "continue", self._finish,
                       repeat=False)
        shortcuts.bind(self, "Escape", "Esc", "back", self._leave, repeat=False)
        shortcuts.describe_tab(self)

    def load(self, root: Path, verdicts: list[Verdict], choices: list[str],
             answers: dict[str, str | None] | None = None, title: str | None = None,
             keep_place: bool = False) -> None:
        """Show verdicts' photos in root under title ("N photos to check" if
        None), each picker offering choices and set to the photo's answer in
        answers if it has one, else to its best. The focus starts on the
        first card, or with keep_place on the card it was on (coming back
        from the summary)."""
        root = Path(root)
        answers = answers or {}
        n = len(verdicts)
        if title is None:
            title = f"{n} photo{'' if n == 1 else 's'} to check"
        self.heading_label.setText(title)
        self.cards = []
        for verdict in verdicts:
            card = CategoryCard(root, verdict, choices)
            card.set_answer(answers.get(verdict.rel, verdict.best))
            self.cards.append(card)
        self.scroll.set_cards(self.cards, keep_place)  # the cards shown before are deleted

    @property
    def preview(self) -> PhotoPreview | None:
        """The photo shown big, while it is open."""
        return self.scroll.preview

    def columns(self) -> int:
        return self.scroll.columns()

    def stop_thumbnails(self) -> None:
        self.scroll.stop_thumbnails()

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
