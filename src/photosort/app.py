"""Start the Photo Sorter desktop app."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import platformdirs
from PySide6.QtWidgets import QApplication

from photosort.settings import default_path, load_settings
from photosort.ui.main_window import TITLE, MainWindow

LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 3


def default_log_path() -> Path:
    return Path(platformdirs.user_log_dir("photo-sorter")) / "photo-sorter.log"


def configure_logging(path: Path) -> logging.Handler:
    """Send the "photosort" logger, and uncaught exceptions, to a rotating file.

    Launched from the app menu there is no terminal, so stderr is lost.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger("photosort")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def log_uncaught(exc_type, exc, tb) -> None:
        logger.error("Uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = log_uncaught
    return handler


def main() -> int:
    try:
        configure_logging(default_log_path())
    except OSError as e:
        print(f"Photo Sorter: could not open the log file: {e}", file=sys.stderr)
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
