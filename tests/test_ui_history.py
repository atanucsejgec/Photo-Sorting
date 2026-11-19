"""The History page: every run of every folder, newest first; undo or forget
several at once; a folder that was moved is found again.

Every dialog is patched (dialogs.ask, show_error, the folder picker): no
real modal ever opens here.
"""

import os
import threading
from pathlib import Path

import pytest
from keys import (
    _activate,
    _hold,
    _key,
    errors,  # noqa: F401  (fixtures, used by name)
    make_window,  # noqa: F401
    models_present,  # noqa: F401
    settings_path,  # noqa: F401
)
from PySide6.QtCore import QItemSelectionModel, Qt, QThreadPool
from PySide6.QtWidgets import QApplication

from photosort import journal
from photosort.executor import execute
from photosort.journal import RunJournal, load_all_runs, load_runs
from photosort.plan import Move, Plan
from photosort.ui import dialogs, history_page
from photosort.ui.history_page import HistoryPage, undo_summary
from photosort.ui.main_window import MainWindow
from photosort.undo import UndoResult

_real_write_meta = journal._write_meta

WHEN, MODE, FOLDER, FILES, STATUS = range(5)
DEPEND = "Forget the older runs that depend on it too, or undo it first."
FORGET = "Your files stay where they are, but these runs can no longer be undone."


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _run(root: Path, moves) -> str:
    plan = Plan("date", [Move(s, d) for s, d in moves], [])
    journal_ = RunJournal.create(root, "date", False, len(moves))
    execute(root, plan, journal_)
    return journal_.run_id


class Asked:
    """dialogs.ask: each call is recorded as (title, text, yes, no, default)
    and answered from answers (in turn), else answer."""

    def __init__(self) -> None:
        self.answer = True
        self.answers: list[bool] = []
        self.calls: list[tuple[str, str, str, str, str]] = []

    def __call__(self, parent, title, text, yes, no, default="no"):
        self.calls.append((title, text, yes, no, default))
        return self.answers.pop(0) if self.answers else self.answer


@pytest.fixture
def asked(monkeypatch) -> Asked:
    record = Asked()
    monkeypatch.setattr(dialogs, "ask", record)
    return record


@pytest.fixture
def shown(monkeypatch) -> list[str]:
    """What the page and the window show as errors."""
    texts: list[str] = []
    monkeypatch.setattr(HistoryPage, "show_error", lambda self, text: texts.append(text))
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: texts.append(text))
    return texts


@pytest.fixture
def picker(monkeypatch):
    """The folder picker: each call is recorded as (title, start) and
    answered from picker.picks in turn (None: cancelled)."""

    class Picker:
        picks: list[Path | None] = []
        calls: list[tuple[str, str]] = []

    record = Picker()
    record.picks, record.calls = [], []

    def choose(parent, title, start):
        record.calls.append((title, start))
        return record.picks.pop(0) if record.picks else None

    monkeypatch.setattr(history_page, "choose_folder", choose)
    return record


@pytest.fixture
def make_history(qtbot, asked, shown):
    """A History page; its root is the Home folder given to load."""

    def _make(root: Path | None) -> HistoryPage:
        d = HistoryPage()
        qtbot.addWidget(d)
        d.resize(900, 500)
        d.load(root)
        d.show()
        qtbot.waitExposed(d)
        _activate(qtbot, d)  # its shortcuts work
        return d

    yield _make
    QThreadPool.globalInstance().waitForDone()


def _column(d: HistoryPage, col: int) -> list[str]:
    return [d.runs_table.item(r, col).text() for r in range(d.runs_table.rowCount())]


def _select(d: HistoryPage, *rows: int) -> None:
    d.runs_table.clearSelection()
    model = d.runs_table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for r in rows:
        model.select(d.runs_table.model().index(r, 0), flags)


def _ids(d: HistoryPage) -> list[str]:
    return [d.runs_table.item(r, WHEN).data(Qt.ItemDataRole.UserRole)
            for r in range(d.runs_table.rowCount())]


def _selected_ids(d: HistoryPage) -> list[str]:
    ids = _ids(d)
    return [ids[i.row()] for i in d.runs_table.selectionModel().selectedRows()]


def _focus(qtbot, widget):
    widget.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is widget)


# ----- one run (as before) -----------------------------------------------------------------

