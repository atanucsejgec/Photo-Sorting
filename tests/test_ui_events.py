from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QImage

from photosort.modes import events
from photosort.modes.events import Event
from photosort.people import PeopleStore
from photosort.settings import Settings
from photosort.ui.events_page import EventsPage
from photosort.ui.flows import events as events_flow
from photosort.ui.main_window import MainWindow

GOA = datetime(2025, 1, 10, 9, 0)
PARTY = datetime(2025, 2, 20, 18, 0)


def _jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 24), "red").save(path, "JPEG")


def _event(root: Path, prefix: str, *times: datetime) -> Event:
    files = tuple(f"{prefix}{n:02d}.jpg" for n in range(len(times)))
    for rel in files:
        _jpeg(root / rel)
    return Event(files, times)


# ----- the page ------------------------------------------------------------

@pytest.fixture
def make_page(qtbot):
    def _make() -> EventsPage:
        page = EventsPage()
        qtbot.addWidget(page)
        return page

    yield _make
    QThreadPool.globalInstance().waitForDone()


def _three(root: Path) -> list[Event]:
    """Goa over three days, a party on one day, and one more party that day."""
    goa = _event(root, "goa", GOA, GOA + timedelta(hours=30), GOA + timedelta(days=2))
    party = _event(root, "party", PARTY, PARTY + timedelta(hours=1),
                   PARTY + timedelta(hours=4), PARTY + timedelta(hours=5))
    late = _event(root, "late", PARTY + timedelta(hours=5, minutes=30))
    return [goa, party, late]


def _rows(page: EventsPage):
    return [(r.name_edit.placeholderText(), r.range_label.text(), r.count_label.text())
            for r in page.rows]


def test_one_row_per_event(qtbot, tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path), 7)
    assert _rows(page) == [
        ("Event 10-01-2025", "10 Jan 2025 – 12 Jan 2025", "3 files"),
        ("Event 20-02-2025", "20 Feb 2025", "4 files"),
        ("Event 20-02-2025 (2)", "20 Feb 2025", "1 file"),
    ]
    assert all(r.name_edit.text() == "" for r in page.rows)
    assert [len(r.thumb_labels) for r in page.rows] == [3, 4, 1]
    assert [r.merge_button.isEnabled() for r in page.rows] == [True, True, False]
    assert [r.split_button.isEnabled() for r in page.rows] == [True, True, False]
    assert [r.merge_button.text() for r in page.rows] == ["Merge with next"] * 3
    assert [r.skip_check.text() for r in page.rows] == ["Skip"] * 3
    assert page.heading_label.text() == "3 events"
    assert "7 files are in no event and stay where they are." in page.subtitle_label.text()
    assert page.back_button.text() == "Back" and page.continue_button.text() == "Continue"
    qtbot.waitUntil(lambda: all(not label.pixmap().isNull()
                                for r in page.rows for label in r.thumb_labels))


def test_at_most_four_thumbnails(tmp_path, make_page):
    many = _event(tmp_path, "m", *(GOA + timedelta(minutes=n) for n in range(9)))
    page = make_page()
    page.load(tmp_path, [many], 0)
    assert len(page.rows[0].thumb_labels) == 4


def test_videos_show_as_video(tmp_path, make_page):
    (tmp_path / "clip.mp4").write_bytes(b"not decoded")
    page = make_page()
    page.load(tmp_path, [Event(("clip.mp4",), (GOA,))], 0)
    assert page.rows[0].thumb_labels[0].text() == "Video"


def test_split_disabled_when_all_at_one_moment(tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, [_event(tmp_path, "s", GOA, GOA)], 0)
    assert not page.rows[0].split_button.isEnabled()


def _finished(page: EventsPage) -> list:
    results = []
    page.finished.connect(lambda found, names, skipped: results.append(
        (found, names, skipped)))
    return results


def test_merge_with_next_keeps_the_first_name(tmp_path, make_page):
    found = _three(tmp_path)
    page = make_page()
    page.load(tmp_path, found, 0)
    page.rows[1].name_edit.setText("Party")
    page.rows[2].name_edit.setText("Late")
    page.rows[1].merge_button.click()
    assert _rows(page) == [
        ("Event 10-01-2025", "10 Jan 2025 – 12 Jan 2025", "3 files"),
        ("Event 20-02-2025", "20 Feb 2025", "5 files"),
    ]
    assert [r.name_edit.text() for r in page.rows] == ["", "Party"]
    assert not page.rows[1].merge_button.isEnabled()
    results = _finished(page)
    page.continue_button.click()
    (events_out, names, skipped), = results
    assert events_out == [found[0], events.merge(found[1], found[2])]
    assert names == ["", "Party"] and skipped == set()


