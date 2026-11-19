"""The "Name the busy days" page and the Date flow around it."""

from datetime import date
from pathlib import Path

import pytest
from keys import (
    _focused,
    _go,
    _in_dialog,
    _key,
    _on_summary,
    _start_by_keyboard,
    errors,  # noqa: F401  (fixtures, used by name)
    make_window,  # noqa: F401
    models_present,  # noqa: F401
    settings_path,  # noqa: F401
)
from PySide6.QtCore import QPoint, QRect, Qt, QThreadPool

from photosort.hashing import sha256_file
from photosort.journal import load_runs
from photosort.ui import dialogs
from photosort.ui.busy_days_page import BusyDaysPage

CTRL = Qt.KeyboardModifier.ControlModifier
D20, D21 = date(2025, 2, 20), date(2025, 2, 21)
PURI = "2025/02-February/20-02-2025 Puri trip"


@pytest.fixture
def photos(tmp_path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def _day(make_jpeg, root: Path, d: date, photos: int, videos: int = 0,
         prefix: str = "p", make_mp4=None) -> list[str]:
    """photos photos and videos videos dated d in root (the videos dated
    only when made with make_mp4); their names."""
    names = []
    for n in range(photos):
        name = f"{prefix}{n:02d}.jpg"
        make_jpeg(root / name, f"{d:%Y:%m:%d} 10:{n:02d}:00")
        names.append(name)
    for n in range(videos):
        name = f"{prefix}{n:02d}.mp4"
        if make_mp4 is None:
            (root / name).write_bytes(b"not really a video")
        else:
            make_mp4(root / name, f"{d:%Y-%m-%d}T10:00:00Z")
        names.append(name)
    return names


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


def _files(root: Path) -> list[str]:
    return sorted(_hashes(root))


@pytest.fixture
def make_page(qtbot):
    def _make() -> BusyDaysPage:
        page = BusyDaysPage()
        qtbot.addWidget(page)
        return page

    yield _make
    QThreadPool.globalInstance().waitForDone()


def _start(qtbot, w, root: Path) -> None:
    w.folder_edit.setText(str(root))
    w.mode_buttons["date"].click()


# ----- the page ----------------------------------------------------------------------------

def test_busy_days_page_shows_rows_oldest_first(qtbot, make_page, photos, make_jpeg):
    late = _day(make_jpeg, photos, D21, 11, prefix="b")
    early = _day(make_jpeg, photos, D20, 13, 2, prefix="a")
    page = make_page()
    page.load(photos, {D20: early, D21: late})
    assert [(r.date_label.text(), r.count_label.text()) for r in page.rows] == [
        ("Thu 20 Feb 2025", "13 photos, 2 videos"),
        ("Fri 21 Feb 2025", "11 photos"),
    ]
    assert [r.day for r in page.rows] == [D20, D21]
    assert [r.name_edit.placeholderText() for r in page.rows] == ["20-02-2025", "21-02-2025"]
    assert all(r.name_edit.text() == "" for r in page.rows)
    assert [r.show_all_button.text() for r in page.rows] == ["Show all", "Show all"]
    # a collage of the first 12 photos (videos can't be shown)
    assert page.rows[0].collage_files == early[:12]
    assert page.rows[1].collage_files == late
    assert page.heading_label.text() == "Name the busy days"
    qtbot.waitUntil(lambda: all(not label.pixmap().isNull()
                                for r in page.rows for label in r.thumb_labels))
    page.load(photos, {D21: late})  # a new load replaces the rows
    assert [r.day for r in page.rows] == [D21]


def test_show_all_lists_every_file_and_esc_closes(qtbot, make_page, photos, make_jpeg):
    files = _day(make_jpeg, photos, D20, 14, 1)
    page = make_page()
    page.resize(1000, 600)
    page.load(photos, {D20: files})
    page.show()
    qtbot.waitExposed(page)
    page.activateWindow()
    qtbot.waitUntil(lambda: _focused() is page.rows[0])
    _key(qtbot, Qt.Key.Key_A)
    assert page.all_view.isVisible()
    assert not page.scroll.isVisible()
    grid = page.all_view.grid
    assert [grid.item(n).toolTip() for n in range(grid.count())] == files
    assert page.all_view.heading_label.text() == "Thu 20 Feb 2025: all 15 files"
    qtbot.waitUntil(lambda: _focused() is grid)
    qtbot.waitUntil(lambda: all(not grid.item(n).icon().isNull()
                                for n in range(grid.count() - 1)))  # the video has none
    _key(qtbot, Qt.Key.Key_Escape)  # closes the grid, not the page
    assert not page.all_view.isVisible()
    assert page.scroll.isVisible()
    assert _focused() is page.rows[0]
    page.rows[0].show_all_button.click()  # by mouse too
    assert page.all_view.isVisible()
    page.all_view.close_button.click()
    assert not page.all_view.isVisible()


def test_page_keys_and_names(qtbot, make_page, photos, make_jpeg, monkeypatch):
    page = make_page()
    page.resize(1000, 600)
    page.load(photos, {D20: _day(make_jpeg, photos, D20, 11, prefix="a"),
                       D21: _day(make_jpeg, photos, D21, 11, prefix="b")})
    page.show()
    qtbot.waitExposed(page)
    page.activateWindow()
    qtbot.waitUntil(lambda: _focused() is page.rows[0])
    named, left = [], []
    page.named.connect(named.append)
    page.left.connect(lambda: left.append(True))
    _key(qtbot, Qt.Key.Key_Down)
    assert _focused() is page.rows[1]
    _key(qtbot, Qt.Key.Key_F2)
    assert _focused() is page.rows[1].name_edit
    qtbot.keyClicks(_focused(), "Party")
    _key(qtbot, Qt.Key.Key_Escape)  # stops editing; the name stays
    assert _focused() is page.rows[1] and page.rows[1].name_edit.text() == "Party"
    _key(qtbot, Qt.Key.Key_Up)
    _key(qtbot, Qt.Key.Key_Return)
    assert _focused() is page.rows[0].name_edit
    qtbot.keyClicks(_focused(), "   ")  # blank: no name
    _key(qtbot, Qt.Key.Key_Return)
    assert _focused() is page.rows[0]
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    assert named == [{D21: "Party"}]
    asked = []
    monkeypatch.setattr(dialogs, "ask", lambda *a, **k: asked.append(a[1]) or False)
    _key(qtbot, Qt.Key.Key_Escape)  # asks; Stay
    assert asked == ["Leave this review?"] and left == []
    monkeypatch.setattr(dialogs, "ask", lambda *a, **k: True)
    _key(qtbot, Qt.Key.Key_Escape)
    assert left == [True]


# ----- the flow ----------------------------------------------------------------------------

def test_no_busy_days_skips_page(qtbot, make_window, photos, make_jpeg, make_mp4):
    # 10 photos: not busy, whatever the videos
    _day(make_jpeg, photos, D20, 10, 3, make_mp4=make_mp4)
    w = make_window()
    _start(qtbot, w, photos)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines()[0] == (
        "13 files → 1 month folders, 1 day folders, 0 No Date")
    w.back_button.click()
    assert w.current_page() is w.home


def test_name_then_continue_plans_named_folder(qtbot, make_window, photos, make_jpeg,
                                               make_mp4):
    files = _day(make_jpeg, photos, D20, 11, 1, make_mp4=make_mp4)
    w = make_window()
    _start(qtbot, w, photos)
    page = w.busy_days_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    assert [r.day for r in page.rows] == [D20]
    page.rows[0].name_edit.setText("Puri trip")
    page.continue_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines()[:2] == [
        "12 files → 1 month folders, 1 day folders, 0 No Date", "1 busy day named"]
    assert {(m.src, m.dst) for m in w.summary_page._moves} == {
        (rel, f"{PURI}/{rel}") for rel in files}
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert _files(photos) == sorted(f"{PURI}/{rel}" for rel in files)


def test_back_from_summary_returns_to_page_with_names_kept(qtbot, make_window, photos,
                                                           make_jpeg):
    _day(make_jpeg, photos, D20, 11, prefix="a")
    _day(make_jpeg, photos, D21, 12, prefix="b")
    w = make_window()
    _start(qtbot, w, photos)
    page = w.busy_days_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    page.rows[1].name_edit.setText("Party")
    page.continue_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert "1 busy day named" in w.summary_label.text()
    w.back_button.click()
    assert w.current_page() is page
    assert [r.name_edit.text() for r in page.rows] == ["", "Party"]
    page.rows[0].name_edit.setText("Goa")
    page.continue_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert "2 busy days named" in w.summary_label.text()
    dsts = {m.dst.rsplit("/", 1)[0] for m in w.summary_page._moves}
    assert dsts == {"2025/02-February/20-02-2025 Goa", "2025/02-February/21-02-2025 Party"}


def test_leaving_the_page_goes_home_with_nothing_moved(qtbot, make_window, photos,
                                                      make_jpeg, monkeypatch):
    _day(make_jpeg, photos, D20, 11)
    before = _hashes(photos)
    w = make_window()
    _start(qtbot, w, photos)
    qtbot.waitUntil(lambda: w.current_page() is w.busy_days_page)
    monkeypatch.setattr(dialogs, "ask", lambda *a, **k: True)
    w.busy_days_page.back_button.click()
    assert w.current_page() is w.home
    assert w.flow_state is None
    assert _hashes(photos) == before
    assert load_runs(photos) == []


@pytest.mark.real_dialogs
def test_keyboard_only_date_with_busy_day(qtbot, make_window, photos, make_jpeg, make_mp4,
                                          errors):
    files = _day(make_jpeg, photos, D20, 11, 1, make_mp4=make_mp4)
    _day(make_jpeg, photos, D21, 2, prefix="q")
    before = _hashes(photos)
    w = make_window()
    _start_by_keyboard(qtbot, w, photos, "date")
    page = w.busy_days_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    qtbot.waitUntil(lambda: _focused() is page.rows[0])
    assert w.strip.text() == ("↑↓ day · Enter name · A all photos · Ctrl+Enter continue · "
                              "Esc leave · Tab/Shift+Tab area")
    _key(qtbot, Qt.Key.Key_A)
    qtbot.waitUntil(lambda: _focused() is page.all_view.grid)
    assert w.strip.text() == "↑↓←→ move · Esc close · Tab/Shift+Tab area"
    _key(qtbot, Qt.Key.Key_Escape)
    assert _focused() is page.rows[0]
    _key(qtbot, Qt.Key.Key_Return)  # name it
    assert _focused() is page.rows[0].name_edit
    assert w.strip.text() == "Enter done · Esc stop editing · Ctrl+Enter continue · " \
                             "Tab/Shift+Tab area"
    qtbot.keyClicks(_focused(), "Puri trip")
    _key(qtbot, Qt.Key.Key_Return)  # done
    assert _focused() is page.rows[0]
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    _on_summary(qtbot, w)
    assert "1 busy day named" in w.summary_label.text()
    _go(qtbot, w)
    assert sorted(f for f in _files(photos) if f.startswith(PURI)) == sorted(
        f"{PURI}/{rel}" for rel in files)

    def undo(dialog):  # Undo, not Cancel
        _key(qtbot, Qt.Key.Key_Right)
        _key(qtbot, Qt.Key.Key_Return)

    _in_dialog(qtbot, undo)
    _key(qtbot, Qt.Key.Key_U)
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _hashes(photos) == before
    assert not (photos / "2025").exists()
    assert errors == []


def test_fits_1024x600(qtbot, make_window, photos, make_jpeg):
    _day(make_jpeg, photos, D20, 30, 3, prefix="a")
    _day(make_jpeg, photos, D21, 11, prefix="b")
    w = make_window(size=(1024, 600))
    _start(qtbot, w, photos)
    page = w.busy_days_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    qtbot.waitUntil(lambda: _focused() is page.rows[0])
    assert w.minimumSizeHint().width() <= 1024 and w.minimumSizeHint().height() <= 600
    assert not page.scroll.horizontalScrollBar().isVisible()
    viewport = page.scroll.viewport()
    row = page.rows[0]
    top_left = row.mapTo(viewport, QPoint(0, 0))
    assert viewport.rect().contains(QRect(top_left, row.size()))  # a whole day in view
    shown = [label for label in row.thumb_labels if label.isVisible()]
    assert len(shown) >= 8
    window = w.rect()
    for widget in (page.continue_button, page.back_button, row.name_edit,
                   row.show_all_button, row.date_label, row.count_label):
        assert widget.isVisible()
        assert window.contains(QRect(widget.mapTo(w, QPoint(0, 0)), widget.size()))
    _key(qtbot, Qt.Key.Key_A)
    assert page.all_view.close_button.isVisible()
    close = page.all_view.close_button
    assert window.contains(QRect(close.mapTo(w, QPoint(0, 0)), close.size()))
