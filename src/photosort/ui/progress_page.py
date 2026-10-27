"""The Progress page: what a sort's slow work is doing, with Cancel.

Esc cancels too. Once cancelled the page says "Stopping…" until the work
notices and ends; Cancel and Esc then do nothing more. Enter does nothing
here: a second Enter after Go (or a tile) must not stop the sort.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from photosort.ui import shortcuts
from photosort.ui.theme import heading, styled
from photosort.ui.widgets import ButtonBar

HEADING = "Working…"
STOPPING = "Stopping…"


class ProgressPage(QWidget):
    """"<prefix> <i> <joiner> <total>" over a bar; cancel_requested on Cancel or Esc."""

    cancel_requested = Signal()
    keys_changed = Signal()  # Esc was switched on or off (the strip redraws)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prefix = ""
        self._joiner = "/"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 12)
        layout.setSpacing(16)
        layout.addWidget(heading(HEADING))
        layout.addStretch()
        panel = styled(QWidget(), "section")
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(18, 16, 18, 18)
        inner.setSpacing(12)
        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        self.progress_label.setTextFormat(Qt.TextFormat.PlainText)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        inner.addWidget(self.progress_label)
        inner.addWidget(self.progress_bar)
        layout.addWidget(panel)
        layout.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumWidth(120)
        self.cancel_button.setAutoDefault(False)  # Enter never cancels (see the module doc)
        self.cancel_button.clicked.connect(self.cancel_requested)
        layout.addWidget(ButtonBar([], [self.cancel_button]))

        self._cancel_key = shortcuts.bind(self, "Escape", "Esc", "cancel",
                                          self.cancel_button.click, repeat=False)

    def begin(self, prefix: str, total: int | None, joiner: str) -> None:
        """Start again: "<prefix>", or "<prefix> 0 <joiner> <total>", the bar busy
        until the first tick; Cancel ready."""
        self._prefix, self._joiner = prefix, joiner
        self.progress_bar.setRange(0, 0)
        if total is None:
            self.progress_label.setText(prefix)
        else:
            self.set_progress(0, total)
        self._set_cancellable(True)

    def set_progress(self, i: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(i)
        self.progress_label.setText(f"{self._prefix} {i} {self._joiner} {total}")

    def stopping(self) -> None:
        """Cancel was pressed: say so, and take Cancel and Esc away."""
        self._set_cancellable(False)
        self.progress_label.setText(STOPPING)

    def focus_first(self) -> None:
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_cancellable(self, on: bool) -> None:
        self.cancel_button.setEnabled(on)
        if self._cancel_key.isEnabled() != on:
            self._cancel_key.setEnabled(on)
            self.keys_changed.emit()
