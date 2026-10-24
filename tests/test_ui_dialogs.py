import os
import subprocess
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool

from photosort.executor import execute
from photosort.journal import RunJournal, load_runs
from photosort.plan import Move, Plan
from photosort.settings import Settings, load_settings
from photosort.ui import history_dialog
from photosort.ui.history_dialog import HistoryDialog, undo_summary
from photosort.ui.main_window import MainWindow
from photosort.ui.settings_dialog import SettingsDialog
from photosort.undo import UndoResult

REPO = Path(__file__).resolve().parents[1]
DATE, MODE, FILES, STATUS = range(4)


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _run(root: Path, moves) -> str:
    plan = Plan("date", [Move(s, d) for s, d in moves], [])
    journal = RunJournal.create(root, "date", False, len(moves))
    execute(root, plan, journal)
    return journal.run_id


@pytest.fixture
def dialog_errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(HistoryDialog, "show_error", lambda self, text: shown.append(text))
    monkeypatch.setattr(SettingsDialog, "show_error", lambda self, text: shown.append(text))
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def make_history(qtbot, dialog_errors):
    dialogs = []

    def _make(root: Path) -> HistoryDialog:
        d = HistoryDialog(root)
        qtbot.addWidget(d)
        dialogs.append(d)
        return d

    yield _make
    for d in dialogs:
        d.close()
    QThreadPool.globalInstance().waitForDone()


def _column(d: HistoryDialog, col: int) -> list[str]:
    return [d.runs_table.item(r, col).text() for r in range(d.runs_table.rowCount())]


def test_undo_summary_wording():
    assert undo_summary(UndoResult(restored=2)) == "Undo complete: 2 files put back."
    result = UndoResult(restored=1, missing=["a.jpg", "b.jpg"],
                        renamed=[("c.jpg", "c (1).jpg")])
    assert undo_summary(result).splitlines() == [
        "Undo complete: 1 files put back.",
        "1 files were put back under a new name because the original name was taken.",
        "2 files were moved or deleted after the sort, so they were left where they are.",
    ]


def test_history_lists_runs_and_undoes(qtbot, make_history, tmp_path, dialog_errors):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    _write(tmp_path / "c.jpg", b"c")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    _run(tmp_path, [("b.jpg", "e/b.jpg"), ("c.jpg", "e/c.jpg")])

    d = make_history(tmp_path)
    assert d.runs_table.rowCount() == 2
    assert _column(d, FILES) == ["2", "1"]  # newest first
    assert _column(d, MODE) == ["Date", "Date"]
    assert _column(d, STATUS) == ["Done", "Done"]
    started = load_runs(tmp_path)[0].started.astimezone()
    assert d.runs_table.item(0, DATE).text() == started.strftime("%Y-%m-%d %H:%M")

    d.runs_table.selectRow(0)
    assert d.undo_button.isEnabled()
    d.undo_button.click()
    qtbot.waitUntil(lambda: _column(d, STATUS)[0] == "Undone")
    assert _column(d, STATUS) == ["Undone", "Done"]
    assert d.result_label.text() == "Undo complete: 2 files put back."
    assert (tmp_path / "b.jpg").read_bytes() == b"b"
    assert (tmp_path / "c.jpg").read_bytes() == b"c"
    assert (tmp_path / "d/a.jpg").read_bytes() == b"a"

    d.runs_table.selectRow(0)
    assert not d.undo_button.isEnabled()  # already undone
    d.runs_table.selectRow(1)
    assert d.undo_button.isEnabled()
    assert dialog_errors == []


