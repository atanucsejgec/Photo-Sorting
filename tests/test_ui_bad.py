from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QImage

from photosort.groups import Candidate
from photosort.people import PeopleStore
from photosort.settings import Settings
from photosort.ui.bad_photos_page import BadPhotosPage
from photosort.ui.flows import bad as bad_flow
from photosort.ui.main_window import MainWindow
from photosort.ui.photo_preview import PhotoPreview


# ----- the page ------------------------------------------------------------

@pytest.fixture
def make_page(qtbot):
    def _make() -> BadPhotosPage:
        page = BadPhotosPage()
        qtbot.addWidget(page)
        return page

    yield _make
    QThreadPool.globalInstance().waitForDone()


def _photo(root: Path, rel: str, note: str, size=(64, 48)) -> Candidate:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "black").save(path, "JPEG")
    return Candidate(rel, "photo", *size, path.stat().st_size, None, 0.0, note)


def _three(root: Path) -> list[Candidate]:
    return [_photo(root, "a.jpg", "accidental shot"), _photo(root, "sub/b.jpg", "too dark"),
            _photo(root, "c.jpg", "blurry")]


def _ticked(page: BadPhotosPage) -> set[str]:
    return {card.candidate.rel for card in page.cards if card.move_check.isChecked()}


def test_one_card_per_photo_all_ticked(qtbot, tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path))
    assert [card.candidate.rel for card in page.cards] == ["a.jpg", "sub/b.jpg", "c.jpg"]
    assert _ticked(page) == {"a.jpg", "sub/b.jpg", "c.jpg"}
    assert all(card.move_check.text() == "Move to Bad Photos" for card in page.cards)
    assert [(c.name_label.text(), c.folder_label.text()) for c in page.cards] == [
        ("a.jpg", "(top folder)"), ("b.jpg", "sub"), ("c.jpg", "(top folder)")]
    assert page.cards[1].name_label.toolTip() == "sub/b.jpg"
    assert all(card.folder_label.objectName() == "muted" for card in page.cards)
    assert [card.tag_label.text() for card in page.cards] == [
        "accidental shot", "too dark", "blurry"]  # the reason, as a tag
    assert all(card.objectName() == "card" for card in page.cards)
    assert page.ticked_label.text() == "3 will be set aside"
    assert page.heading_label.text() == "3 bad photos"
    assert page.subtitle_label.text() == (
        "Tick the ones to move. They go to Bad Photos/ (nothing is deleted). "
        "Double-click a photo to see it larger.")
    qtbot.waitUntil(lambda: all(not c.thumb_label.pixmap().isNull() for c in page.cards))


def test_load_with_the_ticks_given(tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path), {"c.jpg"})
    assert _ticked(page) == {"c.jpg"}


def test_subtitle_names_the_folder_as_spelt_on_disk(tmp_path, make_page):
    (tmp_path / "bad photos").mkdir()
    page = make_page()
    page.load(tmp_path, _three(tmp_path)[:1])
    assert page.heading_label.text() == "1 bad photo"
    assert page.subtitle_label.text() == (
        "Tick the ones to move. They go to bad photos/ (nothing is deleted). "
        "Double-click a photo to see it larger.")


def test_tick_all_untick_all_and_continue(tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path))
    results = []
    page.finished.connect(results.append)
    page.untick_all_button.click()
    assert _ticked(page) == set()
    page.continue_button.click()
    page.tick_all_button.click()
    page.cards[1].move_check.setChecked(False)
    page.continue_button.click()
    assert results == [set(), {"a.jpg", "c.jpg"}]


def test_back_cancels(tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path))
    cancelled = []
    page.cancelled.connect(lambda: cancelled.append(True))
    page.back_button.click()
    assert cancelled == [True]


def test_unreadable_thumbnail(qtbot, tmp_path, make_page):
    (tmp_path / "broken.jpg").write_bytes(b"not a jpeg")
    page = make_page()
    page.load(tmp_path, [Candidate("broken.jpg", "photo", None, None, 10, None, None, "blurry")])
    qtbot.waitUntil(lambda: page.cards[0].thumb_label.text() == "Can't show")


def test_late_thumbnail_for_old_cards_is_ignored(qtbot, tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path))
    page.load(tmp_path, _three(tmp_path)[:1])  # before the first thumbnails arrive
    QThreadPool.globalInstance().waitForDone()
    qtbot.waitUntil(lambda: not page.cards[0].thumb_label.pixmap().isNull())
    page.scroll._thumbnail_ready((-1, 2, QImage(4, 4, QImage.Format.Format_RGB888)))  # stale
    assert [card.candidate.rel for card in page.cards] == ["a.jpg"]


def test_double_click_opens_the_preview(qtbot, tmp_path, make_page):
    bad = [_photo(tmp_path, "big.jpg", "blurry", size=(2000, 1000))]
    page = make_page()
    page.show()
    page.load(tmp_path, bad)
    qtbot.mouseDClick(page.cards[0].thumb_label, Qt.MouseButton.LeftButton)
    preview = page.preview
    assert isinstance(preview, PhotoPreview)
    assert preview.isVisible()
    assert preview.windowTitle() == "big.jpg"
    qtbot.waitUntil(lambda: preview.photo is not None)
    assert (preview.photo.width(), preview.photo.height()) == (1600, 800)  # at most 1600 px
    assert not preview.image_label.pixmap().isNull()
    assert preview.image_label.pixmap().width() <= preview.image_label.width()  # fitted
    with qtbot.waitSignal(preview.destroyed):  # Esc closes it, and it lets itself go
        qtbot.keyClick(preview, Qt.Key.Key_Escape)
    assert page.preview is None


