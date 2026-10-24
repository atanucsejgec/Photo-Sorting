"""Settings: the app-wide preferences that don't belong on the Home page.

OK applies the changes to the Settings object and saves it; Cancel leaves
both untouched.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout,
)

from photosort.settings import Settings, save_settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, settings_path: Path | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._settings_path = settings_path

        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        self.gpu_check = QCheckBox("Use GPU")
        self.gpu_check.setChecked(settings.use_gpu)
        layout.addWidget(self.gpu_check)
        self.note_label = QLabel(
            "GPU is used by the face and content modes (coming in later stages)."
        )
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)
        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def show_error(self, text: str) -> None:
        QMessageBox.warning(self, "Settings", text)

    def accept(self) -> None:
        self.settings.use_gpu = self.gpu_check.isChecked()
        try:
            save_settings(self.settings, self._settings_path)
        except OSError as e:
            # The change still applies until the app is closed.
            self.show_error(f"Could not save settings: {e}")
        super().accept()
