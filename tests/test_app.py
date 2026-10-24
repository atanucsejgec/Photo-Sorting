import logging
import sys
from logging.handlers import RotatingFileHandler

from photosort.app import configure_logging, default_log_path


def test_configure_logging_writes_to_the_given_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored afterwards
    path = tmp_path / "logs" / "photo-sorter.log"
    handler = configure_logging(path)
    try:
        assert isinstance(handler, RotatingFileHandler)
        assert (handler.maxBytes, handler.backupCount) == (1024 * 1024, 3)
        logging.getLogger("photosort").error("Task boom failed")
        try:
            raise ValueError("uncaught")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "ERROR photosort: Task boom failed" in text
        assert "ValueError: uncaught" in text
    finally:
        logging.getLogger("photosort").removeHandler(handler)
        handler.close()


def test_default_log_path_is_the_user_log_dir():
    assert default_log_path().name == "photo-sorter.log"
    assert "photo-sorter" in default_log_path().parent.parts