def test_history_blocks_older_dependent_run(make_history, tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    _run(tmp_path, [("d/a.jpg", "e/a.jpg")])

    d = make_history(tmp_path)
    d.runs_table.selectRow(1)
    assert not d.undo_button.isEnabled()
    assert d.undo_button.toolTip() == "Undo newer runs first"
    d.runs_table.selectRow(0)
    assert d.undo_button.isEnabled()
    assert d.undo_button.toolTip() == ""


def test_history_shows_interrupted_run(make_history, tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    j = RunJournal.create(tmp_path, "date", False, planned=5)
    for name in ("a.jpg", "b.jpg"):
        i = j.record_intent(name, f"d/{name}")
        (tmp_path / "d").mkdir(exist_ok=True)
        os.rename(tmp_path / name, tmp_path / "d" / name)
        j.record_done(i)
    j._log.close()  # the process died: finish() never ran, meta "moved" is 0

    d = make_history(tmp_path)
    assert _column(d, STATUS) == ["Interrupted"]
    assert _column(d, FILES) == ["2"]
    d.runs_table.selectRow(0)
    assert d.undo_button.isEnabled()


def test_history_undo_error_is_shown(qtbot, make_history, tmp_path, dialog_errors,
                                     monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])

    def broken(root, run_id, progress=None):
        raise OSError("disk unplugged")

    monkeypatch.setattr(history_dialog, "undo_run", broken)
    d = make_history(tmp_path)
    d.runs_table.selectRow(0)
    d.undo_button.click()
    qtbot.waitUntil(lambda: dialog_errors != [])
    assert dialog_errors == ["Could not undo: disk unplugged"]
    assert d.undo_button.isEnabled()
    assert _column(d, STATUS) == ["Done"]


def test_closing_history_waits_for_undo(qtbot, make_history, tmp_path, monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow(root, run_id, progress=None):
        started.set()
        release.wait(10)
        finished.set()
        raise OSError("stopped")

    monkeypatch.setattr(history_dialog, "undo_run", slow)
    d = make_history(tmp_path)
    d.runs_table.selectRow(0)
    d.undo_button.click()
    assert not d.undo_button.isEnabled()
    assert started.wait(5)
    threading.Timer(0.2, release.set).start()
    d.reject()
    assert finished.is_set()


def test_settings_dialog_saves_gpu(qtbot, tmp_path, dialog_errors):
    path = tmp_path / "config" / "settings.json"
    settings = Settings(use_gpu=True)
    d = SettingsDialog(settings, path)
    qtbot.addWidget(d)
    assert d.gpu_check.text() == "Use GPU"
    assert d.gpu_check.isChecked()
    assert d.note_label.text() == (
        "GPU is used by the face and content modes (coming in later stages)."
    )
    d.gpu_check.setChecked(False)
    d.accept()
    assert settings.use_gpu is False
    assert load_settings(path).use_gpu is False
    assert dialog_errors == []


def test_settings_dialog_cancel_changes_nothing(qtbot, tmp_path):
    path = tmp_path / "config" / "settings.json"
    settings = Settings(use_gpu=True)
    d = SettingsDialog(settings, path)
    qtbot.addWidget(d)
    d.gpu_check.setChecked(False)
    d.reject()
    assert settings.use_gpu is True
    assert not path.exists()


@pytest.fixture
def window(qtbot, tmp_path, dialog_errors):
    w = MainWindow(Settings(), tmp_path / "config" / "settings.json")
    qtbot.addWidget(w)
    yield w
    w.close()
    QThreadPool.globalInstance().waitForDone()


def test_undo_history_needs_a_folder(window, monkeypatch):
    opened = []
    monkeypatch.setattr(HistoryDialog, "exec", lambda self: opened.append(self))
    window.undo_history_button.click()
    assert window.home_status_label.text() == "Pick a folder first."
    assert opened == []


def test_home_buttons_open_dialogs(window, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(HistoryDialog, "exec", lambda self: opened.append(self))
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: opened.append(self))
    window.folder_edit.setText(str(tmp_path))
    window.undo_history_button.click()
    window.settings_button.click()
    history, settings = opened
    assert isinstance(history, HistoryDialog) and history.root == tmp_path
    assert isinstance(settings, SettingsDialog) and settings.settings is window.settings
    assert window.home_status_label.text() == ""


def test_launcher_resolves_repo_through_symlink(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
    fake_uv.chmod(0o755)
    link = tmp_path / "launch"
    link.symlink_to(REPO / "scripts" / "photo-sorter.sh")

    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    out = subprocess.run([str(link), "--x", "a b"], env=env, cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == ["run", "--project", str(REPO), "photo-sorter", "--x", "a b"]


def test_scripts_are_executable():
    for name in ("photo-sorter.sh", "install-desktop-entry.sh"):
        assert os.access(REPO / "scripts" / name, os.X_OK)
