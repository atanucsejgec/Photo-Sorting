"""Home: the folder, the subfolders switch, the mode tiles and their keys."""

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt, QThreadPool
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication

from photosort.modes import date_mode
from photosort.settings import Settings, load_settings
from photosort.ui import dialogs, home_page
from photosort.ui.home_page import TILE_MIN_WIDTH, HomePage
from photosort.ui.main_window import MainWindow

EXIF = "2025:02:20 10:00:00"
TILES = [
    ("date", "Date", "Year / month"),
    ("face", "Face", "Per person"),
    ("duplicates", "Duplicates", "Exact copies"),
    ("repeated", "Repeated shots", "Bursts, keep best"),
    ("content", "Content", "Food, pets, nature…"),
    ("notes", "Notes & Pages", "Receipts, documents"),
    ("bad", "Bad photos", "Blurry, dark"),
    ("events", "Events", "Trips and days"),
    ("location", "Location", "Country / city"),
    ("camera", "Camera", "Per phone"),
    ("type", "Type / source", "WhatsApp, screenshots"),
]
HOME_KEYS = "←↑↓→ choose · Enter start · F folder · S subfolders · Tab sidebar"
FIELD_KEYS = "Enter use this folder · Esc leave"


@pytest.fixture
def settings_path(tmp_path):
    return tmp_path / "config" / "settings.json"


@pytest.fixture
def warned(monkeypatch):
    """dialogs.warn records its text instead of opening."""
    shown: list[str] = []
    monkeypatch.setattr(dialogs, "warn", lambda parent, title, text: shown.append(text))
    return shown


@pytest.fixture
def make_window(qtbot, settings_path, warned, tmp_path):
    from photosort.people import PeopleStore

    windows = []

    def _make(settings: Settings | None = None, size=(1280, 800)) -> MainWindow:
        w = MainWindow(settings or Settings(), settings_path,
                       people_store=PeopleStore(tmp_path / "people"))
        qtbot.addWidget(w)
        w.resize(*size)
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


def _show(qtbot, w):
    w.show()
    qtbot.waitExposed(w)
    w.activateWindow()
    return w


def _focus(qtbot, widget):
    widget.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is widget)


def _focused():
    return QApplication.focusWidget()


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


# ----- the tiles -------------------------------------------------------------

def test_tiles_in_order_with_their_hints(make_window):
    w = make_window()
    assert [(key, tile.name, tile.hint) for key, tile in w.mode_buttons.items()] == TILES
    for tile in w.mode_buttons.values():
        assert tile.objectName() == "modeTile"
        assert tile.isEnabled()
        assert QKeySequence.mnemonic(tile.text()).isEmpty()  # & shown as &
    notes = w.mode_buttons["notes"]
    assert notes.name_label.text() == "Notes & Pages"
    assert not hasattr(w, "start_button")


def test_tiles_sit_in_four_columns_in_order(qtbot, make_window):
    w = _show(qtbot, make_window())
    assert w.home.columns() == 4
    tiles = list(w.mode_buttons.values())
    for n, tile in enumerate(tiles):
        assert w.home.tile_layout.getItemPosition(w.home.tile_layout.indexOf(tile))[:2] == (
            n // 4, n % 4)
    rows = sorted({t.geometry().top() for t in tiles})
    assert len(rows) == 3


def test_focus_starts_on_date(qtbot, make_window):
    w = _show(qtbot, make_window())
    qtbot.waitUntil(lambda: _focused() is w.mode_buttons["date"])


def test_focus_starts_on_the_last_mode(qtbot, make_window):
    w = _show(qtbot, make_window(Settings(last_mode="events")))
    qtbot.waitUntil(lambda: _focused() is w.mode_buttons["events"])


def test_arrows_follow_four_columns(qtbot, make_window):
    w = _show(qtbot, make_window())
    date = w.mode_buttons["date"]
    _focus(qtbot, date)
    for key in (Qt.Key.Key_Right, Qt.Key.Key_Right, Qt.Key.Key_Down):
        qtbot.keyClick(_focused(), key)
    assert _focused() is w.mode_buttons["bad"]
    qtbot.keyClick(_focused(), Qt.Key.Key_Up)
    qtbot.keyClick(_focused(), Qt.Key.Key_Left)
    assert _focused() is w.mode_buttons["face"]
    qtbot.keyClick(_focused(), Qt.Key.Key_Up)  # the top row: stays
    assert _focused() is w.mode_buttons["face"]


def test_down_into_a_short_last_row_lands_on_its_last_tile(qtbot, make_window):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.mode_buttons["events"])  # index 7: below it is nothing
    qtbot.keyClick(_focused(), Qt.Key.Key_Down)
    assert _focused() is w.mode_buttons["type"]