def test_preview_of_an_unreadable_photo(qtbot, tmp_path):
    (tmp_path / "broken.jpg").write_bytes(b"not a jpeg")
    preview = PhotoPreview(tmp_path / "broken.jpg", None)
    qtbot.addWidget(preview)
    qtbot.waitUntil(lambda: preview.image_label.text() == "Can't show this photo")
    assert preview.photo is None


# ----- the main window -----------------------------------------------------

@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def make_window(qtbot, tmp_path, errors):
    windows = []

    def _make() -> MainWindow:
        w = MainWindow(Settings(), tmp_path / "config" / "settings.json",
                       people_store=PeopleStore(tmp_path / "people"))
        qtbot.addWidget(w)
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def photos(tmp_path, make_pattern_jpeg):
    """A folder with a black photo, a blurred one and a sharp one."""
    folder = tmp_path / "photos"
    folder.mkdir()
    Image.new("RGB", (64, 48), "black").save(folder / "pocket.jpg", "JPEG")
    make_pattern_jpeg(folder / "blurred.jpg", 1, blur=4)
    make_pattern_jpeg(folder / "sharp.jpg", 2)
    return folder


def _start_bad(qtbot, w, folder):
    w.folder_edit.setText(str(folder))
    w.mode_buttons["bad"].click()
    qtbot.waitUntil(lambda: w.current_page() is not w.progress_page)


def _files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                  if p.is_file() and ".photosort" not in p.parts)


def test_bad_photos_end_to_end(qtbot, make_window, photos, errors):
    from photosort.journal import load_runs

    before = _files(photos)
    w = make_window()
    _start_bad(qtbot, w, photos)
    assert w.current_page() is w.bad_page
    assert w.progress_label.text() == "Checking photos… 3 of 3"
    page = w.bad_page
    assert [(c.candidate.rel, c.candidate.note) for c in page.cards] == [
        ("blurred.jpg", "blurry"), ("pocket.jpg", "accidental shot")]
    page.continue_button.click()
    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines() == ["2 photos → Bad Photos"]
    assert w.go_button.isEnabled()

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 2 files." in w.done_label.text()
    assert _files(photos) == ["Bad Photos/blurred.jpg", "Bad Photos/pocket.jpg", "sharp.jpg"]
    assert [run.mode for run in load_runs(photos)] == ["bad"]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _files(photos) == before
    assert not (photos / "Bad Photos").exists()
    assert errors == []


def test_nothing_ticked(qtbot, make_window, photos, errors):
    w = make_window()
    _start_bad(qtbot, w, photos)
    w.bad_page.untick_all_button.click()
    w.bad_page.continue_button.click()
    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines() == [
        "Nothing to move.", "0 photos → Bad Photos", "2 photos stay where they are"]
    assert not w.go_button.isEnabled()
    assert errors == []


def test_back_from_summary_keeps_the_ticks(qtbot, make_window, photos, errors):
    w = make_window()
    _start_bad(qtbot, w, photos)
    page = w.bad_page
    page.cards[0].move_check.setChecked(False)
    page.continue_button.click()
    assert w.summary_label.text().splitlines() == [
        "1 photos → Bad Photos", "1 photos stay where they are"]

    w.back_button.click()
    assert w.current_page() is page
    assert _ticked(page) == {"pocket.jpg"}
    qtbot.waitUntil(lambda: all(not c.thumb_label.pixmap().isNull() for c in page.cards))
    page.tick_all_button.click()
    page.continue_button.click()
    assert w.summary_label.text().splitlines() == ["2 photos → Bad Photos"]
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert _files(photos) == ["Bad Photos/blurred.jpg", "Bad Photos/pocket.jpg", "sharp.jpg"]
    assert errors == []


def test_back_on_the_page_goes_home(qtbot, make_window, photos):
    w = make_window()
    _start_bad(qtbot, w, photos)
    w.bad_page.back_button.click()
    assert w.current_page() is w.home


def test_no_bad_photos_found(qtbot, make_window, tmp_path, make_pattern_jpeg, errors):
    folder = tmp_path / "good"
    make_pattern_jpeg(folder / "sharp.jpg", 2)
    w = make_window()
    _start_bad(qtbot, w, folder)
    assert w.current_page() is w.summary_page
    assert w.summary_label.text() == "No bad photos found."
    assert not w.go_button.isEnabled()
    w.back_button.click()
    assert w.current_page() is w.home
    assert errors == []


def test_planning_error_goes_home(qtbot, make_window, photos, errors, monkeypatch):
    def unplugged(*args, **kwargs):
        raise OSError("drive gone")

    monkeypatch.setattr(bad_flow, "plan_bad", unplugged)
    w = make_window()
    _start_bad(qtbot, w, photos)
    w.bad_page.continue_button.click()
    assert w.current_page() is w.home
    assert errors == ["Could not plan the moves: drive gone"]
    w.back_button.click()  # a stale Back must not bring the page back
    assert w.current_page() is w.home


def test_cancelled_finder_returns_home(qtbot, make_window, photos, monkeypatch):
    from photosort.modes import bad_photos

    monkeypatch.setattr(bad_photos, "find_bad_photos",
                        lambda root, include, progress=None, cancel=None: None)
    w = make_window()
    _start_bad(qtbot, w, photos)
    assert w.current_page() is w.home


def test_start_forgets_the_last_ticks(qtbot, make_window, photos):
    w = make_window()
    _start_bad(qtbot, w, photos)
    w.bad_page.cards[0].move_check.setChecked(False)
    w.bad_page.continue_button.click()
    assert w.back_to is not None
    w.back_button.click()
    w.bad_page.back_button.click()  # home
    w.mode_buttons["date"].click()
    assert w.back_to is None and w.flow_state is None  # no old ticks for a later Back
