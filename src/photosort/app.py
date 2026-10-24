"""Start the Photo Sorter desktop app."""

import sys

from PySide6.QtWidgets import QApplication

from photosort.settings import default_path, load_settings
from photosort.ui.main_window import TITLE, MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    QApplication.setApplicationName(TITLE)
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
