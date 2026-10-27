"""The Settings page: the app-wide preferences that don't belong on Home:
the GPU switch, the event gap and minimum, and the Content categories.

Each change is saved as it is made (a number typed, once it is done); there is no OK. "Content categories…"
opens the category editor, and its OK saves the list.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from photosort.ai.runtime import runtime_status
from photosort.settings import Settings, save_settings
from photosort.thresholds import EVENT_GAP_HOURS_RANGE, EVENT_MIN_FILES_RANGE
from photosort.ui import dialogs, shortcuts
from photosort.ui.category_editor import CategoryEditor
from photosort.ui.theme import heading, styled
from photosort.ui.widgets import Switch

SPIN_TEXT_MARGIN_PX = 10  # Qt ignores a spin box's horizontal padding in the stylesheet


def _section(layout: QVBoxLayout, title: str) -> QVBoxLayout:
    """A panel titled title, added to layout; its own layout, for its rows."""
    panel = styled(QWidget(), "section")
    inner = QVBoxLayout(panel)
    inner.setContentsMargins(18, 14, 18, 16)
    inner.setSpacing(10)
    label = QLabel(title)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    inner.addWidget(label)
    layout.addWidget(panel)
    return inner


class SettingsPage(QWidget):
    def __init__(self, settings: Settings, settings_path: Path | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._settings_path = settings_path

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 12)
        layout.setSpacing(14)
        layout.addWidget(heading("Settings"))

        faces = _section(layout, "Face and photo recognition")
        self.gpu_check = Switch("Use GPU")
        self.gpu_check.setChecked(settings.use_gpu)
        faces.addWidget(self.gpu_check, 0, Qt.AlignmentFlag.AlignLeft)
        self.runtime_label = QLabel()
        faces.addWidget(self.runtime_label)
        self.note_label = QLabel(
            "The GPU makes face recognition faster. Without one, the CPU is used."
        )
        self.note_label.setObjectName("muted")
        self.note_label.setWordWrap(True)
        faces.addWidget(self.note_label)

        events = _section(layout, "Events")
        self.gap_label, self.gap_spin, self.gap_unit_label = self._spin_row(
            events, "New event after a gap of", EVENT_GAP_HOURS_RANGE,
            settings.event_gap_hours, "hours")
        self.min_files_label, self.min_files_spin, self.min_files_unit_label = (
            self._spin_row(events, "An event needs at least", EVENT_MIN_FILES_RANGE,
                           settings.event_min_files, "files"))

        content = _section(layout, "Content")
        hint = QLabel("The folders Content mode sorts photos into.")
        hint.setObjectName("muted")
        content.addWidget(hint)
        self.categories_button = QPushButton("Content categories…")
        self.categories_button.clicked.connect(self._edit_categories)
        content.addWidget(self.categories_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()

        self.gpu_check.toggled.connect(self._gpu_changed)
        self.gap_spin.valueChanged.connect(self._gap_changed)
        self.min_files_spin.valueChanged.connect(self._min_files_changed)

        shortcuts.describe(self.gpu_check, "Space", "on/off")
        for spin in (self.gap_spin, self.min_files_spin):
            shortcuts.describe(spin, "↑↓", "change")
        shortcuts.bind(self.categories_button, "Return", "Enter", "open",
                       self.categories_button.click)
        shortcuts.describe(self, "Tab", "next")

    @staticmethod
    def _spin_row(layout: QVBoxLayout, text: str, limits: tuple[int, int], value: int,
                  unit: str) -> tuple[QLabel, QSpinBox, QLabel]:
        """Add a row "<text> [spin box] <unit>" to layout."""
        label, spin, unit_label = QLabel(text), QSpinBox(), QLabel(unit)
        spin.setRange(*limits)
        spin.setValue(value)
        spin.setKeyboardTracking(False)  # typed digits save once, when the edit is done
        spin.setMinimumWidth(90)
        spin.lineEdit().setTextMargins(SPIN_TEXT_MARGIN_PX, 0, 0, 0)
        label.setBuddy(spin)
        row = QHBoxLayout()
        row.setSpacing(10)
        for widget in (label, spin, unit_label):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)
        return label, spin, unit_label

    def showEvent(self, event) -> None:
        # Asked when shown, not built: it loads the AI runtime.
        self._show_runtime(self.gpu_check.isChecked())
        super().showEvent(event)

    def _show_runtime(self, use_gpu: bool) -> None:
        self.runtime_label.setText(f"Running on: {runtime_status(use_gpu)}")

    def show_error(self, text: str) -> None:
        dialogs.warn(self, "Settings", text)

    def _save(self) -> None:
        try:
            save_settings(self.settings, self._settings_path)
        except OSError as e:
            # The change still applies until the app is closed.
            self.show_error(f"Could not save settings: {e}")

    def _gpu_changed(self, checked: bool) -> None:
        self.settings.use_gpu = checked
        self._show_runtime(checked)
        self._save()

    def _gap_changed(self, value: int) -> None:
        self.settings.event_gap_hours = value
        self._save()

    def _min_files_changed(self, value: int) -> None:
        self.settings.event_min_files = value
        self._save()

    def _edit_categories(self) -> None:
        editor = CategoryEditor(list(self.settings.content_categories), self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self.settings.content_categories = editor.categories()
            self._save()
        editor.deleteLater()