def test_undo_summary_wording():
    assert undo_summary(UndoResult(restored=2)) == "Undo complete: 2 files put back."
    result = UndoResult(restored=1, missing=["a.jpg", "b.jpg"],
                        renamed=[("c.jpg", "c (1).jpg")])
    assert undo_summary(result).splitlines() == [
        "Undo complete: 1 files put back.",
        "1 files were put back under a new name because the original name was taken.",
        "2 files were moved or deleted after the sort, so they were left where they are.",
    ]
    stuck = UndoResult(restored=1, failed=[("a.jpg", "in use"), ("b.jpg", "in use")])
    assert undo_summary(stuck).splitlines() == [
        "Undo not finished: 1 files put back.",
        "2 files couldn't be put back yet. Close any program using them and "
        "press Undo again.",
    ]


def test_history_lists_runs_and_undoes(qtbot, make_history, tmp_path, shown, asked):
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
    assert d.runs_table.item(0, WHEN).text() == started.strftime("%Y-%m-%d %H:%M")

    _select(d, 0)
    assert d.undo_button.isEnabled()
    d.undo_button.click()
    assert [c[0] for c in asked.calls] == ["Undo this sort?"]
    qtbot.waitUntil(lambda: _column(d, STATUS)[0] == "Undone")
    assert _column(d, STATUS) == ["Undone", "Done"]
    assert d.result_label.text() == "Undo complete: 2 files put back."
    assert (tmp_path / "b.jpg").read_bytes() == b"b"
    assert (tmp_path / "c.jpg").read_bytes() == b"c"
    assert (tmp_path / "d/a.jpg").read_bytes() == b"a"

    _select(d, 0)
    assert not d.undo_button.isEnabled()  # already undone
    _select(d, 1)
    assert d.undo_button.isEnabled()
    assert shown == []


def test_history_blocks_older_dependent_run(make_history, tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    run2 = _run(tmp_path, [("d/a.jpg", "e/a.jpg")])

    d = make_history(tmp_path)
    _select(d, 1)
    assert not d.undo_button.isEnabled()
    assert d.undo_button.toolTip() == f"Undo the newer run(s) first: {run2}"
    assert d.blocked_label.text() == f"Undo the newer run(s) first: {run2}"
    _select(d, 0)
    assert d.undo_button.isEnabled()
    assert d.undo_button.toolTip() == ""
    assert d.blocked_label.text() == ""


def test_history_names_folder_of_blocking_run_inside(make_history, tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    first = _run(tmp_path, [("a.jpg", "d/a.jpg")])
    inner = _run(tmp_path / "d", [("a.jpg", "e/a.jpg")])  # owner picked "d" later

    d = make_history(tmp_path)
    _select(d, _ids(d).index(first))
    assert not d.undo_button.isEnabled()
    assert d.blocked_label.text() == f"Undo the newer run(s) first: {inner} (in d)"


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
    _select(d, 0)
    assert d.undo_button.isEnabled()


def test_history_undo_error_is_shown(qtbot, make_history, tmp_path, shown, monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])

    def broken(root, run_id, progress=None):
        raise OSError("disk unplugged")

    monkeypatch.setattr(history_page, "undo_run", broken)
    d = make_history(tmp_path)
    _select(d, 0)
    d.undo_button.click()
    qtbot.waitUntil(lambda: shown != [])
    assert shown == ["Could not undo: disk unplugged"]
    assert d.undo_button.isEnabled()
    assert _column(d, STATUS) == ["Done"]


def test_history_says_a_blocked_undo_as_done_does(qtbot, make_history, tmp_path, shown,
                                                  monkeypatch):
    from photosort.undo import BlockingRun, UndoBlocked

    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])

    def blocked(root, run_id, progress=None):  # a newer run came after the page loaded
        raise UndoBlocked([BlockingRun("20261024-130000-date", "2025/02-February")])

    monkeypatch.setattr(history_page, "undo_run", blocked)
    d = make_history(tmp_path)
    _select(d, 0)
    d.undo_button.click()
    qtbot.waitUntil(lambda: shown != [])
    assert shown == [
        "Undo the newer run(s) first: 20261024-130000-date (in 2025/02-February)"]