def test_arrows_follow_the_reflowed_grid(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.resize(1100, 600)
    page.show()
    qtbot.waitExposed(page)
    page.activateWindow()
    qtbot.waitUntil(lambda: page.columns() == 4)
    page.resize(2 * TILE_MIN_WIDTH + 120, 700)
    qtbot.waitUntil(lambda: page.columns() == 2)
    tiles = list(page.mode_buttons.values())
    qtbot.waitUntil(lambda: tiles[2].geometry().top() > tiles[0].geometry().top())
    assert tiles[1].geometry().top() == tiles[0].geometry().top()  # Duplicates: row 2
    _focus(qtbot, page.mode_buttons["date"])
    qtbot.keyClick(_focused(), Qt.Key.Key_Down)
    assert _focused() is page.mode_buttons["duplicates"]


def test_enter_on_a_tile_starts_its_mode_and_remembers_it(qtbot, make_window, photos,
                                                          settings_path, warned):
    w = _show(qtbot, make_window())
    w.folder_edit.setText(str(photos))
    camera = w.mode_buttons["camera"]
    _focus(qtbot, camera)
    qtbot.keyClick(camera, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.mode == "camera"
    assert load_settings(settings_path).last_mode == "camera"
    assert warned == []


def test_a_click_on_a_tile_starts_its_mode(qtbot, make_window, photos, make_jpeg):
    make_jpeg(photos / "a.jpg", EXIF)
    w = make_window()
    w.folder_edit.setText(str(photos))
    w.mode_buttons["date"].click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert "1 files → 1 month folders" in w.summary_label.text()


def test_a_held_enter_starts_nothing(qtbot, make_window, photos):
    w = _show(qtbot, make_window())
    w.folder_edit.setText(str(photos))
    _focus(qtbot, w.mode_buttons["date"])
    held = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier,
                     "\r", True)  # a repeat of a held Enter
    QApplication.sendEvent(w.mode_buttons["date"], held)
    qtbot.wait(50)
    assert w.current_page() is w.home


def test_a_tile_without_a_folder_warns(qtbot, make_window, warned, photos, settings_path):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.mode_buttons["date"])
    qtbot.keyClick(w.mode_buttons["date"], Qt.Key.Key_Return)
    assert warned == ["Choose a folder first."]
    assert w.current_page() is w.home
    w.folder_edit.setText(str(photos / "missing"))
    w.mode_buttons["date"].click()
    assert warned[-1] == "This folder doesn't exist any more."
    assert w.current_page() is w.home
    assert not settings_path.exists()  # nothing saved, no last mode


# ----- the folder field and the letter keys (Review Focus 4) ------------------

def test_typing_f_and_s_in_the_field_only_types(qtbot, make_window, monkeypatch):
    picked = []
    monkeypatch.setattr(home_page.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: picked.append(a) or "")
    w = _show(qtbot, make_window())
    _focus(qtbot, w.folder_edit)
    qtbot.keyClicks(w.folder_edit, "fs")
    qtbot.keyClicks(w.folder_edit, "FS")
    assert w.folder_edit.text() == "fsFS"
    assert not w.home.subfolders_switch.isChecked()
    assert picked == []


def test_a_path_with_spaces_types_as_written(qtbot, make_window):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.folder_edit)
    qtbot.keyClicks(w.folder_edit, "/tmp/My Photos ")
    assert w.folder_edit.text() == "/tmp/My Photos "


def test_f_outside_the_field_opens_the_picker(qtbot, make_window, monkeypatch, photos):
    picked = []

    def pick(parent, title, start):
        picked.append(title)
        return str(photos)

    monkeypatch.setattr(home_page.QFileDialog, "getExistingDirectory", pick)
    w = _show(qtbot, make_window())
    _focus(qtbot, w.mode_buttons["date"])
    qtbot.keyClick(_focused(), Qt.Key.Key_F)
    assert picked == ["Choose a folder"]
    assert w.folder_edit.text() == str(photos)


def test_browse_opens_the_picker(qtbot, make_window, monkeypatch, photos):
    monkeypatch.setattr(home_page.QFileDialog, "getExistingDirectory",
                        lambda *a: str(photos))
    w = make_window()
    w.browse_button.click()
    assert w.folder_edit.text() == str(photos)


def test_s_switches_subfolders_and_saves(qtbot, make_window, settings_path):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.mode_buttons["face"])
    qtbot.keyClick(_focused(), Qt.Key.Key_S)
    assert w.home.subfolders_switch.isChecked()
    assert w.subfolders_check is w.home.subfolders_switch
    assert load_settings(settings_path).include_subfolders is True
    qtbot.keyClick(_focused(), Qt.Key.Key_S)
    assert not w.home.subfolders_switch.isChecked()