def test_split_splits_the_row(tmp_path, make_page):
    found = _three(tmp_path)
    page = make_page()
    page.load(tmp_path, found, 0)
    page.rows[1].name_edit.setText("Party")
    page.rows[1].split_button.click()
    assert _rows(page) == [
        ("Event 10-01-2025", "10 Jan 2025 – 12 Jan 2025", "3 files"),
        ("Event 20-02-2025", "20 Feb 2025", "2 files"),
        ("Event 20-02-2025 (2)", "20 Feb 2025", "2 files"),
        ("Event 20-02-2025 (3)", "20 Feb 2025", "1 file"),
    ]
    assert [r.name_edit.text() for r in page.rows] == ["", "Party", "", ""]
    page.rows[0].split_button.click()  # the first night and the rest
    assert [r.count_label.text() for r in page.rows][:2] == ["1 file", "2 files"]
    assert page.rows[1].range_label.text() == "11 Jan 2025 – 12 Jan 2025"
    results = _finished(page)
    page.continue_button.click()
    (events_out, names, _), = results
    assert [len(e.files) for e in events_out] == [1, 2, 2, 2, 1]
    assert names == ["", "", "Party", "", ""]


@pytest.mark.parametrize("first, second, merged", [
    (False, False, False), (True, False, True), (False, True, True), (True, True, True)])
def test_merge_skips_if_either_row_was_skipped(tmp_path, make_page, first, second, merged):
    page = make_page()
    page.load(tmp_path, _three(tmp_path), 0)
    page.rows[1].skip_check.setChecked(first)
    page.rows[2].skip_check.setChecked(second)
    page.rows[1].merge_button.click()
    assert page.rows[1].skip_check.isChecked() is merged


def test_split_keeps_the_skip_tick_on_both_halves(tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path), 0)
    page.rows[1].skip_check.setChecked(True)
    page.rows[1].split_button.click()
    assert [r.skip_check.isChecked() for r in page.rows] == [False, True, True, False]


def test_skip_tick(tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path), 0)
    results = _finished(page)
    page.rows[0].skip_check.setChecked(True)
    page.rows[2].skip_check.setChecked(True)
    page.continue_button.click()
    page.rows[2].skip_check.setChecked(False)
    page.continue_button.click()
    assert [skipped for _, _, skipped in results] == [{0, 2}, {0}]


def test_back_cancels(tmp_path, make_page):
    page = make_page()
    page.load(tmp_path, _three(tmp_path), 0)
    cancelled = []
    page.cancelled.connect(lambda: cancelled.append(True))
    page.back_button.click()
    assert cancelled == [True]


def test_late_thumbnail_for_old_rows_is_ignored(qtbot, tmp_path, make_page):
    page = make_page()
    found = _three(tmp_path)
    page.load(tmp_path, found, 0)
    page.load(tmp_path, found[:1], 0)  # before the first thumbnails arrive
    QThreadPool.globalInstance().waitForDone()
    qtbot.waitUntil(lambda: all(not label.pixmap().isNull()
                                for label in page.rows[0].thumb_labels))
    page._thumbnail_ready((-1, 0, 0, QImage(4, 4, QImage.Format.Format_RGB888)))  # stale
    assert len(page.rows) == 1


# ----- the main window -----------------------------------------------------

@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def make_window(qtbot, tmp_path, errors):
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
def photos(tmp_path, monkeypatch):
    """Ten photos of a Goa trip, ten of a party, and two loose ones; dated by name."""
    folder = tmp_path / "photos"
    times: dict[str, datetime | None] = {}
    for n in range(10):
        times[f"goa{n}.jpg"] = GOA + timedelta(minutes=20 * n)
        times[f"party{n}.jpg"] = PARTY + timedelta(minutes=5 * n)
    times["loose.jpg"] = GOA + timedelta(days=20)
    times["undated.jpg"] = None
    for name in times:
        _jpeg(folder / name)
    monkeypatch.setattr(events, "find_datetime", lambda path: times[path.name])
    return folder


