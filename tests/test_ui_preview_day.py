"""Preview's day view in the window, driven by keys: G opens the grid of the
shown file's day (its modified date), every file ticked; Space and A tick;
N and M move the ticked ones as one group, the unticked staying unticked;
an emptied day opens the next; Ctrl+Z puts the whole group back; Enter
opens one file alone and Esc comes back; PgUp/PgDn change the day; a move
that fails comes back ticked; 1024 × 600; a big day opens at once.

The files are tmp JPEGs (and a text file) whose modified times are set
with os.utime. The helpers and fixtures are test_ui_preview's (its autouse
shown_in_folder stub too, so no test opens a real file manager).
"""

import os
import time
from datetime import datetime
from pathlib import Path

import pytest
from keys import (
    _focused,
    _in_dialog,
    _key,
    _records,
    errors,  # noqa: F401  (fixtures, used by name)
    make_window,  # noqa: F401
    models_present,  # noqa: F401
    settings_path,  # noqa: F401
)
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QPushButton
from test_ui_preview import (
    CTRL,
    IMAGE_KEYS,
    PAGE_KEYS,
    WAIT_MS,
    _idle,
    _jpeg,
    _open,
    _settings,
    _showing,
    _slow_moves,
    _speed_back_to_1,  # noqa: F401  (autouse fixture)
    root,  # noqa: F401  (fixture)
    shown_in_folder,  # noqa: F401  (autouse fixture)
)

from photosort.modes.preview import NOT_NEEDED
from photosort.ui import preview_viewers as pv
from photosort.ui.review_page import LOADING

GRID_KEYS = ("←↑↓→ move · Space tick · A tick all/none · Enter open alone · "
             "PgUp/PgDn day before/after · N not needed · M move to… · L edit folders · "
             "E show in folder · Ctrl+Z put back · G/Esc one at a time")


def _at(path: Path, when: datetime) -> Path:
    """Give path the modified time when (local time)."""
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def _photo(root: Path, name: str, when: datetime, colour: str = "red") -> Path:
    return _at(_jpeg(root / name, colour), when)


FRI = datetime(2025, 2, 28, 10, 0)  # Fri 28 Feb 2025
SAT = datetime(2025, 3, 1, 12, 0)  # Sat 1 Mar 2025
SUN = datetime(2025, 3, 2, 12, 0)  # Sun 2 Mar 2025


@pytest.fixture
def one_day(root) -> Path:
    """a.jpg, b.jpg, c.jpg on Fri 28 Feb (in that order of time); d.jpg on Sat 1 Mar."""
    _photo(root, "a.jpg", FRI.replace(hour=9))
    _photo(root, "b.jpg", FRI.replace(hour=10), "green")
    _photo(root, "c.jpg", FRI.replace(hour=11), "blue")
    _photo(root, "d.jpg", SAT)
    return root


def _grid(qtbot, page):
    """G: the grid of the shown file's day, once it shows."""
    _key(qtbot, Qt.Key.Key_G)
    qtbot.waitUntil(lambda: page.view() == "day", timeout=WAIT_MS)
    qtbot.waitUntil(lambda: _focused() is not None and page.day_grid.isAncestorOf(_focused()),
                    timeout=WAIT_MS)
    return page.day_grid


# ----- opening a day and ticking -------------------------------------------------------------

def test_g_opens_the_day_all_ticked(qtbot, make_window, root, errors):
    _photo(root, "a.jpg", FRI.replace(hour=10))
    _photo(root, "b.jpg", FRI.replace(hour=11))
    _photo(root, "c.jpg", FRI.replace(hour=9))
    _photo(root, "d.jpg", SAT)
    (root / "notes.txt").write_text("hi")
    _at(root / "notes.txt", FRI.replace(hour=12))
    w = make_window()
    page = _open(qtbot, w, root)
    _showing(qtbot, page, "1 of 5 · a.jpg")
    assert page.view() == "one"
    assert w.strip.text() == f"{IMAGE_KEYS} · {PAGE_KEYS}"
    grid = _grid(qtbot, page)
    assert grid.rels() == ["c.jpg", "a.jpg", "b.jpg", "notes.txt"]  # by modified time
    assert grid.ticked() == ["c.jpg", "a.jpg", "b.jpg", "notes.txt"]
    assert grid.focused() == "a.jpg"  # the file shown
    assert page.day_text() == "Fri 28 Feb 2025 · day 1 of 2 · 4 files · 4 ticked"
    assert page.text() == page.day_text()
    assert w.strip.text() == GRID_KEYS
    assert _records(_focused(), page) == w.strip.text()
    notes = grid.card("notes.txt")
    assert notes.thumb_label.text() == "Text"  # not a photo: its kind, and its name
    assert notes.name() == "notes.txt"
    qtbot.waitUntil(lambda: not grid.card("a.jpg").thumb_label.pixmap().isNull(),
                    timeout=WAIT_MS)
    assert errors == []


