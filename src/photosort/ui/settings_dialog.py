"""Settings: the app-wide preferences that don't belong on the Home page.

OK applies the changes to the Settings object and saves it; Cancel leaves
both untouched. That includes the Content categories, edited from "Content
categories…". "Remembered people…" lists the people of the given store,
where they can be forgotten straight away.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout,
)

from photosort.ai.runtime import runtime_status
from photosort.people import PeopleStore
from photosort.settings import Settings, save_settings
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
        QMessageBox.warning(self, "Settings", text)

    def accept(self) -> None:
        self.settings.use_gpu = self.gpu_check.isChecked()
        self.settings.content_categories = list(self._categories)
        try:
            save_settings(self.settings, self._settings_path)
        except OSError as e:
            # The change still applies until the app is closed.
            self.show_error(f"Could not save settings: {e}")
        super().accept()
