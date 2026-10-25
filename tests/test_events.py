import threading
from datetime import datetime, timedelta

import pytest

from photosort.modes import MODE_SPECS, events
from photosort.modes.events import (
    Event, default_names, find_events, merge, plan_events, split,
)
from photosort.naming import fits
from photosort.thresholds import FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES

T0 = datetime(2025, 2, 20, 9, 0, 0)


@pytest.fixture
def dated(tmp_path, monkeypatch):
    """dated({rel: datetime or None}) makes the files and gives each its time."""
    times: dict[str, datetime | None] = {}

    def fake_find_datetime(path):
        return times[path.relative_to(tmp_path).as_posix()]

    monkeypatch.setattr(events, "find_datetime", fake_find_datetime)

    def _make(table: dict[str, datetime | None]) -> None:
        for rel, when in table.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            times[rel] = when

    return _make


def _event(*hours: float, prefix: str = "f") -> Event:
    files = tuple(f"{prefix}{n:02d}.jpg" for n in range(len(hours)))
    return Event(files, tuple(T0 + timedelta(hours=h) for h in hours))


def _dsts(plan):
    return {m.src: m.dst for m in plan.moves}


# find_events

def test_runs_split_by_gaps_and_short_runs_left_out(tmp_path, dated):
    table = {}
    for n in range(12):  # within 2 hours
        table[f"a{n:02d}.jpg"] = T0 + timedelta(minutes=10 * n)
    last = T0 + timedelta(minutes=110)
    for n in range(3):  # 7 hours later: too few for an event
        table[f"b{n}.jpg"] = last + timedelta(hours=7, minutes=n)
    for n in range(10):  # another 7 hours later
        table[f"c{n}.mp4"] = last + timedelta(hours=14, minutes=n)
    dated(table)
    found, left_out = find_events(tmp_path, False, gap_hours=6, min_files=10)
    assert [len(e.files) for e in found] == [12, 10]
    assert left_out == 3
    assert found[0].files == tuple(f"a{n:02d}.jpg" for n in range(12))
    assert found[1].files == tuple(f"c{n}.mp4" for n in range(10))  # videos too
    assert found[0].start == T0 and found[0].end == last
    assert found[0].times == tuple(table[rel] for rel in found[0].files)


def test_a_gap_of_exactly_the_hours_is_no_new_event(tmp_path, dated):
    dated({"a.jpg": T0, "b.jpg": T0 + timedelta(hours=6),
           "c.jpg": T0 + timedelta(hours=12, seconds=1)})
    found, left_out = find_events(tmp_path, False, gap_hours=6, min_files=2)
    assert [e.files for e in found] == [("a.jpg", "b.jpg")]
    assert left_out == 1


def test_undated_files_are_left_out(tmp_path, dated):
    dated({"a.jpg": T0, "b.jpg": T0, "c.jpg": None, "d.mp4": None})
    found, left_out = find_events(tmp_path, False, gap_hours=6, min_files=2)
    assert [e.files for e in found] == [("a.jpg", "b.jpg")]
    assert left_out == 2


def test_files_with_one_timestamp_make_one_event_sorted_by_name(tmp_path, dated):
    dated({f"{n:02d}.jpg": T0 for n in reversed(range(20))})
    found, left_out = find_events(tmp_path, False, gap_hours=6, min_files=10)
    assert len(found) == 1 and left_out == 0
    assert found[0].files == tuple(f"{n:02d}.jpg" for n in range(20))


def test_subfolders_only_when_asked(tmp_path, dated):
    dated({"a.jpg": T0, "b.jpg": T0, "sub/c.jpg": T0})
    assert find_events(tmp_path, False, gap_hours=6, min_files=2)[0][0].files == (
        "a.jpg", "b.jpg")
    assert find_events(tmp_path, True, gap_hours=6, min_files=2)[0][0].files == (
        "a.jpg", "b.jpg", "sub/c.jpg")


def test_progress_and_cancel(tmp_path, dated):
    dated({"a.jpg": T0, "b.jpg": T0})
    ticks = []
    find_events(tmp_path, False, 6, 2, progress=lambda i, n: ticks.append((i, n)))
    assert ticks == [(1, 2), (2, 2)]
    cancel = threading.Event()
    cancel.set()
    assert find_events(tmp_path, False, 6, 2, cancel=cancel) is None


def test_mode_spec_passes_the_settings_on(tmp_path, dated):
    dated({"a.jpg": T0, "b.jpg": T0 + timedelta(hours=2)})
    spec = MODE_SPECS["events"]
    assert spec.kind == "events"
    assert spec.none_found == "No events found."
    assert spec.prepare(tmp_path, False, gap_hours=1, min_files=2) == ([], 2)
    assert len(spec.prepare(tmp_path, False, gap_hours=2, min_files=2)[0]) == 1


# split and merge

