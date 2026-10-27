import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDropEvent

from photosort.faces import Face
from photosort.modes import MODE_SPECS, MODES, date_mode
from photosort.people import PeopleStore
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
def store(tmp_path) -> PeopleStore:
    return PeopleStore(tmp_path / "people")  # never the owner's own


@pytest.fixture
def make_window(qtbot, settings_path, errors, store):
    windows = []

    def _make(settings: Settings | None = None) -> MainWindow:
        w = MainWindow(settings or Settings(), settings_path, people_store=store)
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


def test_available_modes(make_window):
    w = make_window()
    buttons = _mode_buttons(w)
    enabled = [b for b in buttons if b.isEnabled()]
    disabled = [b for b in buttons if not b.isEnabled()]
    assert [b.text() for b in enabled] == [
        "Date", "Face", "Duplicates", "Repeated shots", "Notes & Pages", "Type / source",
        "Content", "Bad photos", "Events", "Location", "Camera", "No people", "Extension",
        "Date (all files)"]
    assert enabled[0].isChecked()
    assert len(disabled) == 0
    assert not any("coming soon" in b.text() for b in buttons)
    assert all(key in MODE_SPECS for key, _, available in MODES if available)


def test_folder_is_resolved(make_window, photos, tmp_path):
    link = tmp_path / "link"
    try:
        link.symlink_to(photos, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not allowed here")
    w = make_window()
    w.folder_edit.setText(str(link / ".." / "link"))
    assert w._folder() == photos.resolve()


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


def test_done_open_folder_button(qtbot, make_window, photos, make_jpeg, errors,
                                 monkeypatch):
    opened = []
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toLocalFile()) or True)
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)

    assert w.open_folder_button.text() == "Open folder"
    w.open_folder_button.click()
    assert [Path(p) for p in opened] == [photos.resolve()]
    assert errors == []

    monkeypatch.setattr(main_window.QDesktopServices, "openUrl", lambda url: False)
    w.open_folder_button.click()
    assert errors == ["Couldn't open the folder."]


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

    monkeypatch.setattr(date_mode, "prepare_date_plan", slow_prepare)
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
    from photosort.undo import BlockingRun, UndoBlocked

    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    _sort_to_done(qtbot, w, photos)

    def blocked(root, run_id, progress=None):
        raise UndoBlocked([BlockingRun("20261024-130000-date", "2025/02-February"),
                           BlockingRun("20261024-120000-date", "")])

    monkeypatch.setattr(main_window, "undo_run", blocked)
    w.undo_button.click()
    qtbot.waitUntil(lambda: errors != [])
    assert errors == [
        "Undo the newer run(s) first: 20261024-130000-date (in 2025/02-February), "
        "20261024-120000-date"
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


def test_one_task_at_a_time(qtbot, make_window, photos, monkeypatch):
    calls = []

    def slow_prepare(root, include_subfolders, progress=None, cancel=None):
        calls.append(root)
        cancel.wait(10)
        return None

    monkeypatch.setattr(date_mode, "prepare_date_plan", slow_prepare)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    w.start_button.click()
    qtbot.waitUntil(lambda: calls != [])
    qtbot.wait(100)
    assert len(calls) == 1

    w.cancel_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    w.start_button.click()  # the finished task no longer blocks a new one
    qtbot.waitUntil(lambda: len(calls) == 2)
    w.cancel_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.home)


def test_stale_plan_is_never_executed(qtbot, make_window, photos, make_jpeg, errors,
                                      monkeypatch):
    from photosort.journal import load_runs

    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.back_button.click()
    assert w.current_page() is w.home  # date mode has no review to go back to

    def broken(root, include_subfolders, progress=None, cancel=None):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(date_mode, "prepare_date_plan", broken)
    w.start_button.click()
    qtbot.waitUntil(lambda: errors != [])
    assert errors == ["Could not check the files: unreadable"]
    w.go_button.click()  # the old plan must not run
    assert w.current_page() is w.home
    assert (photos / "a.jpg").is_file()
    assert load_runs(photos) == []


