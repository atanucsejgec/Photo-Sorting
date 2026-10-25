import gc
import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication

from photosort import app as app_module
from photosort.app import configure_logging, default_log_path
from photosort.ui import gc_guard


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


def test_main_collects_garbage_on_the_gui_thread_only(qapp, tmp_path, monkeypatch):
    """app.main installs the GC guard (conftest installs it for the tests too,
    so without this a lost line in main would go unnoticed)."""
    shown = []

    class Window:
        def __init__(self, settings, path):
            assert path == tmp_path / "settings.json"

        def show(self):
            shown.append(gc_guard._guard)

    monkeypatch.setattr(app_module, "configure_logging", lambda path: None)
    monkeypatch.setattr(app_module, "default_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(app_module, "MainWindow", Window)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    name, desktop, font = qapp.applicationName(), qapp.desktopFileName(), qapp.font()
    gc_guard.uninstall()  # the session's guard; put back below
    assert gc.isenabled()
    try:
        assert app_module.main() == 0
        assert gc_guard._guard is not None
        assert shown == [gc_guard._guard]  # installed before the window shows
        assert not gc.isenabled()
    finally:
        gc_guard.uninstall()
        gc_guard.install(qapp)
        qapp.setApplicationName(name)
        qapp.setDesktopFileName(desktop)
        qapp.setFont(font)


def test_main_applies_the_theme_before_the_window_shows(qapp, tmp_path, monkeypatch):
    from photosort.ui import theme

    events = []

    class Window:
        def __init__(self, settings, path):
            pass

        def show(self):
            events.append("show")

    monkeypatch.setattr(app_module, "configure_logging", lambda path: None)
    monkeypatch.setattr(app_module, "default_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(app_module, "MainWindow", Window)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(theme, "apply", lambda app: events.append(("theme", app)))
    name, desktop, font = qapp.applicationName(), qapp.desktopFileName(), qapp.font()
    try:
        assert app_module.main() == 0
        assert events == [("theme", qapp), "show"]
    finally:
        qapp.setApplicationName(name)
        qapp.setDesktopFileName(desktop)
        qapp.setFont(font)
