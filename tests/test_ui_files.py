"""The "files" flow (Extension and Date (all files)): the subfolders warning,
the date choice, the summary, Go and Undo."""

import os
import threading
from datetime import date, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool

from photosort.hashing import sha256_file
from photosort.journal import load_runs
from photosort.modes import all_dates, extension_mode
from photosort.people import PeopleStore
from photosort.settings import Settings, load_settings
from photosort.ui import dialogs
from photosort.ui.main_window import MainWindow

WARNING = ("Include subfolders?",
           "Files inside folders may belong to programs or projects. "
           "Moving them can break them. Continue?")


@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def asked(monkeypatch):
    """dialogs.ask records each question and gives the answer in asked.answer;
    dialogs.choose records each choice offered in asked.choices and gives
    asked.choice; asked.order is the titles of both, in turn. warn and info
    must not open at all."""

    class Asked(list):
        answer = True
        choice: int | None = 0

        def __init__(self):
            super().__init__()
            self.choices: list[tuple] = []
            self.order: list[str] = []

    questions = Asked()

    def ask(parent, title, text, yes, no, default="no"):
        questions.append((title, text, yes, no, default))
        questions.order.append(title)
        return questions.answer

    def choose(parent, title, text, options, default=0):
        questions.choices.append((title, text, list(options), default))
        questions.order.append(title)
        return questions.choice

    def never(parent, title, text):
        raise AssertionError(f"unexpected dialog: {title}: {text}")

    monkeypatch.setattr(dialogs, "ask", ask)
    monkeypatch.setattr(dialogs, "choose", choose)
    monkeypatch.setattr(dialogs, "warn", never)
    monkeypatch.setattr(dialogs, "info", never)
    return questions


@pytest.fixture
def planned(monkeypatch):
    """How many times the Extension plan was prepared."""
    calls: list[bool] = []
    real = extension_mode.prepare_extension_plan

    def counted(root, include_subfolders, progress=None, cancel=None):
        calls.append(include_subfolders)
        return real(root, include_subfolders, progress=progress, cancel=cancel)

    monkeypatch.setattr(extension_mode, "prepare_extension_plan", counted)
    return calls


@pytest.fixture
def make_window(qtbot, tmp_path, errors, asked):
    windows = []

    def _make(settings: Settings | None = None) -> MainWindow:
        w = MainWindow(settings or Settings(), tmp_path / "config" / "settings.json",
                       people_store=PeopleStore(tmp_path / "people"))
        qtbot.addWidget(w)
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "files"
    for rel in ("app.apk", "game.APK", "doc.pdf", "sub/setup.exe", "sub/README"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())
    return root


def _start(w, root: Path, subfolders: bool) -> None:
    w.folder_edit.setText(str(root))
    w.subfolders_check.setChecked(subfolders)
    w.mode_buttons["extension"].click()


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


def test_the_tile_comes_after_type(make_window):
    w = make_window()
    keys = list(w.mode_buttons)
    assert keys[keys.index("type") + 1] == "extension"
    tile = w.mode_buttons["extension"]
    assert (tile.name, tile.hint) == ("Extension", "APK, EXE, PDF…")
    assert tile.isEnabled()


def test_no_warning_without_subfolders(qtbot, make_window, folder, asked, errors, planned):
    w = make_window()
    _start(w, folder, subfolders=False)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert asked == []
    assert asked.choices == []  # Extension asks no date
    assert planned == [False]
    assert w.summary_label.text().splitlines() == ["3 files → 2 folders"]
    assert w.go_button.isEnabled()
    assert errors == []


def test_the_warning_with_subfolders_cancel(qtbot, make_window, folder, asked, planned):
    asked.answer = False
    w = make_window()
    _start(w, folder, subfolders=True)
    assert asked == [(*WARNING, "Continue", "Cancel", "no")]
    assert w.current_page() is w.home
    assert planned == []
    assert w._plan is None
    assert not w.sidebar.is_locked()
    assert not w.busy


def test_the_warning_with_subfolders_continue(qtbot, make_window, folder, asked, planned):
    w = make_window()
    _start(w, folder, subfolders=True)
    assert asked == [(*WARNING, "Continue", "Cancel", "no")]
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert planned == [True]
    assert w.summary_label.text().splitlines() == ["5 files → 4 folders"]


def test_go_then_undo_restores_every_file(qtbot, make_window, folder, errors):
    before = _hashes(folder)
    w = make_window()
    _start(w, folder, subfolders=True)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 5 files." in w.done_label.text()
    assert _hashes(folder) == {
        "APK/app.apk": before["app.apk"], "APK/game.APK": before["game.APK"],
        "PDF/doc.pdf": before["doc.pdf"], "EXE/setup.exe": before["sub/setup.exe"],
        "No extension/README": before["sub/README"]}
    assert [run.mode for run in load_runs(folder)] == ["extension"]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert "Undo complete: 5 files put back." in w.undo_label.text()
    assert _hashes(folder) == before
    assert errors == []


def test_cancel_on_the_progress_page_then_run_again(qtbot, make_window, folder, monkeypatch,
                                                    errors):
    # Review Focus 5
    started = threading.Event()
    real = extension_mode.prepare_extension_plan
    calls = []

    def first_slow(root, include_subfolders, progress=None, cancel=None):
        calls.append(include_subfolders)
        if len(calls) == 1:
            started.set()
            cancel.wait(10)
            return None
        return real(root, include_subfolders, progress=progress, cancel=cancel)

    monkeypatch.setattr(extension_mode, "prepare_extension_plan", first_slow)
    w = make_window()
    _start(w, folder, subfolders=False)
    assert w.current_page() is w.progress_page
    assert started.wait(5)
    w.cancel_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    qtbot.waitUntil(lambda: not w.busy)
    assert w._plan is None
    assert not w.sidebar.is_locked()

    w.mode_buttons["extension"].click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines() == ["3 files → 2 folders"]
    assert len(calls) == 2
    assert errors == []


