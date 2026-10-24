import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDropEvent

from photosort.settings import Settings, load_settings
from photosort.ui import main_window
from photosort.ui.main_window import MainWindow

EXIF = "2025:02:20 10:00:00"


@pytest.fixture
def settings_path(tmp_path):
    return tmp_path / "config" / "settings.json"


@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def make_window(qtbot, settings_path, errors):
    windows = []

    def _make(settings: Settings | None = None) -> MainWindow:
        w = MainWindow(settings or Settings(), settings_path)
        qtbot.addWidget(w)
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def _mode_buttons(w):
    return list(w.mode_buttons.values())


def test_start_disabled_without_folder(make_window, photos):
    w = make_window()
    assert w.folder_edit.text() == ""
    assert not w.start_button.isEnabled()
    w.folder_edit.setText(str(photos / "missing"))
    assert not w.start_button.isEnabled()
    w.folder_edit.setText(str(photos))
    assert w.start_button.isEnabled()


def test_only_date_mode_available(make_window):
    w = make_window()
    buttons = _mode_buttons(w)
    enabled = [b for b in buttons if b.isEnabled()]
    disabled = [b for b in buttons if not b.isEnabled()]
    assert [b.text() for b in enabled] == ["Date"]
    assert enabled[0].isChecked()
    assert len(disabled) == 10
    assert all("coming soon" in b.text() for b in disabled)


def test_subfolders_default_off_and_saved(make_window, settings_path):
    w = make_window()
    assert not w.subfolders_check.isChecked()
    w.subfolders_check.setChecked(True)
    assert load_settings(settings_path).include_subfolders is True


def test_full_date_flow_and_undo(qtbot, make_window, photos, make_jpeg, errors,
                                 settings_path):
    names = ["a.jpg", "b.jpg", "c.jpg"]
    for name in names:
        make_jpeg(photos / name, EXIF)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert "3 files → 1 month folders, 1 day folders, 0 No Date" in w.summary_label.text()
    assert w.go_button.isEnabled()
    assert load_settings(settings_path).last_folder == str(photos)

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 3 files." in w.done_label.text()
    day = photos / "2025" / "02-February" / "20-02-2025"
    assert sorted(p.name for p in day.iterdir()) == names
    assert not any((photos / n).exists() for n in names)

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert "Undo complete: 3 files put back." in w.undo_label.text()
    assert not w.undo_button.isEnabled()
    assert all((photos / n).is_file() for n in names)
    assert not (photos / "2025").exists()
    assert errors == []


def test_nothing_to_move(qtbot, make_window, photos, errors):
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert "Nothing to move." in w.summary_label.text()
    assert not w.go_button.isEnabled()
    assert errors == []


def test_show_all_moves_table(qtbot, make_window, photos, make_jpeg):
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.moves_table.isHidden()
    w.show_moves_button.click()
    assert not w.moves_table.isHidden()
    assert w.moves_table.rowCount() == 1
    assert w.moves_table.item(0, 0).text() == "a.jpg"
    assert w.moves_table.item(0, 1).text() == "2025/02-February/a.jpg"


def test_unwritable_folder_is_refused(make_window, photos, errors, monkeypatch):
    monkeypatch.setattr(main_window.os, "access", lambda path, mode: False)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    assert errors == ["Can't write to this folder."]
    assert w.current_page() is w.home


def test_cancel_while_checking_returns_home(qtbot, make_window, photos, monkeypatch):
    started = threading.Event()

    def slow_prepare(root, include_subfolders, progress=None, cancel=None):
        started.set()
        cancel.wait(10)
        return None

    monkeypatch.setattr(main_window.date_mode, "prepare_date_plan", slow_prepare)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    assert w.current_page() is w.progress_page
    assert w.progress_label.text() == "Checking files…"
    assert started.wait(5)
    w.cancel_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.home)


def test_last_folder_restored_and_drop(make_window, photos, tmp_path):
    w = make_window(Settings(last_folder=str(photos)))
    assert w.folder_edit.text() == str(photos)

    gone = make_window(Settings(last_folder=str(tmp_path / "gone")))
    assert gone.folder_edit.text() == ""

    other = tmp_path / "other"
    other.mkdir()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(other))])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    w.dropEvent(event)
    assert Path(w.folder_edit.text()) == other


def _sort_to_done(qtbot, w, photos):
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)


def test_undo_blocked_shows_newer_runs(qtbot, make_window, photos, make_jpeg,
                                       errors, monkeypatch):
    from photosort.undo import UndoBlocked

    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    _sort_to_done(qtbot, w, photos)

    def blocked(root, run_id, progress=None):
        raise UndoBlocked(["20261024-120000-date", "20261024-130000-date"])

    monkeypatch.setattr(main_window, "undo_run", blocked)
    w.undo_button.click()
    qtbot.waitUntil(lambda: errors != [])
    assert errors == [
        "Undo the newer run(s) first: 20261024-120000-date, 20261024-130000-date"
    ]
    assert w.undo_button.isEnabled()


def test_cancelled_move_says_so(qtbot, make_window, photos, make_jpeg, monkeypatch):
    real_execute = main_window.execute

    def cancelled_at_once(root, plan, journal, progress=None, cancel=None):
        cancel.set()
        return real_execute(root, plan, journal, progress=progress, cancel=cancel)

    monkeypatch.setattr(main_window, "execute", cancelled_at_once)
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    _sort_to_done(qtbot, w, photos)
    assert w.done_label.text().splitlines() == [
        "Moved 0 files.",
        "Cancelled — the files moved so far can be undone.",
    ]
    assert (photos / "a.jpg").is_file()