def test_space_and_a_tick(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    assert grid.focused() == "a.jpg"
    _key(qtbot, Qt.Key.Key_Space)
    assert grid.ticked() == ["b.jpg", "c.jpg"]
    assert page.day_text() == "Fri 28 Feb 2025 · day 1 of 2 · 3 files · 2 ticked"
    _key(qtbot, Qt.Key.Key_A)  # not all ticked: all
    assert grid.ticked() == ["a.jpg", "b.jpg", "c.jpg"]
    _key(qtbot, Qt.Key.Key_A)  # all ticked: none
    assert grid.ticked() == []
    assert page.day_text() == "Fri 28 Feb 2025 · day 1 of 2 · 3 files · 0 ticked"
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)
    assert grid.focused() == "b.jpg"
    assert grid.ticked() == ["b.jpg"]
    grid.card("c.jpg").tick.click()  # a click on the tick
    assert grid.ticked() == ["b.jpg", "c.jpg"]
    assert errors == []


# ----- moving the ticked ---------------------------------------------------------------------

def test_n_moves_the_ticked_and_keeps_the_unticked(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_Space)  # a unticked
    _key(qtbot, Qt.Key.Key_N)
    assert grid.rels() == ["a.jpg"]  # at once
    assert grid.ticked() == []  # it stays unticked
    _idle(qtbot, page)
    assert (one_day / NOT_NEEDED / "b.jpg").is_file()
    assert (one_day / NOT_NEEDED / "c.jpg").is_file()
    assert (one_day / "a.jpg").is_file()
    assert page.view() == "day"
    assert grid.rels() == ["a.jpg"]
    assert page.day_text() == "Fri 28 Feb 2025 · day 1 of 2 · 1 file · 0 ticked"
    assert [src for src, _ in page.session.moved] == ["b.jpg", "c.jpg"]  # each journalled
    assert page.files == ["a.jpg", "d.jpg"]
    assert grid.focused() == "a.jpg"
    assert errors == []


def test_m_digit_moves_the_ticked_to_a_slot(qtbot, make_window, one_day, errors):
    (one_day / "Trips").mkdir()
    w = make_window(_settings(one_day / "Trips"))
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)  # b unticked
    _key(qtbot, Qt.Key.Key_M)
    assert page.overlay.isVisible()
    _key(qtbot, Qt.Key.Key_1)
    assert not page.overlay.isVisible()
    assert grid.rels() == ["b.jpg"]
    _idle(qtbot, page)
    assert (one_day / "Trips" / "a.jpg").is_file()
    assert (one_day / "Trips" / "c.jpg").is_file()
    assert page.session.counts() == (0, 2)
    assert page.files == ["b.jpg", "d.jpg"]
    assert page.day_grid.isAncestorOf(_focused())
    assert errors == []


def test_nothing_ticked_says_so(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_A)  # all were ticked: none now
    _key(qtbot, Qt.Key.Key_N)
    assert page.message() == "Nothing ticked"
    _key(qtbot, Qt.Key.Key_M)
    assert page.message() == "Nothing ticked"
    assert not page.overlay.isVisible()
    assert grid.rels() == ["a.jpg", "b.jpg", "c.jpg"]
    _idle(qtbot, page)
    assert page.session.moved == []
    assert errors == []