def test_history_says_while_it_undoes(qtbot, make_history, tmp_path, monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    started, release = threading.Event(), threading.Event()

    def slow(root, run_id, progress=None):
        started.set()
        release.wait(10)
        raise OSError("stopped")

    monkeypatch.setattr(history_page, "undo_run", slow)
    d = make_history(tmp_path)
    running = []
    d.undo_running.connect(running.append)
    _select(d, 0)
    d.undo_button.click()
    assert running == [True] and d.undoing()
    assert not d.undo_button.isEnabled()
    assert not d.forget_button.isEnabled()  # nothing is forgotten mid-undo
    d.undo_button.click()  # one undo at a time
    assert started.wait(5)
    release.set()
    qtbot.waitUntil(lambda: running == [True, False])
    assert not d.undoing()


def _fail_undo_moves_of(monkeypatch, names):
    import errno

    from photosort import undo

    real = undo.move_file

    def flaky(src, dst):
        if os.path.basename(src) in names:
            raise PermissionError(errno.EACCES, "File in use", str(src))
        return real(src, dst)

    monkeypatch.setattr(undo, "move_file", flaky)


def test_history_partial_undo_keeps_undo_available(qtbot, make_history, tmp_path, shown,
                                                   monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    _run(tmp_path, [("a.jpg", "d/a.jpg"), ("b.jpg", "d/b.jpg")])
    d = make_history(tmp_path)
    _select(d, 0)

    _fail_undo_moves_of(monkeypatch, {"b.jpg"})
    d.undo_button.click()
    qtbot.waitUntil(lambda: "couldn't be put back" in d.result_label.text())
    monkeypatch.undo()
    monkeypatch.setattr(dialogs, "ask", lambda *args, **kwargs: True)  # undone too
    monkeypatch.setattr(HistoryPage, "show_error", lambda self, text: shown.append(text))
    assert _column(d, STATUS) == ["Done"]
    assert d.undo_button.isEnabled()

    d.undo_button.click()
    qtbot.waitUntil(lambda: _column(d, STATUS) == ["Undone"])
    assert d.result_label.text() == "Undo complete: 1 files put back."
    assert (tmp_path / "b.jpg").read_bytes() == b"b"
    assert shown == []


# ----- in the window -----------------------------------------------------------------------

def test_history_without_a_folder_lists_every_run(make_window, tmp_path, shown):
    w = make_window()
    w.sidebar.items["history"].click()
    page = w.history_page
    assert page.runs_table.rowCount() == 0
    assert not page.undo_button.isEnabled() and not page.forget_button.isEnabled()
    assert not page.filter_switch.isEnabled()
    _write(tmp_path / "photos" / "a.jpg", b"a")
    _run(tmp_path / "photos", [("a.jpg", "d/a.jpg")])
    w.sidebar.items["sort"].click()
    w.sidebar.items["history"].click()
    assert page.runs_table.rowCount() == 1  # no folder chosen: still listed


def test_history_undo_asks_first(qtbot, make_window, tmp_path, asked, shown):
    root = tmp_path / "photos"
    _write(root / "a.jpg", b"a")
    _run(root, [("a.jpg", "d/a.jpg")])
    w = make_window()
    w.folder_edit.setText(str(root))
    w.sidebar.items["history"].click()
    page = w.history_page
    assert page.runs_table.rowCount() == 1
    _focus(qtbot, page.runs_table)

    asked.answer = False
    _key(qtbot, Qt.Key.Key_Return)
    assert [(c[0], c[2], c[3], c[4]) for c in asked.calls] == [
        ("Undo this sort?", "Undo", "Cancel", "no")]
    qtbot.wait(50)
    assert page.runs_table.item(0, STATUS).text() == "Done"
    assert (root / "d" / "a.jpg").is_file()

    asked.answer = True
    page.undo_button.click()
    qtbot.waitUntil(lambda: page.runs_table.item(0, STATUS).text() == "Undone")
    assert (root / "a.jpg").read_bytes() == b"a"
    assert page.result_label.text() == "Undo complete: 1 files put back."
    assert len(asked.calls) == 2


def test_a_held_enter_on_history_asks_once(qtbot, make_window, tmp_path, asked):
    root = tmp_path / "photos"
    _write(root / "a.jpg", b"a")
    _run(root, [("a.jpg", "d/a.jpg")])
    w = make_window()
    w.folder_edit.setText(str(root))
    w.sidebar.items["history"].click()
    _focus(qtbot, w.history_page.runs_table)
    asked.answer = False
    _key(qtbot, Qt.Key.Key_Return)
    _hold(Qt.Key.Key_Return)  # its repeats ask nothing more
    assert len(asked.calls) == 1


def test_history_locks_the_sidebar_while_it_undoes(qtbot, make_window, tmp_path, asked,
                                                   monkeypatch):
    root = tmp_path / "photos"
    _write(root / "a.jpg", b"a")
    _run(root, [("a.jpg", "d/a.jpg")])
    release = threading.Event()
    real = history_page.undo_run

    def slow(*args, **kwargs):
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(history_page, "undo_run", slow)
    w = make_window()
    w.folder_edit.setText(str(root))
    w.sidebar.items["history"].click()
    w.history_page.undo_button.click()
    assert not w.sidebar.items["sort"].isEnabled()
    release.set()
    qtbot.waitUntil(lambda: w.sidebar.items["sort"].isEnabled())
    assert w.history_page.runs_table.item(0, STATUS).text() == "Undone"


# ----- every folder ------------------------------------------------------------------------

def _three_folders(tmp_path) -> tuple[Path, Path, Path, dict[str, str]]:
    """Runs in photos, photos/2025 (inside it) and other: oldest first."""
    photos, inner, other = tmp_path / "photos", tmp_path / "photos" / "2025", tmp_path / "other"
    _write(photos / "a.jpg", b"a")
    _write(inner / "b.jpg", b"b")
    _write(other / "c.jpg", b"c")
    ids = {"photos": _run(photos, [("a.jpg", "x/a.jpg")]),
           "inner": _run(inner, [("b.jpg", "y/b.jpg")]),
           "other": _run(other, [("c.jpg", "z/c.jpg")])}
    return photos, inner, other, ids


def test_lists_runs_from_all_folders_with_folder_column(make_history, tmp_path):
    photos, inner, other, ids = _three_folders(tmp_path)
    d = make_history(photos)
    assert [d.runs_table.horizontalHeaderItem(c).text() for c in range(5)] == [
        "When", "Mode", "Folder", "Files", "Status"]
    assert _ids(d) == [ids["other"], ids["inner"], ids["photos"]]  # newest first
    assert _column(d, FOLDER) == [str(other), str(inner), str(photos)]
    assert [d.runs_table.item(r, FOLDER).toolTip() for r in range(3)] == [
        str(other), str(inner), str(photos)]
    assert d.runs_table.textElideMode() == Qt.TextElideMode.ElideMiddle
    assert _column(d, STATUS) == ["Done", "Done", "Done"]

    d.load(None)  # no Home folder: still every run
    assert d.runs_table.rowCount() == 3
    assert not d.filter_switch.isEnabled()


def test_this_folder_only_filter(qtbot, make_history, tmp_path):
    photos, inner, other, ids = _three_folders(tmp_path)
    d = make_history(photos)
    assert not d.filter_switch.isChecked()  # off by default
    assert d.filter_switch.text() == "This folder only"
    d.filter_switch.click()
    assert _ids(d) == [ids["inner"], ids["photos"]]  # the folder and those inside it
    d.load(inner)
    assert _ids(d) == [ids["inner"]]
    d.filter_switch.click()
    assert d.runs_table.rowCount() == 3

    _select(d, 0)
    _focus(qtbot, d.runs_table)
    _key(qtbot, Qt.Key.Key_F)  # the key does it too
    assert d.filter_switch.isChecked()
    assert _ids(d) == [ids["inner"]]


def _chain(tmp_path) -> tuple[Path, list[str]]:
    """Three runs of one folder, each on its own file: oldest first."""
    root = tmp_path / "photos"
    for name in "abc":
        _write(root / f"{name}.jpg", name.encode())
    return root, [_run(root, [(f"{n}.jpg", f"{n}/{n}.jpg")]) for n in "abc"]


def test_multi_undo_newest_first_restores_all(qtbot, make_history, tmp_path, asked, shown,
                                             monkeypatch):
    root, runs = _chain(tmp_path)
    order = []
    real = history_page.undo_run

    def watched(root_, run_id, progress=None):
        order.append(run_id)
        return real(root_, run_id, progress=progress)

    monkeypatch.setattr(history_page, "undo_run", watched)
    d = make_history(root)
    d.runs_table.selectAll()
    asked.answer = False
    d.undo_button.click()
    assert asked.calls == [("Undo 3 runs?", "Put back the files of these 3 runs, newest first?",
                            "Undo", "Cancel", "no")]
    assert order == []

    asked.answer = True
    d.undo_button.click()
    qtbot.waitUntil(lambda: _column(d, STATUS) == ["Undone"] * 3)
    assert order == runs[::-1]
    for name in "abc":
        assert (root / f"{name}.jpg").read_bytes() == name.encode()
    assert d.result_label.text() == (
        f"Undo complete: 3 runs undone ({', '.join(runs[::-1])}), 3 files put back.")
    assert shown == []


def test_multi_undo_stops_at_blocked(qtbot, make_history, tmp_path, asked, shown):
    root = tmp_path / "photos"
    for name in "ab":
        _write(root / f"{name}.jpg", name.encode())
    first = _run(root, [("a.jpg", "d/a.jpg")])
    middle = _run(root, [("b.jpg", "e/b.jpg")])
    newest = _run(root, [("d/a.jpg", "f/a.jpg")])  # moved what first placed
    d = make_history(root)
    ids = _ids(d)
    _select(d, ids.index(middle), ids.index(first))  # not newest: it blocks first
    d.undo_button.click()
    assert asked.calls[0][0] == "Undo 2 runs?"
    qtbot.waitUntil(lambda: shown != [])
    assert (root / "b.jpg").read_bytes() == b"b"  # middle went back
    assert (root / "f/a.jpg").read_bytes() == b"a"  # first could not
    status = dict(zip(_ids(d), _column(d, STATUS)))
    assert status[middle] == "Undone" and status[first] == "Done"
    text = (f"Undo stopped: 1 of 2 runs undone ({middle}), 1 file put back.\n"
            f"Stopped at {first}: Undo the newer run(s) first: {newest}")
    assert d.result_label.text() == text
    assert shown == [text]


def test_multi_undo_stops_at_failed_and_does_not_try_the_rest(qtbot, make_history,
                                                             tmp_path, shown, monkeypatch):
    root, runs = _chain(tmp_path)
    tried = []
    real = history_page.undo_run

    def flaky(root_, run_id, progress=None):
        tried.append(run_id)
        if run_id == runs[1]:
            raise OSError("disk unplugged")
        return real(root_, run_id, progress=progress)

    monkeypatch.setattr(history_page, "undo_run", flaky)
    d = make_history(root)
    d.runs_table.selectAll()
    d.undo_button.click()
    qtbot.waitUntil(lambda: shown != [])
    assert tried == [runs[2], runs[1]]  # the oldest is not tried
    assert shown == [
        f"Undo stopped: 1 of 3 runs undone ({runs[2]}), 1 file put back.\n"
        f"Stopped at {runs[1]}: Could not undo: disk unplugged\n"
        "The older runs were not tried."]
    assert (root / "a/a.jpg").is_file()


def test_undo_brings_in_each_folder_first(qtbot, make_history, tmp_path, monkeypatch):
    photos, inner, other, ids = _three_folders(tmp_path)
    brought = []
    monkeypatch.setattr(history_page.migrate, "bring_in",
                        lambda root, progress=None, cancel=None: brought.append(root))
    d = make_history(photos)
    _select(d, 0, 2)  # other and photos
    d.undo_button.click()
    qtbot.waitUntil(lambda: _column(d, STATUS)[0] == "Undone")
    QThreadPool.globalInstance().waitForDone()
    assert sorted(brought) == sorted([other, photos])


# ----- forget ------------------------------------------------------------------------------

def test_delete_asks_with_keep_focused_and_forgets(qtbot, make_history, tmp_path, asked):
    root, runs = _chain(tmp_path)
    d = make_history(root)
    _select(d, 0, 2)  # c and a: neither depends on the other
    before = {p: p.read_bytes() for p in root.rglob("*.jpg")}

    asked.answer = False
    d.forget_button.click()
    assert asked.calls == [("Forget 2 runs?", FORGET, "Forget", "Keep", "no")]
    assert d.runs_table.rowCount() == 3

    asked.answer = True
    _focus(qtbot, d.runs_table)
    _key(qtbot, Qt.Key.Key_Delete)
    assert len(asked.calls) == 2
    assert _ids(d) == [runs[1]]
    assert [r.run_id for r in load_all_runs()] == [runs[1]]
    assert {p: p.read_bytes() for p in root.rglob("*.jpg")} == before  # nothing moved
    assert {k for k, _, _ in journal.forgotten_keys()} == {runs[0], runs[2]}
    assert d.result_label.text() == "Forgot 2 runs."


def test_delete_of_undone_runs_asks_shorter(qtbot, make_history, tmp_path, asked):
    root, runs = _chain(tmp_path)
    d = make_history(root)
    _select(d, 0)
    d.undo_button.click()
    qtbot.waitUntil(lambda: _column(d, STATUS)[0] == "Undone")
    _select(d, 0)
    asked.calls.clear()
    d.forget_button.click()
    assert asked.calls == [("Forget 1 run?", "", "Forget", "Keep", "no")]
    assert _ids(d) == runs[1::-1]


def test_delete_with_dependents_offers_forget_all(qtbot, make_history, tmp_path, asked):
    root = tmp_path / "photos"
    _write(root / "a.jpg", b"a")
    _write(root / "b.jpg", b"b")
    first = _run(root, [("a.jpg", "d/a.jpg")])
    second = _run(root, [("d/a.jpg", "e/a.jpg")])  # first depends on it
    third = _run(root, [("e/a.jpg", "f/a.jpg")])  # second (and so first) on this
    apart = _run(root, [("b.jpg", "g/b.jpg")])
    d = make_history(root)
    _select(d, _ids(d).index(third))

    asked.answer = False
    d.forget_button.click()
    assert asked.calls == [("Older runs depend on it", f"{DEPEND}\n\n{second}, {first}",
                            "Forget all 3", "Cancel", "no")]
    assert d.runs_table.rowCount() == 4

    assert set(_selected_ids(d)) == {third, second, first}  # shown as they go
    asked.answer = True
    d.forget_button.click()
    assert len(asked.calls) == 2  # Forget all is the one question
    assert _ids(d) == [apart]
    assert (root / "f/a.jpg").read_bytes() == b"a"


def test_delete_refuses_when_a_record_cannot_be_read(qtbot, make_history, tmp_path, asked,
                                                     shown, monkeypatch):
    root, runs = _chain(tmp_path)

    def unreadable(root_, run_id):
        raise ValueError("bad line")

    monkeypatch.setattr(history_page, "dependents", unreadable)
    d = make_history(root)
    _select(d, 0)
    d.forget_button.click()
    assert asked.calls == []
    assert shown == ["Couldn't check which runs depend on these, so nothing was forgotten: "
                     "bad line"]
    assert len(load_all_runs()) == 3


def test_held_delete_asks_once(qtbot, make_history, tmp_path, asked):
    _chain(tmp_path)
    d = make_history(tmp_path / "photos")
    _select(d, 0)
    _focus(qtbot, d.runs_table)
    asked.answer = False
    _key(qtbot, Qt.Key.Key_Delete)
    _hold(Qt.Key.Key_Delete)  # its repeats ask nothing more
    assert len(asked.calls) == 1


def test_dependents_are_worked_out_only_on_delete(qtbot, make_history, tmp_path, asked,
                                                  monkeypatch):
    _chain(tmp_path)
    calls = []
    monkeypatch.setattr(history_page, "dependents",
                        lambda root, run_id: calls.append(run_id) or [])
    d = make_history(tmp_path / "photos")
    for r in range(3):
        _select(d, r)
    d.runs_table.selectAll()
    assert calls == []
    asked.answer = False
    d.forget_button.click()
    assert len(calls) == 3


# ----- a folder that was moved -------------------------------------------------------------

def _moved_folder(tmp_path) -> tuple[Path, Path, str]:
    old = tmp_path / "photos"
    _write(old / "a.jpg", b"a")
    run = _run(old, [("a.jpg", "d/a.jpg")])
    new = tmp_path / "renamed"
    os.rename(old, new)
    return old, new, run


def test_folder_not_found_then_wrong_pick_refused(qtbot, make_history, tmp_path, asked,
                                                  shown, picker):
    old, new, run = _moved_folder(tmp_path)
    wrong = tmp_path / "wrong"
    _write(wrong / "a.jpg", b"a")  # a.jpg, but not at d/a.jpg: not the folder
    d = make_history(None)
    assert _column(d, STATUS) == ["Folder not found"]
    _select(d, 0)
    assert d.undo_button.isEnabled()

    d.undo_button.click()  # cancelled: nothing happens
    assert picker.calls == [("Where is this folder now?", str(tmp_path))]
    assert asked.calls == [] and shown == []

    picker.picks = [wrong]
    d.undo_button.click()
    assert shown == ["This doesn't look like the same folder.\n"
                     f"None of the files this sort moved are in {wrong}."]
    assert asked.calls == []
    assert [r.root for r in load_all_runs()] == [old]
    assert _column(d, STATUS) == ["Folder not found"]


def test_relocate_then_undo_restores(qtbot, make_history, tmp_path, asked, shown, picker):
    old = tmp_path / "photos"
    _write(old / "a.jpg", b"a")
    _write(old / "2025" / "b.jpg", b"b")
    run = _run(old, [("a.jpg", "d/a.jpg")])
    inner = _run(old / "2025", [("b.jpg", "e/b.jpg")])  # a folder inside it
    new = tmp_path / "renamed"
    os.rename(old, new)
    d = make_history(None)
    assert _column(d, STATUS) == ["Folder not found"] * 2

    _select(d, _ids(d).index(run))
    picker.picks = [new]
    d.undo_button.click()
    assert [c[0] for c in asked.calls] == ["Undo this sort?"]
    qtbot.waitUntil(lambda: not d.undoing())
    assert (new / "a.jpg").read_bytes() == b"a"
    assert {r.run_id: r.root for r in load_all_runs()} == {run: new, inner: new / "2025"}
    assert dict(zip(_ids(d), _column(d, STATUS))) == {run: "Undone", inner: "Done"}
    assert shown == []


def test_relocated_out_of_this_folder_is_still_undone(qtbot, make_history, tmp_path, asked,
                                                      shown, picker):
    home = tmp_path / "home"
    _write(home / "sub" / "a.jpg", b"a")
    run = _run(home / "sub", [("a.jpg", "d/a.jpg")])
    new = tmp_path / "elsewhere"
    os.rename(home / "sub", new)  # out of the Home folder
    d = make_history(home)
    d.filter_switch.click()
    assert _ids(d) == [run]
    _select(d, 0)
    picker.picks = [new]
    d.undo_button.click()
    assert [c[0] for c in asked.calls] == ["Undo this sort?"]
    qtbot.waitUntil(lambda: not d.undoing())
    assert (new / "a.jpg").read_bytes() == b"a"
    assert [(r.run_id, r.root, r.undone) for r in load_all_runs()] == [(run, new, True)]
    assert shown == []


def _blocking_pair(tmp_path) -> tuple[Path, Path, str, str]:
    """first (older) placed d/a.jpg; second (newer) moved it on, so it must
    be undone first. Then the folder is moved: both are not found."""
    old = tmp_path / "photos"
    _write(old / "a.jpg", b"a")
    _write(old / "b.jpg", b"b")  # stays at d/b.jpg: the folder is recognised by it
    first = _run(old, [("a.jpg", "d/a.jpg"), ("b.jpg", "d/b.jpg")])
    second = _run(old, [("d/a.jpg", "e/a.jpg")])
    new = tmp_path / "renamed"
    os.rename(old, new)
    return old, new, first, second


def _failing_record_writes(monkeypatch, fails):
    """journal._write_meta fails for a run record when fails(n, path) is
    true, n counting the run records written (the pending list is not one)."""
    real = journal._write_meta
    seen = []

    def flaky(path, meta):
        if path.name != "relocating.json":
            seen.append(path)
            if fails(len(seen), path):
                raise OSError("disk full")
        return real(path, meta)

    monkeypatch.setattr(journal, "_write_meta", flaky)


FAILED_RELOCATE = ("Couldn't point every run to the new folder: disk full\n"
                   "It is tried again before every undo, and nothing is undone until it works.")


@pytest.mark.parametrize("fail_at", [1, 2])
def test_retry_on_the_selected_run_after_a_part_way_relocate(
        qtbot, make_history, tmp_path, asked, shown, picker, monkeypatch, fail_at):
    old, new, first, second = _blocking_pair(tmp_path)
    _failing_record_writes(monkeypatch, lambda n, path: n == fail_at)  # once
    d = make_history(None)
    _select(d, _ids(d).index(first))
    picker.picks = [new]
    d.undo_button.click()
    assert shown == [FAILED_RELOCATE]
    assert asked.calls == []

    # The retry, on the run first selected: the pending pair was finished
    # (the page's refresh), so the newer run is seen to block it.
    assert {r.root for r in load_all_runs()} == {new}
    assert not (journal.appdata.runs_dir() / "relocating.json").exists()
    _select(d, _ids(d).index(first))
    assert not d.undo_button.isEnabled()
    assert d.blocked_label.text() == f"Undo the newer run(s) first: {second}"
    d.undo_button.click()
    assert asked.calls == [] and len(picker.calls) == 1
    assert (new / "e/a.jpg").read_bytes() == b"a"

    _select(d, _ids(d).index(second), _ids(d).index(first))  # in order: fine
    d.undo_button.click()
    qtbot.waitUntil(lambda: not d.undoing())
    assert (new / "a.jpg").read_bytes() == b"a"
    assert shown == [FAILED_RELOCATE]


@pytest.mark.parametrize("stuck", ["first", "second"])
def test_a_relocate_still_failing_refuses_every_undo(qtbot, make_history, tmp_path, asked,
                                                     shown, picker, monkeypatch, stuck):
    old, new, first, second = _blocking_pair(tmp_path)
    bad = {"first": first, "second": second}[stuck]
    _failing_record_writes(monkeypatch, lambda n, path: path.stem == bad)  # always
    d = make_history(None)
    _select(d, _ids(d).index(first))
    picker.picks = [new]
    d.undo_button.click()
    assert shown == [FAILED_RELOCATE]

    # Undo the run first selected again: it may point at the new folder now
    # (no picker then) while the newer run is still under the old path.
    picker.picks = [new]
    _select(d, _ids(d).index(first))
    d.undo_button.click()
    qtbot.waitUntil(lambda: len(shown) == 2)
    QThreadPool.globalInstance().waitForDone()
    assert shown[1].startswith(history_page.PENDING) or shown[1] == FAILED_RELOCATE
    assert (new / "e/a.jpg").read_bytes() == b"a"  # nothing undone out of order
    assert not any(r.undone for r in load_all_runs())
    assert not (new / "a.jpg").exists()


def test_a_pending_relocate_is_finished_after_a_restart(qtbot, make_history, tmp_path,
                                                        asked, shown, picker, monkeypatch):
    old, new, first, second = _blocking_pair(tmp_path)
    _failing_record_writes(monkeypatch, lambda n, path: path.stem == second)
    d = make_history(None)
    _select(d, _ids(d).index(first))
    picker.picks = [new]
    d.undo_button.click()
    assert shown == [FAILED_RELOCATE]
    d.close()  # the app is closed
    monkeypatch.setattr(journal, "_write_meta", _real_write_meta)  # the disk is fine again
    assert (journal.appdata.runs_dir() / "relocating.json").exists()  # on disk

    again = make_history(None)  # the app, opened again
    assert {r.root for r in load_all_runs()} == {new}
    assert not (journal.appdata.runs_dir() / "relocating.json").exists()
    assert _column(again, STATUS) == ["Done", "Done"]
    _select(again, _ids(again).index(first))
    assert not again.undo_button.isEnabled()  # the newer run blocks it, as it should
    assert again.blocked_label.text() == f"Undo the newer run(s) first: {second}"


def test_done_undo_refuses_while_a_relocate_is_pending(qtbot, make_window, tmp_path, errors,
                                                       monkeypatch):
    old, new, first, second = _blocking_pair(tmp_path)
    _failing_record_writes(monkeypatch, lambda n, path: path.stem == second)
    with pytest.raises(OSError):
        journal.relocate_root(old, new)
    w = make_window()
    w.root, w._run_id = new, first  # as if Done showed first's sort
    w._undo()
    qtbot.waitUntil(lambda: errors != [])
    assert errors[0].startswith(history_page.PENDING)
    assert (new / "e/a.jpg").read_bytes() == b"a"
    assert not any(r.undone for r in load_all_runs())


# ----- the keyboard, end to end ------------------------------------------------------------

def test_keyboard_only_history(qtbot, make_window, tmp_path, asked, shown):
    root, runs = _chain(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / "z.jpg", b"z")
    other = _run(elsewhere, [("z.jpg", "q/z.jpg")])
    w = make_window(size=(1024, 600))
    w.folder_edit.setText(str(root))
    _key(qtbot, Qt.Key.Key_Tab)  # from the tiles to the sidebar
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Return)
    page = w.history_page
    assert w.current_page() is page
    _key(qtbot, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is page.runs_table
    assert w.strip.text() == ("↑↓ choose · Space select · Ctrl+A all · Enter undo · "
                              "Del forget · F this folder · Esc sidebar · Tab/Shift+Tab area")
    assert _ids(page) == [other, *runs[::-1]]

    _key(qtbot, Qt.Key.Key_F)  # this folder only
    assert _ids(page) == runs[::-1]
    _key(qtbot, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(page.runs_table.selectionModel().selectedRows()) == 3
    _key(qtbot, Qt.Key.Key_Space)  # the focused row (the first) off
    assert len(page.runs_table.selectionModel().selectedRows()) == 2
    _key(qtbot, Qt.Key.Key_Return)
    assert asked.calls[-1][0] == "Undo 2 runs?"
    qtbot.waitUntil(lambda: not page.undoing())
    assert _column(page, STATUS) == ["Done", "Undone", "Undone"]
    assert (root / "a.jpg").is_file() and (root / "b.jpg").is_file()

    assert page.runs_table.currentIndex().row() == 1
    _key(qtbot, Qt.Key.Key_Up)  # to the first row, alone
    _key(qtbot, Qt.Key.Key_Delete)
    assert asked.calls[-1][:2] == ("Forget 1 run?", FORGET)
    assert _ids(page) == runs[1::-1]
    _key(qtbot, Qt.Key.Key_F)
    assert _ids(page) == [other, *runs[1::-1]]
    _key(qtbot, Qt.Key.Key_Escape)
    assert QApplication.focusWidget() is w.sidebar.items["history"]
    assert shown == []
