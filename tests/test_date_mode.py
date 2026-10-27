import threading
from datetime import date

import pytest

from photosort.modes import MODES
from photosort.modes.date_mode import (
    MONTH_NAMES, date_target, plan_date, prepare_date_plan,
)
from photosort.plan import Plan


def _dsts(plan):
    return {m.src: m.dst for m in plan.moves}


def test_modes_list():
    assert [k for k, _, _ in MODES] == [
        "date", "face", "duplicates", "repeated", "notes", "type",
        "content", "bad", "events", "location", "camera", "no_people", "extension",
        "all_dates",
    ]
    assert MODES[0] == ("date", "Date", True)
    assert [k for k, _, available in MODES if available] == [
        "date", "face", "duplicates", "repeated", "notes", "type", "content", "bad",
        "events", "location", "camera", "no_people", "extension", "all_dates"]


def test_month_names():
    assert MONTH_NAMES[0] == "January"
    assert MONTH_NAMES[11] == "December"
    assert len(MONTH_NAMES) == 12


def test_date_target():
    assert date_target(None, 0) == "No Date"
    assert date_target(date(2025, 2, 20), 3) == "2025/02-February/20-02-2025"
    assert date_target(date(2025, 2, 20), 2) == "2025/02-February"


def test_plan_day_folder_and_loose(tmp_path):
    d20, d21 = date(2025, 2, 20), date(2025, 2, 21)
    dates = {"a.jpg": d20, "b.jpg": d20, "c.jpg": d20, "d.jpg": d21, "e.jpg": None}
    plan = plan_date(tmp_path, sorted(dates), dates)
    assert plan.mode == "date"
    assert _dsts(plan) == {
        "a.jpg": "2025/02-February/20-02-2025/a.jpg",
        "b.jpg": "2025/02-February/20-02-2025/b.jpg",
        "c.jpg": "2025/02-February/20-02-2025/c.jpg",
        "d.jpg": "2025/02-February/d.jpg",
        "e.jpg": "No Date/e.jpg",
    }
    assert plan.summary == ["5 files → 1 month folders, 1 day folders, 1 No Date"]
    assert plan.unchanged == 0


def test_photos_and_videos_counted_together(tmp_path):
    d = date(2024, 12, 1)
    dates = {"a.jpg": d, "b.jpg": d, "c.mp4": d}
    plan = plan_date(tmp_path, sorted(dates), dates)
    assert _dsts(plan) == {
        "a.jpg": "2024/12-December/01-12-2024/a.jpg",
        "b.jpg": "2024/12-December/01-12-2024/b.jpg",
        "c.mp4": "2024/12-December/01-12-2024/c.mp4",
    }


def test_same_name_from_different_subfolders(tmp_path):
    d = date(2025, 2, 20)
    dates = {"a/IMG_1.jpg": d, "b/img_1.JPG": d, "c.jpg": d}
    plan = plan_date(tmp_path, sorted(dates), dates)
    dsts = [m.dst for m in plan.moves]
    assert len(dsts) == 3
    assert len({x.casefold() for x in dsts}) == 3
    assert any(" (1)" in x for x in dsts)
    assert all(x.startswith("2025/02-February/20-02-2025/") for x in dsts)


def test_clash_with_file_on_disk(tmp_path):
    (tmp_path / "No Date").mkdir()
    (tmp_path / "No Date" / "X.JPG").write_bytes(b"x")
    plan = plan_date(tmp_path, ["x.jpg"], {"x.jpg": None})
    assert _dsts(plan) == {"x.jpg": "No Date/x (1).jpg"}


def test_already_in_place_not_moved(tmp_path):
    dates = {"2025/02-February/x.jpg": date(2025, 2, 15)}
    plan = plan_date(tmp_path, sorted(dates), dates)
    assert plan.moves == []
    assert plan.unchanged == 1
    assert plan.summary == [
        "0 files → 0 month folders, 0 day folders, 0 No Date",
        "1 already in place",
    ]