def test_empty_day_opens_the_next_day(qtbot, make_window, root, errors):
    _photo(root, "a.jpg", SAT)  # shown first
    _photo(root, "b.jpg", FRI)
    _photo(root, "c.jpg", SUN)
    w = make_window()
    page = _open(qtbot, w, root)
    grid = _grid(qtbot, page)
    assert page.day_text() == "Sat 1 Mar 2025 · day 2 of 3 · 1 file · 1 ticked"
    _key(qtbot, Qt.Key.Key_N)
    qtbot.waitUntil(lambda: grid.rels() == ["c.jpg"], timeout=WAIT_MS)  # the day after
    assert page.day_text() == "Sun 2 Mar 2025 · day 2 of 2 · 1 file · 1 ticked"
    assert grid.focused() == "c.jpg"
    qtbot.waitUntil(lambda: page.day_grid.isAncestorOf(_focused()), timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_N)
    qtbot.waitUntil(lambda: grid.rels() == ["b.jpg"], timeout=WAIT_MS)  # none after: before
    assert page.day_text() == "Fri 28 Feb 2025 · day 1 of 1 · 1 file · 1 ticked"
    _idle(qtbot, page)
    assert errors == []


def test_last_day_emptied_ends_the_preview(qtbot, make_window, one_day, errors):
    (one_day / "d.jpg").unlink()
    w = make_window()
    page = _open(qtbot, w, one_day)
    _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_N)
    qtbot.waitUntil(lambda: w.current_page() is w.done_page, timeout=WAIT_MS)
    assert w.done_label.text() == ("That was the last file.\n"
                                   "Moved 3 files: 3 to Not needed, 0 to folders")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        assert (one_day / NOT_NEEDED / name).is_file()
    assert errors == []


# ----- putting back --------------------------------------------------------------------------

def test_ctrl_z_puts_back_the_whole_group(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_N)  # a, b, c: Sat opens
    qtbot.waitUntil(lambda: grid.rels() == ["d.jpg"], timeout=WAIT_MS)
    qtbot.waitUntil(lambda: page.day_grid.isAncestorOf(_focused()), timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Z, CTRL)  # in the grid: the three, their day shown
    qtbot.waitUntil(lambda: grid.rels() == ["a.jpg", "b.jpg", "c.jpg"], timeout=WAIT_MS)
    assert grid.ticked() == ["a.jpg", "b.jpg", "c.jpg"]
    assert page.day_text() == "Fri 28 Feb 2025 · day 1 of 2 · 3 files · 3 ticked"
    assert sorted(p.name for p in one_day.glob("*.jpg")) == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
    _idle(qtbot, page)
    assert page.session.moved == []

    qtbot.waitUntil(lambda: page.day_grid.isAncestorOf(_focused()), timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_N)  # again; then one at a time, on Sat's d
    qtbot.waitUntil(lambda: grid.rels() == ["d.jpg"], timeout=WAIT_MS)
    qtbot.waitUntil(lambda: page.day_grid.isAncestorOf(_focused()), timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_G)
    qtbot.waitUntil(lambda: page.view() == "one", timeout=WAIT_MS)
    _showing(qtbot, page, "1 of 1 · d.jpg")
    _key(qtbot, Qt.Key.Key_Z, CTRL)  # in the one view: the whole group too
    _showing(qtbot, page, "1 of 4 · a.jpg")
    assert page.view() == "one"
    _idle(qtbot, page)
    assert page.files == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
    assert page.session.counts() == (0, 0)
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    qtbot.waitUntil(lambda: page.message() == "Nothing to put back", timeout=WAIT_MS)
    assert errors == []


