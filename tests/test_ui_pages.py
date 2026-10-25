"""The sidebar and its pages: History, People and Settings."""

import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QApplication, QDialog

from photosort.executor import execute
from photosort.journal import RunJournal
from photosort.modes import date_mode
from photosort.people import PeopleStore
from photosort.plan import Move, Plan
from photosort.settings import DEFAULT_CONTENT_CATEGORIES, Settings, load_settings
from photosort.ui import dialogs
from photosort.ui.category_editor import CategoryEditor
from photosort.ui.main_window import MainWindow

EXIF = "2025:02:20 10:00:00"


@pytest.fixture
def settings_path(tmp_path):
    return tmp_path / "config" / "settings.json"


@pytest.fixture
def store(tmp_path) -> PeopleStore:
    return PeopleStore(tmp_path / "people")  # never the owner's own


@pytest.fixture
def warned(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(dialogs, "warn", lambda parent, title, text: shown.append(text))
    return shown


@pytest.fixture
def asked(monkeypatch):
    """dialogs.ask answers asked.answer; asked.calls records (title, yes, no, default)."""

    class Asked:
        answer = False
        calls: list[tuple[str, str, str, str]] = []

    record = Asked()
    record.calls = []

    def ask(parent, title, text, yes, no, default="no"):
        record.calls.append((title, yes, no, default))
        return record.answer

    monkeypatch.setattr(dialogs, "ask", ask)
    return record


@pytest.fixture
def make_window(qtbot, settings_path, warned, store):
    windows = []

    def _make(settings: Settings | None = None) -> MainWindow:
        w = MainWindow(settings or Settings(), settings_path, people_store=store)
        qtbot.addWidget(w)
        w.resize(1280, 800)
        w.show()
        qtbot.waitExposed(w)
        w.activateWindow()
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


def _focus(qtbot, widget):
    widget.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is widget)


def _focused():
    return QApplication.focusWidget()


def _key(qtbot, key, modifier=Qt.KeyboardModifier.NoModifier):
    qtbot.keyClick(_focused(), key, modifier)


def _run(root: Path, moves) -> str:
    plan = Plan("date", [Move(s, d) for s, d in moves], [])
    journal = RunJournal.create(root, "date", False, len(moves))
    execute(root, plan, journal)
    return journal.run_id


# ----- the sidebar ------------------------------------------------------------

def test_the_sidebar_items_in_order(make_window):
    w = make_window()
    assert w.sidebar.objectName() == "sidebar"
    assert list(w.sidebar.items) == ["sort", "history", "people", "settings"]
    assert [b.text() for b in w.sidebar.items.values()] == [
        "Sort", "History", "People", "Settings"]
    assert w.sidebar.items["sort"].isChecked()


def test_up_down_and_enter_switch_pages(qtbot, make_window):
    w = make_window()
    items = w.sidebar.items
    _focus(qtbot, items["sort"])
    _key(qtbot, Qt.Key.Key_Down)
    assert _focused() is items["history"]
    assert w.current_page() is w.home  # moving alone switches nothing
    _key(qtbot, Qt.Key.Key_Return)
    assert w.current_page() is w.history_page
    assert items["history"].isChecked() and not items["sort"].isChecked()
    assert _focused() is items["history"]  # focus stays in the sidebar
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Down)  # the last item: stays
    assert _focused() is items["settings"]
    _key(qtbot, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    assert w.current_page() is w.settings_page
    _key(qtbot, Qt.Key.Key_Up)
    _key(qtbot, Qt.Key.Key_Return)
    assert w.current_page() is w.people_page
    for _ in range(3):
        _key(qtbot, Qt.Key.Key_Up)
    assert _focused() is items["sort"]
    _key(qtbot, Qt.Key.Key_Return)
    assert w.current_page() is w.home


def test_a_click_on_an_item_switches_pages(make_window):
    w = make_window()
    w.sidebar.items["people"].click()
    assert w.current_page() is w.people_page
    w.sidebar.items["sort"].click()
    assert w.current_page() is w.home


def test_tab_leaves_the_sidebar(qtbot, make_window):
    w = make_window()
    _focus(qtbot, w.sidebar.items["sort"])
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Tab)
    assert not w.sidebar.isAncestorOf(_focused())