def test_split_at_the_largest_gap():
    first, second = split(_event(0, 1, 10, 11))
    assert first == Event(("f00.jpg", "f01.jpg"), (T0, T0 + timedelta(hours=1)))
    assert second.files == ("f02.jpg", "f03.jpg")
    assert second.start == T0 + timedelta(hours=10)


def test_split_takes_the_earliest_of_equal_gaps():
    first, second = split(_event(0, 5, 10))
    assert first.files == ("f00.jpg",) and second.files == ("f01.jpg", "f02.jpg")


@pytest.mark.parametrize("hours", [(0,), (3, 3, 3)])
def test_split_refuses_one_file_or_one_moment(hours):
    with pytest.raises(ValueError):
        split(_event(*hours))


def test_merge_keeps_the_time_order():
    a = _event(0, 10, prefix="a")
    b = _event(5, 20, prefix="b")
    merged = merge(b, a)
    assert merged.files == ("a00.jpg", "b00.jpg", "a01.jpg", "b01.jpg")
    assert merged.times == tuple(T0 + timedelta(hours=h) for h in (0, 5, 10, 20))
    assert merge(a, b) == merged


# names

def test_default_names_number_repeats():
    later = Event(("x.jpg",), (datetime(2025, 3, 1, 8),))
    assert default_names([_event(0), _event(5), later, _event(9)]) == [
        "Event 20-02-2025", "Event 20-02-2025 (2)", "Event 01-03-2025",
        "Event 20-02-2025 (3)"]


# plan_events

def _two_events(tmp_path):
    """On disk: a00, a01 (20 Feb) and b00, b01 (1 Mar), each pair an event."""
    a = _event(0, 1, prefix="a")
    b = Event(("b00.jpg", "b01.jpg"), (datetime(2025, 3, 1, 8), datetime(2025, 3, 1, 9)))
    for rel in (*a.files, *b.files):
        (tmp_path / rel).write_bytes(b"x")
    return [a, b]


def test_typed_names_are_cleaned_and_blank_gives_the_default(tmp_path):
    found = _two_events(tmp_path)
    plan = plan_events(tmp_path, found, ["Goa / trip.", "  "], set(), 0)
    assert plan.mode == "events"
    assert _dsts(plan) == {
        "a00.jpg": "Goa _ trip/a00.jpg", "a01.jpg": "Goa _ trip/a01.jpg",
        "b00.jpg": "Event 01-03-2025/b00.jpg", "b01.jpg": "Event 01-03-2025/b01.jpg"}
    assert plan.summary == ["4 files → 2 event folders"]


def test_a_name_with_no_letter_or_digit_gives_the_default(tmp_path):
    found = _two_events(tmp_path)
    plan = plan_events(tmp_path, found, ["...", "///"], set(), 0)
    assert {dst.split("/")[0] for dst in _dsts(plan).values()} == {
        "Event 20-02-2025", "Event 01-03-2025"}


def test_long_and_reserved_names(tmp_path):
    found = _two_events(tmp_path)
    plan = plan_events(tmp_path, found, ["ক" * 150, "CON"], set(), 0)
    folders = {dst.split("/")[0] for dst in _dsts(plan).values()}
    assert "_CON" in folders
    (long,) = folders - {"_CON"}
    assert fits(long, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)


def test_skipped_events_stay_and_left_out_is_counted(tmp_path):
    found = _two_events(tmp_path)
    plan = plan_events(tmp_path, found, ["", ""], {0}, 3)
    assert _dsts(plan) == {"b00.jpg": "Event 01-03-2025/b00.jpg",
                           "b01.jpg": "Event 01-03-2025/b01.jpg"}
    assert plan.summary == ["2 files → 1 event folders", "5 files stay where they are"]


def test_events_given_one_name_share_its_folder(tmp_path):
    found = _two_events(tmp_path)
    (tmp_path / "goa").mkdir()
    (tmp_path / "goa" / "a00.jpg").write_bytes(b"older")
    plan = plan_events(tmp_path, found, ["Goa", "GOA"], set(), 0)
    assert _dsts(plan) == {"a00.jpg": "goa/a00 (1).jpg", "a01.jpg": "goa/a01.jpg",
                           "b00.jpg": "goa/b00.jpg", "b01.jpg": "goa/b01.jpg"}
    assert plan.summary == ["4 files → 1 event folders"]


def test_rerun_leaves_files_in_their_event_folder(tmp_path, dated):
    dated({f"Goa trip/{n}.jpg": T0 + timedelta(minutes=n) for n in range(10)})
    found, left_out = find_events(tmp_path, True, gap_hours=6, min_files=10)
    plan = plan_events(tmp_path, found, ["goa TRIP"], set(), left_out)
    assert plan.moves == []
    assert plan.unchanged == 10
    assert plan.summary == ["10 already in place"]


def test_nothing_named_to_move(tmp_path):
    found = _two_events(tmp_path)
    plan = plan_events(tmp_path, found, ["", ""], {0, 1}, 0)
    assert plan.moves == []
    assert plan.summary == ["4 files stay where they are"]