def test_ctrl_z_after_a_single_move_puts_back_one(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    _key(qtbot, Qt.Key.Key_N)  # a
    _key(qtbot, Qt.Key.Key_N)  # b
    _showing(qtbot, page, "1 of 2 · c.jpg")
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    _showing(qtbot, page, "1 of 3 · b.jpg")
    _idle(qtbot, page)
    assert (one_day / NOT_NEEDED / "a.jpg").is_file()
    assert errors == []


# ----- one file alone ------------------------------------------------------------------------

@pytest.mark.real_dialogs
def test_enter_opens_alone_and_esc_comes_back(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)  # b unticked
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Return)  # c alone
    qtbot.waitUntil(lambda: page.view() == "one", timeout=WAIT_MS)
    _showing(qtbot, page, "3 of 4 · c.jpg")
    qtbot.waitUntil(lambda: _focused() is page._box, timeout=WAIT_MS)
    assert w.strip.text() == (f"{IMAGE_KEYS} · ←→ file · N not needed · M move to… · "
                              "L edit folders · E show in folder · Ctrl+Z put back · "
                              "G/Esc back to the day")
    viewer = page.viewer()
    qtbot.waitUntil(viewer.is_loaded, timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Plus)
    assert viewer.zoom() > 1.0
    _key(qtbot, Qt.Key.Key_Escape)  # back to the grid, not the Leave question
    qtbot.waitUntil(lambda: page.view() == "day", timeout=WAIT_MS)
    assert grid.rels() == ["a.jpg", "b.jpg", "c.jpg"]
    assert grid.ticked() == ["a.jpg", "c.jpg"]  # the same ticks
    assert grid.focused() == "c.jpg"
    qtbot.waitUntil(lambda: page.day_grid.isAncestorOf(_focused()), timeout=WAIT_MS)
    grid.card("a.jpg").thumb_label.double_clicked.emit()  # a double-click opens alone too
    qtbot.waitUntil(lambda: page.view() == "one", timeout=WAIT_MS)
    _showing(qtbot, page, "1 of 4 · a.jpg")
    qtbot.waitUntil(lambda: _focused() is page._box, timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_G)  # G goes back too
    qtbot.waitUntil(lambda: page.view() == "day", timeout=WAIT_MS)
    assert grid.ticked() == ["a.jpg", "c.jpg"]
    qtbot.waitUntil(lambda: page.day_grid.isAncestorOf(_focused()), timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Escape)  # the grid's Esc: one at a time, on the focused file
    qtbot.waitUntil(lambda: page.view() == "one", timeout=WAIT_MS)
    _showing(qtbot, page, "1 of 4 · a.jpg")
    qtbot.waitUntil(lambda: _focused() is page._box, timeout=WAIT_MS)
    assert w.strip.text() == f"{IMAGE_KEYS} · {PAGE_KEYS}"
    asked = []

    def stay(dialog):
        asked.append(dialog.windowTitle())
        _key(qtbot, Qt.Key.Key_Escape)

    _in_dialog(qtbot, stay)
    _key(qtbot, Qt.Key.Key_Escape)  # the plain one view: Esc asks to leave
    assert asked == ["Leave Preview?"]
    assert page.view() == "one"
    assert page.session is not None
    assert errors == []


# ----- the days ------------------------------------------------------------------------------

def test_pgup_pgdn_change_day(qtbot, make_window, root, errors):
    _photo(root, "a.jpg", SAT)
    _photo(root, "b.jpg", FRI)
    _photo(root, "c.jpg", SUN)
    _photo(root, "d.jpg", SUN.replace(hour=13))
    w = make_window()
    page = _open(qtbot, w, root)
    grid = _grid(qtbot, page)
    assert page.day_text().startswith("Sat 1 Mar 2025 · day 2 of 3")
    assert page.after_button.isVisible() and page.before_button.isVisible()
    _key(qtbot, Qt.Key.Key_Space)  # a unticked
    _key(qtbot, Qt.Key.Key_PageDown)
    assert page.day_text() == "Sun 2 Mar 2025 · day 3 of 3 · 2 files · 2 ticked"
    assert grid.rels() == ["c.jpg", "d.jpg"]
    assert grid.focused() == "c.jpg"
    assert not page.after_button.isEnabled()
    _key(qtbot, Qt.Key.Key_PageDown)  # none after
    assert page.day_text().startswith("Sun 2 Mar 2025 · day 3 of 3")
    _key(qtbot, Qt.Key.Key_PageUp)
    _key(qtbot, Qt.Key.Key_PageUp)
    assert page.day_text() == "Fri 28 Feb 2025 · day 1 of 3 · 1 file · 1 ticked"
    assert not page.before_button.isEnabled()
    qtbot.mouseClick(page.after_button, Qt.MouseButton.LeftButton)
    assert page.day_text() == "Sat 1 Mar 2025 · day 2 of 3 · 1 file · 1 ticked"  # ticked anew
    assert page.before_button.text() == "◀ Day before (PgUp)"
    assert page.after_button.text() == "Day after (PgDn) ▶"
    _key(qtbot, Qt.Key.Key_G)
    qtbot.waitUntil(lambda: page.view() == "one", timeout=WAIT_MS)
    assert not page.after_button.isVisible()
    assert errors == []


def test_a_group_move_with_one_failure(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_Space)  # a unticked
    (one_day / "b.jpg").unlink()  # gone under it
    _key(qtbot, Qt.Key.Key_N)
    qtbot.waitUntil(lambda: page.message().startswith("Couldn't move b.jpg: "),
                    timeout=WAIT_MS)
    _idle(qtbot, page)
    assert (one_day / NOT_NEEDED / "c.jpg").is_file()
    assert grid.rels() == ["a.jpg", "b.jpg"]  # back in its place
    assert grid.ticked() == ["b.jpg"]  # ticked, as it was
    assert [src for src, _ in page.session.moved] == ["c.jpg"]
    assert errors == []