def test_the_sidebar_strip(qtbot, make_window):
    w = make_window()
    _focus(qtbot, w.sidebar.items["sort"])
    assert w.strip.text() == "↑↓ move · Enter open · Tab leave"


def test_the_sidebar_is_locked_during_a_sort(qtbot, make_window, monkeypatch, tmp_path,
                                            make_jpeg):
    release = threading.Event()
    real = date_mode.prepare_date_plan

    def slow(root, include_subfolders, progress=None, cancel=None):
        assert release.wait(10)
        return real(root, include_subfolders, progress=progress, cancel=cancel)

    monkeypatch.setattr(date_mode, "prepare_date_plan", slow)
    photos = tmp_path / "photos"
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.mode_buttons["date"].click()
    assert w.current_page() is w.progress_page
    items = list(w.sidebar.items.values())
    assert not any(item.isEnabled() for item in items)
    assert not any(item.focusPolicy() & Qt.FocusPolicy.TabFocus and item.isEnabled()
                   for item in items)
    w.sidebar.items["history"].click()  # a click does nothing
    assert w.current_page() is w.progress_page
    release.set()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert not any(item.isEnabled() for item in items)
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert all(item.isEnabled() for item in items)
    w.home_button.click()
    assert w.current_page() is w.home
    assert all(item.isEnabled() for item in items)


def test_cancel_unlocks_the_sidebar(qtbot, make_window, monkeypatch, tmp_path):
    def slow(root, include_subfolders, progress=None, cancel=None):
        cancel.wait(10)
        return None

    monkeypatch.setattr(date_mode, "prepare_date_plan", slow)
    w = make_window()
    w.folder_edit.setText(str(tmp_path))
    w.mode_buttons["date"].click()
    assert not w.sidebar.items["sort"].isEnabled()
    w.cancel_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    assert w.sidebar.items["sort"].isEnabled()


# ----- History ----------------------------------------------------------------

def test_history_without_a_folder_says_choose_one(make_window):
    w = make_window()
    w.sidebar.items["history"].click()
    assert "Choose a folder first" in w.history_page.intro_label.text()
    assert w.history_page.runs_table.rowCount() == 0
    assert not w.history_page.undo_button.isEnabled()


def test_history_undo_asks_first(qtbot, make_window, tmp_path, asked):
    root = tmp_path / "photos"
    (root / "a.jpg").parent.mkdir()
    (root / "a.jpg").write_bytes(b"a")
    _run(root, [("a.jpg", "d/a.jpg")])
    w = make_window()
    w.folder_edit.setText(str(root))
    w.sidebar.items["history"].click()
    page = w.history_page
    assert str(root) in page.intro_label.text()
    assert page.runs_table.rowCount() == 1
    _focus(qtbot, page.runs_table)
    assert w.strip.text() == "↑↓ choose · Enter undo · Esc sidebar"

    asked.answer = False
    _key(qtbot, Qt.Key.Key_Return)
    assert asked.calls == [("Undo this sort?", "Undo", "Cancel", "no")]
    qtbot.wait(50)
    assert page.runs_table.item(0, 3).text() == "Done"
    assert (root / "d" / "a.jpg").is_file()

    asked.answer = True
    page.undo_button.click()
    qtbot.waitUntil(lambda: page.runs_table.item(0, 3).text() == "Undone")
    assert (root / "a.jpg").read_bytes() == b"a"
    assert page.result_label.text() == "Undo complete: 1 files put back."
    assert len(asked.calls) == 2