def test_already_in_place_is_case_insensitive_and_counts_toward_day(tmp_path):
    d = date(2025, 2, 20)
    dates = {
        "2025/02-february/20-02-2025/a.jpg": d,
        "b.jpg": d,
        "c.jpg": d,
        "no date/z.jpg": None,
    }
    plan = plan_date(tmp_path, sorted(dates), dates)
    assert _dsts(plan) == {
        "b.jpg": "2025/02-February/20-02-2025/b.jpg",
        "c.jpg": "2025/02-February/20-02-2025/c.jpg",
    }
    assert plan.unchanged == 2
    assert plan.summary == [
        "2 files → 1 month folders, 1 day folders, 0 No Date",
        "2 already in place",
    ]


def test_month_count_includes_day_folder_moves(tmp_path):
    d = date(2025, 3, 5)
    dates = {"a.jpg": d, "b.jpg": d, "c.jpg": d, "d.jpg": date(2025, 4, 1),
             "e.jpg": date(2025, 3, 9)}
    plan = plan_date(tmp_path, sorted(dates), dates)
    assert plan.summary == ["5 files → 2 month folders, 1 day folders, 0 No Date"]


def test_prepare_date_plan_end_to_end(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg", "2025:02:20 10:00:00")
    make_jpeg(tmp_path / "b.jpg", "2025:02:20 11:00:00")
    make_jpeg(tmp_path / "c.jpg", "2025:02:20 12:00:00")
    make_jpeg(tmp_path / "d.jpg", "2025:02:21 12:00:00")
    make_jpeg(tmp_path / "e.jpg")
    make_jpeg(tmp_path / "sub" / "f.jpg", "2025:02:21 12:00:00")

    calls = []
    plan = prepare_date_plan(tmp_path, False, progress=lambda i, t: calls.append((i, t)))
    assert isinstance(plan, Plan)
    assert _dsts(plan) == {
        "a.jpg": "2025/02-February/20-02-2025/a.jpg",
        "b.jpg": "2025/02-February/20-02-2025/b.jpg",
        "c.jpg": "2025/02-February/20-02-2025/c.jpg",
        "d.jpg": "2025/02-February/d.jpg",
        "e.jpg": "No Date/e.jpg",
    }
    assert calls == [(i, 5) for i in range(1, 6)]

    with_sub = prepare_date_plan(tmp_path, True)
    assert _dsts(with_sub)["sub/f.jpg"] == "2025/02-February/f.jpg"

    cancel = threading.Event()
    cancel.set()
    assert prepare_date_plan(tmp_path, False, cancel=cancel) is None


def test_existing_day_folder_takes_that_days_files(tmp_path, make_jpeg):
    # A re-run after 20 Feb's day folder was made: a newly added 20 Feb photo
    # joins it rather than sitting loose beside it in the month folder.
    day = tmp_path / "2025/02-February/20-02-2025"
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_jpeg(day / name, "2025:02:20 10:00:00")
    make_jpeg(tmp_path / "new.jpg", "2025:02:20 18:00:00")
    make_jpeg(tmp_path / "other.jpg", "2025:02:21 18:00:00")
    plan = prepare_date_plan(tmp_path, False)
    assert _dsts(plan) == {
        "new.jpg": "2025/02-February/20-02-2025/new.jpg",
        "other.jpg": "2025/02-February/other.jpg",
    }
    assert plan.summary == ["2 files → 1 month folders, 1 day folders, 0 No Date"]


@pytest.mark.usefixtures("case_sensitive_fs")
def test_existing_day_folder_found_case_insensitively(tmp_path):
    (tmp_path / "2025/02-february/20-02-2025").mkdir(parents=True)
    d = date(2025, 2, 20)
    plan = plan_date(tmp_path, ["new.jpg"], {"new.jpg": d})
    assert _dsts(plan) == {"new.jpg": "2025/02-february/20-02-2025/new.jpg"}