# ----- Date (all files) ----------------------------------------------------------

DATE_CHOICE = ("Which date?",
               "Sort files by the date they were last modified, or the date they were "
               "created? Photos and videos use the date they were taken.",
               ["Modified", "Created"])


def _at(day: date) -> float:
    return datetime(day.year, day.month, day.day, 12).timestamp()


@pytest.fixture
def dated(tmp_path, monkeypatch):
    """Files of any type, modified on 5, 6 and 7 March 2024, all created on 2 January 2020."""
    monkeypatch.setattr(all_dates, "created_time", lambda path: _at(date(2020, 1, 2)))
    root = tmp_path / "dated"
    for rel, day in (("app.apk", 5), ("doc.pdf", 6), ("sub/setup.exe", 7)):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())
        os.utime(path, (_at(date(2024, 3, day)), _at(date(2024, 3, day))))
    return root


@pytest.fixture
def date_sources(monkeypatch):
    """The date source each Date (all files) plan was prepared with."""
    sources: list[str] = []
    real = all_dates.prepare_all_dates_plan

    def counted(root, include_subfolders, *, date_source, progress=None, cancel=None):
        sources.append(date_source)
        return real(root, include_subfolders, date_source=date_source, progress=progress,
                    cancel=cancel)

    monkeypatch.setattr(all_dates, "prepare_all_dates_plan", counted)
    return sources


def _start_dates(w, root: Path, subfolders: bool) -> None:
    w.folder_edit.setText(str(root))
    w.subfolders_check.setChecked(subfolders)
    w.mode_buttons["all_dates"].click()


def test_the_date_tile_comes_after_extension(make_window):
    w = make_window()
    keys = list(w.mode_buttons)
    assert keys[keys.index("extension") + 1] == "all_dates"
    tile = w.mode_buttons["all_dates"]
    assert (tile.name, tile.hint) == ("Date (all files)", "Any file, year / month")
    assert tile.isEnabled()


def test_the_date_choice_without_subfolders(qtbot, make_window, dated, asked, errors,
                                            date_sources):
    w = make_window()
    _start_dates(w, dated, subfolders=False)
    assert asked == []  # no warning
    assert asked.choices == [(*DATE_CHOICE, 0)]  # Modified, the first time
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert date_sources == ["modified"]
    assert w.summary_label.text().splitlines() == ["2 files → 1 month folders, 0 day folders"]
    assert w.go_button.isEnabled()
    assert errors == []


def test_esc_at_the_date_choice_goes_home(qtbot, make_window, dated, asked, date_sources):
    # Review Focus 5
    asked.choice = None
    w = make_window(Settings(file_date="created"))
    _start_dates(w, dated, subfolders=False)
    assert asked.choices == [(*DATE_CHOICE, 1)]
    assert w.current_page() is w.home
    assert date_sources == []
    assert w._plan is None
    assert w.settings.file_date == "created"
    assert not w.sidebar.is_locked()
    assert not w.busy


def test_subfolders_gives_the_warning_then_the_choice(qtbot, make_window, dated, asked,
                                                      date_sources):
    asked.choice = 1
    w = make_window()
    _start_dates(w, dated, subfolders=True)
    assert asked.order == ["Include subfolders?", "Which date?"]
    assert asked == [(*WARNING, "Continue", "Cancel", "no")]
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert date_sources == ["created"]
    assert w.summary_label.text().splitlines() == ["3 files → 1 month folders, 1 day folders"]


def test_cancel_at_the_warning_asks_no_date(qtbot, make_window, dated, asked, date_sources):
    asked.answer = False
    w = make_window()
    _start_dates(w, dated, subfolders=True)
    assert asked.order == ["Include subfolders?"]
    assert w.current_page() is w.home
    assert date_sources == []


def test_the_choice_is_remembered(qtbot, make_window, dated, asked, tmp_path, date_sources):
    asked.choice = 1
    w = make_window()
    _start_dates(w, dated, subfolders=False)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.settings.file_date == "created"
    assert load_settings(tmp_path / "config" / "settings.json").file_date == "created"

    w.show_page(w.home)
    asked.choice = 0
    w.mode_buttons["all_dates"].click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert [default for *_, default in asked.choices] == [0, 1]  # "Created" focused
    assert date_sources == ["created", "modified"]
    assert w.settings.file_date == "modified"


def test_date_go_then_undo_restores_every_file(qtbot, make_window, dated, asked, errors):
    before = _hashes(dated)
    w = make_window()
    _start_dates(w, dated, subfolders=True)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 3 files." in w.done_label.text()
    month = "2024/03-March"
    assert _hashes(dated) == {
        f"{month}/app.apk": before["app.apk"], f"{month}/doc.pdf": before["doc.pdf"],
        f"{month}/setup.exe": before["sub/setup.exe"]}
    assert [run.mode for run in load_runs(dated)] == ["all_dates"]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert "Undo complete: 3 files put back." in w.undo_label.text()
    assert _hashes(dated) == before
    assert errors == []
