"""The Done page: what the sort did, with Undo, Open folder and Home.

Home has the focus when the page opens, never Undo, so a second Enter (or
a held one) can't undo the sort: Enter presses the focused button, once
(widgets.ButtonKeys), and ← → switch between them. U undoes this run, after
asking, with "Cancel" focused; the Undo button (one Tab away) undoes at
once, as a click always has. Esc goes Home too.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from photosort.ui import dialogs, shortcuts
from photosort.ui.theme import heading, styled
from photosort.ui.widgets import ButtonBar, ButtonKeys

HEADING = "Done"
UNDO_TITLE = "Undo this sort?"
UNDO_QUESTION = "Put back the files this sort moved?"
UNDOING = "Putting files back…"


class DonePage(QWidget):
    """The sort's result (show_result) and what an undo did (undo_* methods)."""

    undo_requested = Signal()
    open_folder_requested = Signal()
    home_requested = Signal()
    keys_changed = Signal()  # U was switched on or off (the strip redraws)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 12)
        layout.setSpacing(16)
        layout.addWidget(heading(HEADING))
        panel = styled(QWidget(), "section")
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(18, 14, 18, 14)
        inner.setSpacing(8)
        self.done_label = QLabel()
        self.undo_label = QLabel()
        for label in (self.done_label, self.undo_label):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            inner.addWidget(label)
        layout.addWidget(panel)

        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(self.undo_requested)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self.open_folder_requested)
        self.home_button = QPushButton("Home")
        self.home_button.setObjectName("primaryButton")  # see theme
        self.home_button.clicked.connect(self.home_requested)
        for button in (self.undo_button, self.open_folder_button, self.home_button):
            button.setMinimumWidth(120)
        layout.addWidget(ButtonBar([self.undo_button, self.open_folder_button],
                                   [self.home_button]))
        layout.addStretch()

        ButtonKeys([self.undo_button, self.open_folder_button, self.home_button],
                   sideways=True)
        for button, word in ((self.home_button, "home"), (self.undo_button, "undo"),
                             (self.open_folder_button, "open folder")):
            shortcuts.describe(button, "Enter", word)
            shortcuts.describe(button, "←→", "switch buttons")
        self._undo_key = shortcuts.bind(self, "U", "U", "undo", self._undo_asked, repeat=False)
        self._home_key = shortcuts.bind(self, "Escape", "Esc", "home", self.home_button.click,
                                        repeat=False)
        shortcuts.describe_tab(self)

    def show_result(self, lines: list[str], can_undo: bool) -> None:
        self.done_label.setText("\n".join(lines))
        self.undo_label.setText("")
        self._set_undoable(can_undo)
        self._set_home(True)

    def undo_started(self) -> None:
        self._set_undoable(False)
        self._set_home(False)
        self.undo_label.setText(UNDOING)

    def undo_ended(self, text: str, can_undo_again: bool) -> None:
        """The undo is over (text says how it went); Undo stays for a retry
        if can_undo_again. The focus goes back to Home."""
        self.undo_label.setText(text)
        self._set_undoable(can_undo_again)
        self._set_home(True)
        if self.isVisible():
            self.home_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def focus_first(self) -> None:
        self.home_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _undo_asked(self) -> None:
        """U: undo, once the owner says yes."""
        if self.undo_button.isEnabled() and dialogs.ask(
                self, UNDO_TITLE, UNDO_QUESTION, yes="Undo", no="Cancel", default="no"):
            self.undo_button.click()

    def _set_undoable(self, on: bool) -> None:
        self.undo_button.setEnabled(on)
        self._switch(self._undo_key, on)

    def _set_home(self, on: bool) -> None:
        """Home, and Esc with it (both wait while an undo runs)."""
        self.home_button.setEnabled(on)
        self._switch(self._home_key, on)

    def _switch(self, key, on: bool) -> None:
        if key.isEnabled() != on:
            key.setEnabled(on)
            self.keys_changed.emit()