def _files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                  if p.is_file() and ".photosort" not in p.parts)


def _start_events(qtbot, w, folder):
    w.folder_edit.setText(str(folder))
    w.mode_buttons["events"].click()
    qtbot.waitUntil(lambda: w.current_page() is not w.progress_page)


def test_events_end_to_end(qtbot, make_window, photos, errors):
    from photosort.journal import load_runs

    before = _files(photos)
    w = make_window()
    _start_events(qtbot, w, photos)
    assert w.current_page() is w.events_page
    assert w.progress_label.text() == "Dating files… 22 of 22"
    page = w.events_page
    assert [r.count_label.text() for r in page.rows] == ["10 files", "10 files"]
    page.rows[0].name_edit.setText("Goa trip")
    page.continue_button.click()
    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines() == [
        "20 files → 2 event folders", "2 files stay where they are"]
    assert w.go_button.isEnabled()

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 20 files." in w.done_label.text()
    assert _files(photos) == sorted(
        [f"Goa trip/goa{n}.jpg" for n in range(10)]
        + [f"Event 20-02-2025/party{n}.jpg" for n in range(10)]
        + ["loose.jpg", "undated.jpg"])
    assert [run.mode for run in load_runs(photos)] == ["events"]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _files(photos) == before
    assert not (photos / "Goa trip").exists()
    assert not (photos / "Event 20-02-2025").exists()
    assert errors == []


def test_back_from_summary_keeps_the_edits(qtbot, make_window, photos, errors):
    w = make_window()
    _start_events(qtbot, w, photos)
    page = w.events_page
    page.rows[0].name_edit.setText("Goa trip")
    page.rows[0].merge_button.click()
    page.continue_button.click()
    assert w.summary_label.text().splitlines() == [
        "20 files → 1 event folders", "2 files stay where they are"]

    w.back_button.click()
    assert w.current_page() is page
    assert [(r.name_edit.text(), r.count_label.text()) for r in page.rows] == [
        ("Goa trip", "20 files")]
    page.rows[0].skip_check.setChecked(True)
    page.continue_button.click()
    assert w.summary_label.text().splitlines() == [
        "Nothing to move.", "22 files stay where they are"]
    assert not w.go_button.isEnabled()
    assert errors == []


def test_settings_set_the_gap_and_minimum(qtbot, make_window, photos):
    w = make_window(Settings(event_gap_hours=41, event_min_files=2))
    _start_events(qtbot, w, photos)
    # The loose photo, 20 days after the trip, is alone: too few for an event.
    assert [r.count_label.text() for r in w.events_page.rows] == ["10 files", "10 files"]
    w.events_page.back_button.click()
    assert w.current_page() is w.home

    w.settings.event_gap_hours = 28 * 24  # the three are less than 28 days apart
    _start_events(qtbot, w, photos)
    assert [r.count_label.text() for r in w.events_page.rows] == ["21 files"]


def test_no_events_found(qtbot, make_window, tmp_path, errors):
    folder = tmp_path / "few"
    _jpeg(folder / "a.jpg")
    w = make_window()
    _start_events(qtbot, w, folder)
    assert w.current_page() is w.summary_page
    assert w.summary_label.text() == "No events found."
    assert not w.go_button.isEnabled()
    w.back_button.click()
    assert w.current_page() is w.home
    assert errors == []


def test_planning_error_goes_home(qtbot, make_window, photos, errors, monkeypatch):
    def unplugged(*args, **kwargs):
        raise OSError("drive gone")

    monkeypatch.setattr(events_flow, "plan_events", unplugged)
    w = make_window()
    _start_events(qtbot, w, photos)
    w.events_page.continue_button.click()
    assert w.current_page() is w.home
    assert errors == ["Could not plan the moves: drive gone"]
    w.back_button.click()  # a stale Back must not bring the page back
    assert w.current_page() is w.home


def test_start_forgets_the_last_events(qtbot, make_window, photos):
    w = make_window()
    _start_events(qtbot, w, photos)
    w.events_page.continue_button.click()
    assert w.back_to is not None
    w.back_button.click()
    w.events_page.back_button.click()  # home
    w.mode_buttons["date"].click()
    assert w.back_to is None and w.flow_state is None  # no old events for a later Back
