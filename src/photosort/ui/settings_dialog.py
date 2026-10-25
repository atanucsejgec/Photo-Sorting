"""Settings: the app-wide preferences that don't belong on the Home page:
the GPU switch, the event gap and minimum, the Content categories and the
remembered people.

OK applies the changes to the Settings object and saves it; Cancel leaves
both untouched. That includes the Content categories, edited from "Content
categories…". "Remembered people…" lists the people of the given store,
where they can be forgotten straight away.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QVBoxLayout,
)

from photosort.ai.runtime import runtime_status
from photosort.people import PeopleStore
from photosort.settings import Settings, save_settings
from photosort.thresholds import EVENT_GAP_HOURS_RANGE, EVENT_MIN_FILES_RANGE
from photosort.ui import dialogs
from photosort.ui.category_editor import CategoryEditor
from photosort.ui.face_setup_page import RememberedPeopleDialog


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, settings_path: Path | None = None,
                 parent=None, people: PeopleStore | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._settings_path = settings_path
        self.people_store = people
        self._categories = list(settings.content_categories)  # as edited, until OK

        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        self.gpu_check = QCheckBox("Use GPU")
        self.gpu_check.setChecked(settings.use_gpu)
        layout.addWidget(self.gpu_check)
        self.runtime_label = QLabel()
        layout.addWidget(self.runtime_label)
        self.gpu_check.toggled.connect(self._show_runtime)
        self._show_runtime(self.gpu_check.isChecked())
        self.note_label = QLabel(
            "The GPU makes face recognition faster. Without one, the CPU is used."
        )
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)
        self.gap_label, self.gap_spin, self.gap_unit_label = self._spin_row(
            layout, "New event after a gap of", EVENT_GAP_HOURS_RANGE,
            settings.event_gap_hours, "hours")
        self.min_files_label, self.min_files_spin, self.min_files_unit_label = (
            self._spin_row(layout, "An event needs at least", EVENT_MIN_FILES_RANGE,
                           settings.event_min_files, "files"))
        self.people_button = QPushButton("Remembered people…")
        self.people_button.setEnabled(people is not None)
        self.people_button.clicked.connect(self._open_people)
        self.categories_button = QPushButton("Content categories…")
        self.categories_button.clicked.connect(self._edit_categories)
        row = QHBoxLayout()
        row.addWidget(self.people_button)
        row.addWidget(self.categories_button)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _spin_row(layout: QVBoxLayout, text: str, limits: tuple[int, int], value: int,
                  unit: str) -> tuple[QLabel, QSpinBox, QLabel]:
        """Add a row "<text> [spin box] <unit>" to layout."""
        label, spin, unit_label = QLabel(text), QSpinBox(), QLabel(unit)
        spin.setRange(*limits)
        spin.setValue(value)
        label.setBuddy(spin)
        row = QHBoxLayout()
        for widget in (label, spin, unit_label):
            row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)
        return label, spin, unit_label

    def _show_runtime(self, use_gpu: bool) -> None:
        self.runtime_label.setText(f"Running on: {runtime_status(use_gpu)}")

    def _open_people(self) -> None:
        if self.people_store is not None:
            RememberedPeopleDialog(self.people_store, self).exec()

    def _edit_categories(self) -> None:
        editor = CategoryEditor(self._categories, self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._categories = editor.categories()
        editor.deleteLater()

    def show_error(self, text: str) -> None:
        dialogs.warn(self, "Settings", text)

    def accept(self) -> None:
        self.settings.use_gpu = self.gpu_check.isChecked()
        self.settings.event_gap_hours = self.gap_spin.value()
        self.settings.event_min_files = self.min_files_spin.value()
        self.settings.content_categories = list(self._categories)
        try:
            save_settings(self.settings, self._settings_path)
        except OSError as e:
            # The change still applies until the app is closed.
            self.show_error(f"Could not save settings: {e}")
        super().accept()