def test_a_pasted_path_is_trimmed(qtbot, make_window):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.folder_edit)
    QApplication.clipboard().setText("/tmp/x \n")
    qtbot.keyClick(w.folder_edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert w.folder_edit.text() == "/tmp/x"
    w.folder_edit.clear()
    QApplication.clipboard().setText("  /tmp/y  ")
    qtbot.keyClick(w.folder_edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert w.folder_edit.text() == "/tmp/y"


def test_enter_in_the_field_uses_the_folder_and_goes_to_the_tiles(qtbot, make_window,
                                                                   photos):
    w = _show(qtbot, make_window(Settings(last_mode="bad")))
    _focus(qtbot, w.folder_edit)
    qtbot.keyClicks(w.folder_edit, f"{photos}  ")
    qtbot.keyClick(w.folder_edit, Qt.Key.Key_Return)
    assert w.folder_edit.text() == str(photos)
    assert _focused() is w.mode_buttons["bad"]
    assert w.current_page() is w.home  # Enter in the field starts nothing


def test_esc_leaves_the_field(qtbot, make_window):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.folder_edit)
    qtbot.keyClick(w.folder_edit, Qt.Key.Key_Escape)
    assert _focused() is w.mode_buttons["date"]


# ----- the file count ---------------------------------------------------------

def test_the_count_is_photos_and_videos_and_follows_the_switch(qtbot, make_window, photos,
                                                              make_jpeg):
    make_jpeg(photos / "a.jpg")
    make_jpeg(photos / "b.jpg")
    (photos / "clip.mp4").write_bytes(b"not really")
    (photos / "notes.txt").write_text("not media")
    make_jpeg(photos / "sub" / "c.jpg")
    w = make_window()
    w.folder_edit.setText(str(photos))
    qtbot.waitUntil(lambda: w.home.count_label.text() == "3 files")
    w.home.subfolders_switch.setChecked(True)
    qtbot.waitUntil(lambda: w.home.count_label.text() == "4 files")
    w.folder_edit.setText(str(photos / "sub"))
    qtbot.waitUntil(lambda: w.home.count_label.text() == "1 file")
    w.folder_edit.setText(str(photos / "missing"))
    qtbot.waitUntil(lambda: w.home.count_label.text() == "")


def test_a_stale_count_is_dropped(qtbot, monkeypatch, tmp_path):
    release = threading.Event()
    calls = []

    def slow_count(root, include_subfolders, progress=None, cancel=None):
        calls.append(Path(root).name)
        if Path(root).name == "old":
            release.wait(10)
            return 99
        return 7

    monkeypatch.setattr(home_page, "count_media", slow_count)
    for name in ("old", "new"):
        (tmp_path / name).mkdir()
    page = HomePage()
    qtbot.addWidget(page)
    page.folder_edit.setText(str(tmp_path / "old"))
    qtbot.waitUntil(lambda: calls == ["old"])
    page.folder_edit.setText(str(tmp_path / "new"))
    qtbot.wait(page.COUNT_DELAY_MS + 50)
    release.set()
    qtbot.waitUntil(lambda: page.count_label.text() == "7 files")
    qtbot.wait(50)
    assert page.count_label.text() == "7 files"
    assert calls == ["old", "new"]  # one count at a time
    QThreadPool.globalInstance().waitForDone()


def test_the_count_never_blocks_the_window(qtbot, monkeypatch, photos):
    release = threading.Event()

    def slow_count(root, include_subfolders, progress=None, cancel=None):
        release.wait(10)
        return 1

    monkeypatch.setattr(home_page, "count_media", slow_count)
    page = HomePage()
    qtbot.addWidget(page)
    page.folder_edit.setText(str(photos))
    qtbot.waitUntil(lambda: page.count_label.text() == "Counting…")
    page.folder_edit.setText("typed while it counts")  # the GUI thread is free
    release.set()
    QThreadPool.globalInstance().waitForDone()


def test_a_cancelled_count_stops(tmp_path):
    for n in range(3):
        (tmp_path / f"d{n}").mkdir()
        (tmp_path / f"d{n}" / "a.jpg").write_bytes(b"")
    cancel = threading.Event()
    cancel.set()
    assert home_page.count_media(tmp_path, True, cancel=cancel) is None
    assert home_page.count_media(tmp_path, True) == 3


# ----- the strip and Tab ------------------------------------------------------

def test_home_strip_lists_exactly_its_keys(qtbot, make_window):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.mode_buttons["date"])
    assert w.strip.text() == HOME_KEYS
    _focus(qtbot, w.folder_edit)
    assert w.strip.text() == FIELD_KEYS
    _focus(qtbot, w.mode_buttons["camera"])
    assert w.strip.text() == HOME_KEYS


def test_tab_goes_from_the_tiles_to_the_sidebar(qtbot, make_window):
    w = _show(qtbot, make_window())
    _focus(qtbot, w.mode_buttons["date"])
    qtbot.keyClick(_focused(), Qt.Key.Key_Right)
    qtbot.keyClick(_focused(), Qt.Key.Key_Tab)
    assert _focused() is w.sidebar.items["sort"]
    qtbot.keyClick(_focused(), Qt.Key.Key_Backtab)
    assert _focused() is w.mode_buttons["face"]  # back to the tile it left


def test_a_small_window_keeps_the_grid_and_strip_in_view(qtbot, make_window):
    w = _show(qtbot, make_window(size=(1024, 600)))
    qtbot.waitUntil(lambda: w.home.columns() == 4)
    _focus(qtbot, w.mode_buttons["date"])
    last = w.mode_buttons["type"]
    bottom = last.mapTo(w, last.rect().bottomRight()).y()
    assert bottom < w.strip.mapTo(w, w.strip.rect().topLeft()).y()
    assert w.strip.mapTo(w, w.strip.rect().bottomLeft()).y() <= w.height()
    assert w.strip.text() == HOME_KEYS