def test_a_group_failing_whole_on_its_last_day_comes_back(qtbot, make_window, one_day, errors):
    """The grid empties, but its moves fail: the day stays, its files back, ticked."""
    (one_day / "d.jpg").unlink()
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_A)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)  # b alone ticked
    (one_day / "b.jpg").unlink()
    _key(qtbot, Qt.Key.Key_N)
    _key(qtbot, Qt.Key.Key_A)
    _key(qtbot, Qt.Key.Key_N)  # a and c too: the grid is empty while b's move fails
    qtbot.waitUntil(lambda: page.message().startswith("Couldn't move b.jpg: "),
                    timeout=WAIT_MS)
    _idle(qtbot, page)
    assert w.current_page() is page
    assert page.view() == "day"
    assert grid.rels() == ["b.jpg"]
    assert grid.ticked() == ["b.jpg"]
    assert errors == []


# ----- the look ------------------------------------------------------------------------------

def test_day_grid_fits_1024x600(qtbot, make_window, root, errors):
    for n in range(12):
        _photo(root, f"p{n:02}.jpg", FRI.replace(minute=n))
    (root / "a long name of a document that goes on and on.pdf").write_bytes(b"%PDF-1.4")
    _at(root / "a long name of a document that goes on and on.pdf", FRI.replace(minute=30))
    w = make_window(_settings(root / "Trips"), size=(1024, 600))
    page = _open(qtbot, w, root)
    grid = _grid(qtbot, page)
    assert w.strip.text() == GRID_KEYS
    room = w.stack.size()
    assert page.minimumSizeHint().width() <= room.width()
    assert page.minimumSizeHint().height() <= room.height()
    window = w.rect()
    strip_top = w.strip.mapTo(w, QPoint(0, 0)).y()
    for widget in (page.title_label, page.before_button, page.after_button, page.edit_button,
                   page.show_button, grid):
        assert widget.isVisible(), widget
        rect = QRect(widget.mapTo(w, QPoint(0, 0)), widget.size())
        assert window.contains(rect), widget
        assert rect.bottom() < strip_top, widget
    for button in page.findChildren(QPushButton):
        if button.isVisible():
            assert button.width() >= button.minimumSizeHint().width(), button.text()
    card = grid.card(grid.focused())
    view = grid.scroll.viewport()
    qtbot.waitUntil(lambda: view.rect().contains(QRect(card.mapTo(view, QPoint(0, 0)),
                                                       card.size())),
                    timeout=WAIT_MS)  # a whole card, its tick too, shows
    assert grid.scroll.columns() >= 2
    pdf = grid.card("a long name of a document that goes on and on.pdf")
    assert pdf.thumb_label.text() == "PDF"
    assert pdf.width() <= grid.scroll.viewport().width()
    assert errors == []


