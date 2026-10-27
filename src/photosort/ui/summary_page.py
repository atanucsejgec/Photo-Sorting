"""The Summary page: what a sort will move, before anything moves.

Go has the focus when the page opens (Back, when there is nothing to move),
so Enter moves the files: the deliberate Go. Esc goes back (go_back), as
Back does; ← and → switch between the buttons, and Tab also reaches "Show
all moves" (Enter shows or hides the list of every move) and the list.
A held Enter, or Enter with a modifier (Ctrl+Enter held since a review),
presses nothing (widgets.ButtonKeys).

Space presses no button here: it skips a face question, and on the last
one the summary opens with Go focused, so a second Space must never move
the files. Go takes no key for GO_ARM_SECONDS after the summary shows, so
a fast second Enter (a quick scan, or Face setup with nothing to ask)
never moves them either; a click on Go is not held back.
"""

import time
from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from photosort.plan import Move
from photosort.ui import icons, shortcuts
from photosort.ui.theme import heading, page_spacing, styled
from photosort.ui.widgets import ButtonBar, ButtonKeys

READY = "Ready to sort"
SAFE = "Nothing is deleted · Undo is always there"
ICON_PX = 28
GO_ARM_SECONDS = 0.3  # Go takes no key this soon after the summary shows
_now = time.monotonic  # the clock Go is armed by (a test moves it on by hand)


class SummaryPage(QWidget):
    """The summary lines (see show), Show all moves, Back and Go."""

    go = Signal()
    go_back = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._moves: Sequence[Move] = ()
        self._shown_at = float("-inf")  # when show_summary last ran, by _now
        layout = QVBoxLayout(self)
        page_spacing(layout)
        self.heading_label = heading(READY)
        layout.addWidget(self.heading_label)

        panel = styled(QWidget(), "section")
        row = QHBoxLayout(panel)
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(14)
        self.icon_label = QLabel()
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(8)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.safe_label = QLabel(SAFE)
        self.safe_label.setObjectName("muted")
        self.safe_label.setWordWrap(True)
        words.addWidget(self.summary_label)
        words.addWidget(self.safe_label)
        row.addLayout(words, 1)
        layout.addWidget(panel)

        self.show_moves_button = QPushButton("Show all moves")
        self.show_moves_button.setCheckable(True)
        self.show_moves_button.toggled.connect(self._toggle_moves)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.go_back)
        self.go_button = QPushButton("Go")
        self.go_button.setObjectName("primaryButton")  # see theme
        self.go_button.clicked.connect(self.go)
        for button in (self.back_button, self.go_button):
            button.setMinimumWidth(120)
        layout.addWidget(ButtonBar([self.show_moves_button], [self.back_button, self.go_button]))

        self.moves_table = QTableWidget(0, 2)
        self.moves_table.setHorizontalHeaderLabels(["From", "To"])
        self.moves_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.moves_table.verticalHeader().hide()
        self.moves_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.moves_table.setTabKeyNavigation(False)  # Tab leaves the list
        self.moves_table.hide()
        layout.addWidget(self.moves_table, 1)
        layout.addStretch(0)  # takes the room while the list is hidden

        # Keys (see the module doc).
        ButtonKeys([self.show_moves_button, self.back_button, self.go_button], sideways=True,
                   space=False, armed=lambda b: b is not self.go_button or self.go_armed())
        for button, word in ((self.go_button, "go"), (self.back_button, "back"),
                             (self.show_moves_button, "show/hide moves")):
            shortcuts.describe(button, "Enter", word)
            shortcuts.describe(button, "←→", "switch buttons")
        shortcuts.describe(self.moves_table, "↑↓", "scroll")
        shortcuts.bind(self, "Escape", "Esc", "back", self.go_back.emit, repeat=False)
        shortcuts.describe_tab(self)

    def show_summary(self, lines: list[str], can_go: bool, moves: Sequence[Move] = (),
                     mode_label: str | None = None, mode_icon: str | None = None) -> None:
        """Show lines; Go only if can_go; moves for Show all moves."""
        self._moves = moves
        self._shown_at = _now()
        self.heading_label.setText(
            READY if mode_label is None else f"{READY} by {mode_label}")
        self.icon_label.setVisible(mode_icon is not None)
        if mode_icon is not None:
            self.icon_label.setPixmap(icons.icon(mode_icon, "focus").pixmap(ICON_PX, ICON_PX))
        self.summary_label.setText("\n".join(lines))
        self.safe_label.setVisible(can_go)
        self.go_button.setEnabled(can_go)
        self.show_moves_button.setEnabled(can_go)
        self.moves_table.setRowCount(0)
        self.show_moves_button.setChecked(False)
        self.moves_table.hide()

    def go_armed(self) -> bool:
        """Whether a key can press Go: it can be pressed, and the summary
        has been shown for GO_ARM_SECONDS."""
        return self.go_button.isEnabled() and _now() - self._shown_at >= GO_ARM_SECONDS

    def focus_first(self) -> None:
        """Go, so Enter moves the files; Back when there is nothing to move."""
        button = self.go_button if self.go_button.isEnabled() else self.back_button
        button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _toggle_moves(self, checked: bool) -> None:
        if checked and self.moves_table.rowCount() == 0:
            self.moves_table.setRowCount(len(self._moves))
            for row, move in enumerate(self._moves):
                self.moves_table.setItem(row, 0, QTableWidgetItem(move.src))
                self.moves_table.setItem(row, 1, QTableWidgetItem(move.dst))
        self.moves_table.setVisible(checked)
