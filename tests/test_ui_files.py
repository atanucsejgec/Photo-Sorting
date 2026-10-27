"""The "files" flow (Extension mode): the subfolders warning, the summary, Go and Undo."""

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool

from photosort.hashing import sha256_file
from photosort.journal import load_runs
from photosort.modes import extension_mode
from photosort.people import PeopleStore
from photosort.settings import Settings
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
    warn and info must not open at all."""

    class Asked(list):
        answer = True

    questions = Asked()

    def ask(parent, title, text, yes, no, default="no"):
        questions.append((title, text, yes, no, default))
        return questions.answer

    def never(parent, title, text):
        raise AssertionError(f"unexpected dialog: {title}: {text}")

    monkeypatch.setattr(dialogs, "ask", ask)
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
