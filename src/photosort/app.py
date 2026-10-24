"""Start the Photo Sorter desktop app."""

import sys

from PySide6.QtWidgets import QApplication

from photosort.settings import default_path, load_settings
from photosort.ui.main_window import TITLE, MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    QApplication.setApplicationName(TITLE)
    # Matches scripts/install-desktop-entry.sh, so the app menu entry's icon
    # and name are used for the window (the Wayland app id).
    QApplication.setDesktopFileName("photo-sorter")
    font = app.font()
    if font.pointSizeF() < 11:
        font.setPointSizeF(11)
        app.setFont(font)
    path = default_path()
    window = MainWindow(load_settings(path), path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