def test_partial_undo_keeps_undo_button(qtbot, make_window, photos, make_jpeg, errors,
                                        monkeypatch):
    import errno
    import os

    from photosort import undo

    names = ["a.jpg", "b.jpg"]
    for name in names:
        make_jpeg(photos / name, EXIF)
    w = make_window()
    _sort_to_done(qtbot, w, photos)

    real = undo.move_file

    def flaky(src, dst):
        if os.path.basename(src) == "b.jpg":
            raise PermissionError(errno.EACCES, "File in use", str(src))
        return real(src, dst)

    monkeypatch.setattr(undo, "move_file", flaky)
    w.undo_button.click()
    qtbot.waitUntil(lambda: "couldn't be put back" in w.undo_label.text())
    monkeypatch.undo()
    assert w.undo_label.text().splitlines()[0] == "Undo not finished: 1 files put back."
    assert w.undo_button.isEnabled()

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert w.undo_label.text() == "Undo complete: 1 files put back."
    assert not w.undo_button.isEnabled()
    assert all((photos / n).is_file() for n in names)
    assert errors == []


def _start_mode(qtbot, w, photos, mode):
    w.folder_edit.setText(str(photos))
    w.mode_buttons[mode].setChecked(True)
    w.start_button.click()


def test_duplicates_end_to_end(qtbot, make_window, photos, make_pattern_jpeg, errors):
    import shutil

    from photosort.journal import load_runs

    make_pattern_jpeg(photos / "x.jpg", 1)
    shutil.copyfile(photos / "x.jpg", photos / "x copy.jpg")
    make_pattern_jpeg(photos / "IMG20250220181325.jpg", 3, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(photos / "IMG-20250220-WA0012.jpg", 3, size=(400, 300), quality=60)
    before = sorted(p.name for p in photos.iterdir())
    w = make_window()
    _start_mode(qtbot, w, photos, "duplicates")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    page = w.review_page
    unkept = []
    for _ in range(2):
        unkept += [c.candidate.rel for c in page.cards if not c.keep_check.isChecked()]
        page.confirm_button.click()
    assert sorted(unkept) == ["IMG-20250220-WA0012.jpg", "x.jpg"]
    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines() == [
        "2 files → Duplicates/ (from 2 groups)",
        "Nothing is deleted. You can look through Duplicates/ later.",
    ]
    assert w.go_button.isEnabled()

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 2 files." in w.done_label.text()
    assert sorted(p.name for p in (photos / "Duplicates").iterdir()) == sorted(unkept)
    assert [run.mode for run in load_runs(photos)] == ["duplicates"]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert not (photos / "Duplicates").exists()
    assert sorted(p.name for p in photos.iterdir() if p.name != ".photosort") == before
    assert errors == []


def _two_duplicate_groups(photos, make_pattern_jpeg):
    import shutil

    make_pattern_jpeg(photos / "x.jpg", 1)
    shutil.copyfile(photos / "x.jpg", photos / "x copy.jpg")
    make_pattern_jpeg(photos / "IMG20250220181325.jpg", 3, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(photos / "IMG-20250220-WA0012.jpg", 3, size=(400, 300), quality=60)


def _ticked(page):
    return {c.candidate.rel for c in page.cards if c.keep_check.isChecked()}


def test_back_from_summary_returns_to_the_review(qtbot, make_window, photos,
                                                 make_pattern_jpeg, errors):
    _two_duplicate_groups(photos, make_pattern_jpeg)
    w = make_window()
    _start_mode(qtbot, w, photos, "duplicates")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    page = w.review_page
    first = _ticked(page)
    page.confirm_button.click()
    for card in page.cards:  # group 2: keep both
        card.keep_check.setChecked(True)
    kept = _ticked(page)
    page.confirm_button.click()
    assert w.current_page() is w.summary_page
    summary = w.summary_label.text()
    assert summary.splitlines()[0] == "1 file → Duplicates/ (from 1 group)"

    w.back_button.click()
    assert w.current_page() is page
    assert page.heading_label.text().startswith("Group 2 of 2")
    assert _ticked(page) == kept  # the answer given, still editable
    qtbot.waitUntil(lambda: all(not c.thumb_label.pixmap().isNull() for c in page.cards))

    page.stop_button.click()  # a fresh plan from the same answers
    assert w.current_page() is w.summary_page
    assert w.summary_label.text() == summary

    w.back_button.click()
    page.back_button.click()  # group 1 kept its answer too
    assert page.heading_label.text().startswith("Group 1 of 2")
    assert _ticked(page) == first
    assert errors == []


def test_back_from_no_groups_found_goes_home(qtbot, make_window, photos, make_pattern_jpeg):
    make_pattern_jpeg(photos / "a.jpg", 1)
    w = make_window()
    _start_mode(qtbot, w, photos, "duplicates")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.back_button.click()
    assert w.current_page() is w.home


def test_no_duplicates_found(qtbot, make_window, photos, make_pattern_jpeg, errors):
    make_pattern_jpeg(photos / "a.jpg", 1)
    make_pattern_jpeg(photos / "b.jpg", 2)
    w = make_window()
    _start_mode(qtbot, w, photos, "duplicates")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text() == "No duplicates found."
    assert not w.go_button.isEnabled()
    assert not w.show_moves_button.isEnabled()
    assert errors == []


def test_no_repeated_shots_found(qtbot, make_window, photos, make_pattern_jpeg, errors):
    make_pattern_jpeg(photos / "a.jpg", 1, exif_dt="2025:02:20 18:13:25")
    w = make_window()
    _start_mode(qtbot, w, photos, "repeated")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text() == "No repeated shots found."
    assert not w.go_button.isEnabled()


def _burst(photos, make_pattern_jpeg):
    make_pattern_jpeg(photos / "a.jpg", 5, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(photos / "b.jpg", 5, shift=2, blur=2, exif_dt="2025:02:20 18:13:26")


def test_repeated_keep_one_moves_the_rest(qtbot, make_window, photos, make_pattern_jpeg,
                                          errors):
    _burst(photos, make_pattern_jpeg)
    w = make_window()
    _start_mode(qtbot, w, photos, "repeated")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    w.review_page.keep_one_button.click()
    assert w.summary_label.text().splitlines()[0] == "1 file → Repeated Shots/ (from 1 group)"
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert (photos / "Repeated Shots" / "b.jpg").is_file()
    assert (photos / "a.jpg").is_file()
    assert errors == []


def test_repeated_end_to_end(qtbot, make_window, photos, make_pattern_jpeg, errors):
    from photosort.journal import load_runs

    make_pattern_jpeg(photos / "a.jpg", 5, shift=2, blur=2, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(photos / "b.jpg", 5, exif_dt="2025:02:20 18:13:26")  # the sharpest
    make_pattern_jpeg(photos / "c.jpg", 5, shift=4, blur=3, exif_dt="2025:02:20 18:13:27")
    w = make_window()
    _start_mode(qtbot, w, photos, "repeated")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    page = w.review_page
    assert page.heading_label.text() == "Group 1 of 1 · Repeated shots"
    assert [c.candidate.rel for c in page.cards] == ["a.jpg", "b.jpg", "c.jpg"]

    page.keep_one_button.click()
    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines() == [
        "2 files → Repeated Shots/ (from 1 group)",
        "Nothing is deleted. You can look through Repeated Shots/ later.",
    ]

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 2 files." in w.done_label.text()
    assert sorted(p.name for p in (photos / "Repeated Shots").iterdir()) == ["a.jpg", "c.jpg"]
    assert (photos / "b.jpg").is_file()
    assert [run.mode for run in load_runs(photos)] == ["repeated"]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert not (photos / "Repeated Shots").exists()
    assert sorted(p.name for p in photos.iterdir() if p.name != ".photosort") == [
        "a.jpg", "b.jpg", "c.jpg",
    ]
    assert errors == []


def test_stop_reviewing_with_nothing_confirmed(qtbot, make_window, photos,
                                               make_pattern_jpeg, errors):
    _burst(photos, make_pattern_jpeg)
    w = make_window()
    _start_mode(qtbot, w, photos, "repeated")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    w.review_page.stop_button.click()
    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines()[0] == "Nothing to move."
    assert not w.go_button.isEnabled()
    assert errors == []


def test_cancel_review_returns_home(qtbot, make_window, photos, make_pattern_jpeg):
    _burst(photos, make_pattern_jpeg)
    w = make_window()
    _start_mode(qtbot, w, photos, "repeated")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    w.review_page.cancel_button.click()
    assert w.current_page() is w.home
    # the analysis cache is in .photosort; no photo moved
    assert sorted(p.name for p in photos.iterdir() if p.name != ".photosort") == ["a.jpg", "b.jpg"]


def test_cancelled_finder_returns_home(qtbot, make_window, photos, monkeypatch):
    from photosort.modes import duplicates

    monkeypatch.setattr(duplicates, "find_duplicate_groups",
                        lambda root, include, progress=None, cancel=None: None)
    w = make_window()
    _start_mode(qtbot, w, photos, "duplicates")
    assert w.current_page() is w.progress_page
    qtbot.waitUntil(lambda: w.current_page() is w.home)


def test_planning_error_goes_home(qtbot, make_window, photos, make_pattern_jpeg, errors,
                                  monkeypatch):
    def unplugged(*args, **kwargs):
        raise OSError("drive gone")

    monkeypatch.setattr(main_window, "plan_set_aside", unplugged)
    _burst(photos, make_pattern_jpeg)
    w = make_window()
    _start_mode(qtbot, w, photos, "repeated")
    qtbot.waitUntil(lambda: w.current_page() is w.review_page)
    w.review_page.keep_one_button.click()
    assert w.current_page() is w.home
    assert errors == ["Could not plan the moves: drive gone"]
    # the analysis cache is in .photosort; no photo moved
    assert sorted(p.name for p in photos.iterdir() if p.name != ".photosort") == ["a.jpg", "b.jpg"]


def _stubborn(result, started: threading.Event):
    """A prepare function that runs until Cancel, then returns result all the same."""

    def prepare(root, include_subfolders, progress=None, cancel=None):
        started.set()
        cancel.wait(10)
        return result

    return prepare


def test_cancel_after_the_finder_loop_returns_home(qtbot, make_window, photos,
                                                   make_pattern_jpeg, monkeypatch):
    from photosort.groups import Candidate, Group
    from photosort.modes import duplicates

    make_pattern_jpeg(photos / "a.jpg", 1)
    a = Candidate("a.jpg", "photo", 800, 600, 1, None, None)
    b = Candidate("b.jpg", "photo", 800, 600, 1, None, None)
    started = threading.Event()
    monkeypatch.setattr(duplicates, "find_duplicate_groups",
                        _stubborn([Group("exact", [a, b], {"a.jpg"})], started))
    w = make_window()
    _start_mode(qtbot, w, photos, "duplicates")
    assert started.wait(5)
    w.cancel_button.click()
    qtbot.waitUntil(lambda: w.current_page() is not w.progress_page)
    assert w.current_page() is w.home


def test_cancel_after_planning_returns_home(qtbot, make_window, photos, make_jpeg,
                                            monkeypatch):
    from photosort.journal import load_runs
    from photosort.plan import Move, Plan

    make_jpeg(photos / "a.jpg", EXIF)
    started = threading.Event()
    plan = Plan("date", [Move("a.jpg", "2025/a.jpg")], ["1 files → somewhere"])
    monkeypatch.setattr(date_mode, "prepare_date_plan", _stubborn(plan, started))
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.start_button.click()
    assert started.wait(5)
    w.cancel_button.click()
    qtbot.waitUntil(lambda: w.current_page() is not w.progress_page)
    assert w.current_page() is w.home
    w.go_button.click()  # the cancelled plan must not run
    assert (photos / "a.jpg").is_file()
    assert load_runs(photos) == []


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _face(embedding: np.ndarray, x: float = 20) -> Face:
    return Face((x, 20.0, x + 60, 80.0), np.zeros((5, 2), np.float32), 0.9,
                embedding.astype(np.float32), (200, 200))


class _Faces:
    """A face engine that knows each photo's faces by file name."""

    def __init__(self, faces: dict[str, list[Face]]) -> None:
        self.faces = faces

    def analyse(self, path: Path) -> list[Face]:
        return list(self.faces.get(Path(path).name, []))


def test_face_end_to_end(qtbot, make_window, photos, make_jpeg, store, errors,
                         monkeypatch):
    monkeypatch.setattr(main_window, "ensure_models", lambda parent, keys: True)
    atanu = store.add("Atanu", _unit(1), Image.new("RGB", (40, 40), "blue"))
    zoe = store.add("Zoe", _unit(2), Image.new("RGB", (40, 40), "green"))
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_jpeg(photos / name, size=(200, 200))
    rather_zoe = 0.3 * zoe.embeddings[0] + 0.7 * _unit(7)  # similarity about 0.39
    rather_zoe /= np.linalg.norm(rather_zoe)
    engine = _Faces({"a.jpg": [_face(atanu.embeddings[0])],
                     "b.jpg": [_face(atanu.embeddings[0]), _face(zoe.embeddings[0], 110)],
                     "c.jpg": [_face(atanu.embeddings[0]), _face(rather_zoe, 110)]})
    w = make_window()
    w.engine_factory = lambda use_gpu: engine

    w.folder_edit.setText(str(photos))
    w.mode_buttons["face"].setChecked(True)
    w.start_button.click()
    assert w.current_page() is w.face_setup_page
    people = w.face_setup_page.people_list
    for n in range(people.count()):
        people.item(n).setCheckState(Qt.CheckState.Checked)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    page = w.face_question_page
    assert page.heading_label.text().startswith("Is this Zoe? ")
    assert page.counter_label.text() == "1 of 1"
    page.yes_button.click()
    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines()[:2] == [
        "2 photos → 0 person folders, 1 together folders",
        "1 photos stay where they are"]  # a.jpg: Atanu alone

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 2 files." in w.done_label.text()
    assert (photos / "a.jpg").is_file()
    assert (photos / "Atanu & Zoe" / "b.jpg").is_file()
    assert (photos / "Atanu & Zoe" / "c.jpg").is_file()
    assert not any((photos / n).exists() for n in ("b.jpg", "c.jpg"))

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert all((photos / n).is_file() for n in ("a.jpg", "b.jpg", "c.jpg"))
    assert not (photos / "Atanu & Zoe").exists()
    assert errors == []


def _camera_jpeg(path: Path, make: str | None = None, model: str | None = None) -> Path:
    exif = Image.Exif()
    if make is not None:
        exif[271] = make
    if model is not None:
        exif[272] = model
    Image.new("RGB", (16, 16), "red").save(path, "JPEG", exif=exif)
    return path


def _go_and_undo(qtbot, w, photos, mode: str, moved: dict[str, str]):
    """Go, check each file in moved landed in its folder, then Undo and check
    the folder is as it was."""
    from photosort.journal import load_runs

    before = sorted(p.name for p in photos.iterdir())
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert f"Moved {len(moved)} files." in w.done_label.text()
    for name, folder in moved.items():
        assert (photos / folder / name).is_file()
        assert not (photos / name).exists()
    assert [run.mode for run in load_runs(photos)] == [mode]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert f"Undo complete: {len(moved)} files put back." in w.undo_label.text()
    assert sorted(p.name for p in photos.iterdir() if p.name != ".photosort") == before


def test_camera_end_to_end(qtbot, make_window, photos, errors):
    _camera_jpeg(photos / "a.jpg", "OPPO", "CPH2581")
    _camera_jpeg(photos / "b.jpg", model="Pixel 7")
    _camera_jpeg(photos / "c.jpg")
    w = make_window()
    _start_mode(qtbot, w, photos, "camera")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines() == [
        "2 files → 2 camera folders",
        "1 files have no camera info and stay where they are",
    ]
    _go_and_undo(qtbot, w, photos, "camera", {"a.jpg": "OPPO CPH2581", "b.jpg": "Pixel 7"})
    assert (photos / "c.jpg").is_file()
    assert errors == []


def _gps_jpeg(path: Path, lat: tuple[float, float, float], lon: tuple[float, float, float]):
    """A JPEG taken at lat, lon (degrees, minutes, seconds; north and east)."""
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: lat, 3: "E", 4: lon}
    Image.new("RGB", (16, 16), "red").save(path, "JPEG", exif=exif)
    return path


def test_location_end_to_end(qtbot, make_window, photos, errors):
    _gps_jpeg(photos / "a.jpg", (22.0, 34.0, 21.4), (88.0, 21.0, 50.0))  # Kolkata
    _gps_jpeg(photos / "b.jpg", (22.0, 34.0, 20.0), (88.0, 21.0, 49.0))
    _gps_jpeg(photos / "c.jpg", (48.0, 51.0, 24.0), (2.0, 21.0, 8.0))  # Paris
    _camera_jpeg(photos / "d.jpg", "OPPO", "CPH2581")  # no GPS
    w = make_window()
    _start_mode(qtbot, w, photos, "location")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page, timeout=10000)
    assert w.summary_label.text().splitlines() == [
        "3 files → 2 countries, 2 cities",
        "1 files have no location and stay where they are",
    ]
    _go_and_undo(qtbot, w, photos, "location", {
        "a.jpg": "India/Kolkata", "b.jpg": "India/Kolkata", "c.jpg": "France/Paris"})
    assert (photos / "d.jpg").is_file()
    assert errors == []


def test_type_end_to_end(qtbot, make_window, photos, errors):
    _camera_jpeg(photos / "IMG-20250315-WA0036.jpg")
    _camera_jpeg(photos / "IMG20250627.jpg", "OPPO", "CPH2581")
    Image.new("RGB", (16, 16), "red").save(photos / "Screenshot_2025.png", "PNG")
    _camera_jpeg(photos / "plain.jpg")
    w = make_window()
    _start_mode(qtbot, w, photos, "type")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines() == [
        "4 files → Camera Photos 1, Screenshots 1, WhatsApp 1, Other 1"]
    _go_and_undo(qtbot, w, photos, "type", {
        "IMG-20250315-WA0036.jpg": "WhatsApp", "IMG20250627.jpg": "Camera Photos",
        "Screenshot_2025.png": "Screenshots", "plain.jpg": "Other",
    })
    assert errors == []