def test_history_locks_the_sidebar_while_it_undoes(qtbot, make_window, tmp_path, asked,
                                                   monkeypatch):
    from photosort.ui import history_page

    root = tmp_path / "photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"a")
    _run(root, [("a.jpg", "d/a.jpg")])
    release = threading.Event()
    real = history_page.undo_run

    def slow(*args, **kwargs):
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(history_page, "undo_run", slow)
    asked.answer = True
    w = make_window()
    w.folder_edit.setText(str(root))
    w.sidebar.items["history"].click()
    w.history_page.undo_button.click()
    assert not w.sidebar.items["sort"].isEnabled()
    release.set()
    qtbot.waitUntil(lambda: w.sidebar.items["sort"].isEnabled())
    assert w.history_page.runs_table.item(0, 3).text() == "Undone"


def test_esc_on_a_page_goes_back_to_the_sidebar(qtbot, make_window):
    w = make_window()
    w.sidebar.items["settings"].click()
    _focus(qtbot, w.settings_page.gap_spin)
    _key(qtbot, Qt.Key.Key_Escape)
    assert _focused() is w.sidebar.items["settings"]


# ----- People -----------------------------------------------------------------

def _remember(store, name, seed):
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return store.add(name, v / np.linalg.norm(v), Image.new("RGB", (40, 40), "blue"))


def test_people_delete_asks_then_forgets(qtbot, make_window, store, asked):
    _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    w = make_window()
    w.sidebar.items["people"].click()
    page = w.people_page
    assert page.people_list.count() == 2
    page.people_list.setCurrentRow(1)
    _focus(qtbot, page.people_list)
    assert w.strip.text() == "↑↓ choose · Delete forget · Esc sidebar"

    asked.answer = False
    _key(qtbot, Qt.Key.Key_Delete)
    assert asked.calls == [("Forget person", "Forget", "Keep", "no")]
    assert [p.name for p in store.people()] == ["Atanu", "Zoe"]

    asked.answer = True
    _key(qtbot, Qt.Key.Key_Delete)
    assert [p.name for p in store.people()] == ["Atanu"]
    assert page.people_list.count() == 1


def test_people_with_nobody_has_no_forget_key(qtbot, make_window):
    w = make_window()
    w.sidebar.items["people"].click()
    _focus(qtbot, w.people_page.people_list)
    assert w.strip.text() == "↑↓ choose · Esc sidebar"


# ----- Settings ---------------------------------------------------------------

def test_settings_saves_each_change_at_once(qtbot, make_window, settings_path, warned):
    w = make_window(Settings(use_gpu=True))
    w.sidebar.items["settings"].click()
    page = w.settings_page
    page.gap_spin.setValue(12)
    assert load_settings(settings_path).event_gap_hours == 12
    assert w.settings.event_gap_hours == 12
    page.min_files_spin.setValue(25)
    assert load_settings(settings_path).event_min_files == 25
    page.gpu_check.setChecked(False)
    assert load_settings(settings_path).use_gpu is False
    assert w.settings.use_gpu is False
    assert warned == []


def test_settings_gap_by_keyboard(qtbot, make_window, settings_path):
    w = make_window()
    w.sidebar.items["settings"].click()
    spin = w.settings_page.gap_spin
    _focus(qtbot, spin)
    assert w.strip.text().startswith("↑↓ change")
    _key(qtbot, Qt.Key.Key_Up)
    assert load_settings(settings_path).event_gap_hours == 7


def test_settings_categories_open_the_editor_and_save(qtbot, make_window, settings_path,
                                                      monkeypatch):
    def edit(editor):
        editor.list_widget.setCurrentRow(0)
        editor.remove_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CategoryEditor, "exec", edit)
    w = make_window()
    w.sidebar.items["settings"].click()
    page = w.settings_page
    assert page.categories_button.text() == "Content categories…"
    _focus(qtbot, page.categories_button)
    _key(qtbot, Qt.Key.Key_Return)
    assert w.settings.content_categories == list(DEFAULT_CONTENT_CATEGORIES[1:])
    assert load_settings(settings_path).content_categories == list(
        DEFAULT_CONTENT_CATEGORIES[1:])


def test_settings_spin_text_is_not_against_the_ring(make_window):
    w = make_window()
    margins = w.settings_page.gap_spin.lineEdit().textMargins()
    assert margins.left() >= 4