def test_a_big_day_opens_at_once(qtbot, make_window, root, errors):
    for n in range(300):
        _photo(root, f"p{n:03}.jpg", FRI.replace(minute=n % 60, second=n // 60))
    w = make_window()
    page = _open(qtbot, w, root)
    start = time.monotonic()
    _key(qtbot, Qt.Key.Key_G)
    qtbot.waitUntil(lambda: page.view() == "day", timeout=WAIT_MS)
    took = time.monotonic() - start
    grid = page.day_grid
    assert len(grid.rels()) == 300
    assert took < 1.0, took
    assert any(grid.card(rel).thumb_label.text() == LOADING for rel in grid.rels())  # later
    qtbot.waitUntil(lambda: all(not grid.card(rel).thumb_label.pixmap().isNull()
                                for rel in grid.rels()), timeout=30000)
    assert errors == []


def test_kind_words():
    from photosort.ui.preview_day import kind_word

    assert kind_word(Path("a.pdf")) == "PDF"
    assert kind_word(Path("a.mp4")) == "Video"
    assert kind_word(Path("a.wav")) == "Sound"
    assert kind_word(Path("a.txt")) == "Text"
    assert kind_word(Path("a.zip")) == "ZIP file"
    assert kind_word(Path("README")) == "File"
    assert kind_word(Path("a.jpg")) is None


def test_unknown_day_is_last(qtbot, make_window, root, monkeypatch, errors):
    from photosort.ui import preview_page as pp

    _photo(root, "a.jpg", FRI)
    _photo(root, "b.jpg", SAT)
    real = pp.file_day
    monkeypatch.setattr(pp, "file_day", lambda p: None if p.name == "a.jpg" else real(p))
    w = make_window()
    page = _open(qtbot, w, root)
    _grid(qtbot, page)
    assert page.day_text() == "Unknown day · day 2 of 2 · 1 file · 1 ticked"
    assert errors == []


def test_viewer_keys_unchanged(qtbot, make_window, one_day, errors):
    """G is not a viewer key: a text file's G opens the day too."""
    (one_day / "e.txt").write_text("text\n")
    _at(one_day / "e.txt", SAT.replace(hour=13))
    w = make_window()
    page = _open(qtbot, w, one_day)
    for _ in range(4):
        _key(qtbot, Qt.Key.Key_Right)
    _showing(qtbot, page, "5 of 5 · e.txt")
    assert isinstance(page.viewer(), pv.TextViewer)
    grid = _grid(qtbot, page)
    assert grid.rels() == ["d.jpg", "e.txt"]
    assert errors == []


# ----- fix round 1 ---------------------------------------------------------------------------

def test_putting_back_a_big_group_is_quick(qtbot, make_window, root, monkeypatch, errors):
    """300 files back into the grid in one go: the handler never freezes the window."""
    for n in range(300):
        _photo(root, f"p{n:03}.jpg", FRI.replace(minute=n % 60, second=n // 60))
    w = make_window()
    page = _open(qtbot, w, root)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_Space)  # p000 stays
    _key(qtbot, Qt.Key.Key_N)
    _idle(qtbot, page)
    assert grid.rels() == ["p000.jpg"]
    took = []
    real = page._put_back_ok

    def timed(result):
        start = time.perf_counter()
        real(result)
        took.append(time.perf_counter() - start)

    monkeypatch.setattr(page, "_put_back_ok", timed)
    qtbot.waitUntil(lambda: page.day_grid.isAncestorOf(_focused()), timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    _idle(qtbot, page)
    assert len(grid.rels()) == 300
    assert len(grid.ticked()) == 299
    assert len(took) == 1 and took[0] < 0.3, took
    assert errors == []


def test_n_then_ctrl_z_on_the_last_day_before_the_moves_end(qtbot, make_window, one_day,
                                                           monkeypatch, errors):
    """The put-back is queued: the Preview never ends, the files come back."""
    (one_day / "d.jpg").unlink()
    gate = _slow_moves(monkeypatch)
    w = make_window()
    page = _open(qtbot, w, one_day)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_N)
    assert grid.rels() == []
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    gate.set()
    _idle(qtbot, page)
    qtbot.wait(50)
    assert w.current_page() is page
    assert page.session is not None
    assert page.view() == "day"
    assert grid.rels() == ["a.jpg", "b.jpg", "c.jpg"]
    assert page.session.moved == []
    assert errors == []


def test_days_are_kept_only_once_read(qtbot, make_window, one_day, errors):
    w = make_window()
    page = _open(qtbot, w, one_day)
    _key(qtbot, Qt.Key.Key_N)
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    _showing(qtbot, page, "1 of 4 · a.jpg")
    _idle(qtbot, page)
    assert page._days == {}  # no G yet: nothing read, nothing kept
    assert errors == []


def test_one_at_a_time_from_an_empty_grid_goes_to_the_next_days_first(
        qtbot, make_window, root, monkeypatch, errors):
    _photo(root, "a.jpg", SAT)
    _photo(root, "b.jpg", FRI)
    _photo(root, "c.jpg", SUN.replace(hour=13))
    _photo(root, "d.jpg", SUN)  # Sun's first, though listed after c
    gate = _slow_moves(monkeypatch)
    w = make_window()
    page = _open(qtbot, w, root)
    grid = _grid(qtbot, page)
    _key(qtbot, Qt.Key.Key_N)  # Sat's a: the grid is empty while it moves
    assert grid.rels() == []
    _key(qtbot, Qt.Key.Key_G)
    assert page.view() == "one"
    assert page.current() == "d.jpg"
    gate.set()
    _idle(qtbot, page)
    assert errors == []
