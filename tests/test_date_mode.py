import threading
from datetime import date

import pytest

from photosort.executor import execute
from photosort.journal import RunJournal
from photosort.modes import MODE_SPECS, MODES
from photosort.modes.date_mode import (
    MONTH_NAMES, DateScan, busy_days, date_target, existing_day_folder, plan_date,
    prepare_date_plan, prepare_date_scan,
)
from photosort.plan import Plan
from photosort.thresholds import BUSY_DAY_MIN_PHOTOS
from photosort.undo import undo_run


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


# ----- busy days: named day folders -------------------------------------------

D20 = date(2025, 2, 20)
PURI = "2025/02-February/20-02-2025 Puri trip"


def _day(n_photos, n_videos=0, d=D20, prefix=""):
    files = [f"{prefix}p{i:02d}.jpg" for i in range(n_photos)]
    files += [f"{prefix}v{i:02d}.mp4" for i in range(n_videos)]
    return {rel: d for rel in files}


def test_busy_day_threshold():
    assert BUSY_DAY_MIN_PHOTOS == 11


def test_named_busy_day_folder(tmp_path):
    dates = _day(12)
    plan = plan_date(tmp_path, sorted(dates), dates, names={D20: "Puri trip"})
    assert _dsts(plan) == {rel: f"{PURI}/{rel}" for rel in dates}
    assert plan.summary == ["12 files → 1 month folders, 1 day folders, 0 No Date",
                            "1 busy day named"]


def test_named_busy_days_summary_counts_days(tmp_path):
    d21 = date(2025, 2, 21)
    dates = {**_day(11), **_day(11, d=d21, prefix="x")}
    plan = plan_date(tmp_path, sorted(dates), dates, names={D20: "A", d21: "B"})
    assert plan.summary[-1] == "2 busy days named"
    assert {m.dst.rsplit("/", 1)[0] for m in plan.moves} == {
        "2025/02-February/20-02-2025 A", "2025/02-February/21-02-2025 B"}


def test_busy_day_counts_photos_only(tmp_path):
    ten = DateScan(tmp_path, sorted(_day(10, 5)), _day(10, 5))
    assert busy_days(ten) == {}
    eleven = DateScan(tmp_path, sorted(_day(11, 2)), _day(11, 2))
    assert busy_days(eleven) == {D20: sorted(_day(11, 2))}


def test_busy_days_oldest_first_and_skip_undated(tmp_path):
    d1, d2 = date(2024, 1, 1), date(2025, 5, 5)
    dates = {**_day(11, d=d2, prefix="b"), **_day(11, d=d1, prefix="a"),
             "none.jpg": None, **_day(3, d=date(2025, 6, 1), prefix="c")}
    files = sorted(dates)
    days = busy_days(DateScan(tmp_path, files, dates))
    assert list(days) == [d1, d2]
    assert days[d1] == [f for f in files if f.startswith("a")]


def test_existing_named_day_folder_is_joined_and_not_asked(tmp_path):
    (tmp_path / "2025/02-February/20-02-2025 Puri").mkdir(parents=True)
    assert existing_day_folder(tmp_path, D20) == "2025/02-February/20-02-2025 Puri"
    plan = plan_date(tmp_path, ["new.jpg"], {"new.jpg": D20})
    assert _dsts(plan) == {"new.jpg": "2025/02-February/20-02-2025 Puri/new.jpg"}
    assert plan.summary == ["1 files → 1 month folders, 1 day folders, 0 No Date"]
    # a busy day with that folder is not asked, and a name given anyway is ignored
    dates = _day(12)
    assert busy_days(DateScan(tmp_path, sorted(dates), dates)) == {}
    plan = plan_date(tmp_path, sorted(dates), dates, names={D20: "Other"})
    assert {m.dst.rsplit("/", 1)[0] for m in plan.moves} == {
        "2025/02-February/20-02-2025 Puri"}
    assert all("named" not in line for line in plan.summary)


@pytest.mark.usefixtures("case_sensitive_fs")
def test_existing_day_folder_first_by_name_any_case(tmp_path):
    month = tmp_path / "2025/02-february"
    (month / "20-02-2025 Zoo").mkdir(parents=True)
    (month / "20-02-2025 Beach").mkdir()
    (month / "20-02-2025x").mkdir()
    (month / "21-02-2025").mkdir()
    (month / "20-02-2025 Abc.txt").write_bytes(b"not a folder")
    assert existing_day_folder(tmp_path, D20) == "2025/02-february/20-02-2025 Beach"
    (month / "20-02-2025").mkdir()
    assert existing_day_folder(tmp_path, D20) == "2025/02-february/20-02-2025"
    assert existing_day_folder(tmp_path, date(2025, 2, 22)) is None
    assert existing_day_folder(tmp_path, date(2025, 3, 20)) is None


def test_existing_prefix_without_space_is_not_a_day_folder(tmp_path):
    (tmp_path / "2025/02-February/20-02-2025x").mkdir(parents=True)
    assert existing_day_folder(tmp_path, D20) is None
    plan = plan_date(tmp_path, ["new.jpg"], {"new.jpg": D20})
    assert _dsts(plan) == {"new.jpg": "2025/02-February/new.jpg"}


def test_busy_day_name_is_cleaned(tmp_path):
    dates = _day(11)

    def folders(name):
        plan = plan_date(tmp_path, sorted(dates), dates, names={D20: name})
        return {m.dst.rsplit("/", 1)[0] for m in plan.moves}, plan.summary

    assert folders("a/b")[0] == {"2025/02-February/20-02-2025 a_b"}
    assert folders("  Puri trip  ")[0] == {PURI}
    for empty in (" .. ", "   ", ""):
        got, summary = folders(empty)
        assert got == {"2025/02-February/20-02-2025"}
        assert all("named" not in line for line in summary)
    long_folder = next(iter(folders("x" * 300)[0])).rsplit("/", 1)[1]
    assert long_folder.startswith("20-02-2025 xxx")
    assert len(long_folder) == 100


def test_unnamed_busy_day_unchanged(tmp_path):
    dates = _day(12)
    expected = {rel: f"2025/02-February/20-02-2025/{rel}" for rel in dates}
    for names in (None, {}, {date(2025, 1, 1): "Elsewhere"}):
        plan = plan_date(tmp_path, sorted(dates), dates, names=names)
        assert _dsts(plan) == expected
        assert plan.summary == ["12 files → 1 month folders, 1 day folders, 0 No Date"]


def test_date_undo_removes_named_folder(tmp_path, make_jpeg):
    for i in range(12):
        make_jpeg(tmp_path / f"p{i:02d}.jpg", f"2025:02:20 10:{i:02d}:00")
    scan = prepare_date_scan(tmp_path, False)
    names = {d: "Puri trip" for d in busy_days(scan)}
    assert list(names) == [D20]
    plan = plan_date(scan.root, scan.files, scan.dates, names=names)
    journal = RunJournal.create(tmp_path, "date", False, len(plan.moves))
    execute(tmp_path, plan, journal)
    assert len(list((tmp_path / PURI).iterdir())) == 12
    undo_run(tmp_path, journal.run_id)
    assert not (tmp_path / "2025").exists()
    assert sorted(p.name for p in tmp_path.glob("*.jpg")) == sorted(scan.files)


def test_prepare_date_scan(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg", "2025:02:20 10:00:00")
    make_jpeg(tmp_path / "b.jpg")
    make_jpeg(tmp_path / "sub" / "c.jpg", "2025:02:21 10:00:00")
    calls = []
    scan = prepare_date_scan(tmp_path, False, progress=lambda i, t: calls.append((i, t)))
    assert scan == DateScan(tmp_path, ["a.jpg", "b.jpg"], {"a.jpg": D20, "b.jpg": None})
    assert calls == [(1, 2), (2, 2)]
    assert prepare_date_scan(tmp_path, True).dates["sub/c.jpg"] == date(2025, 2, 21)


def test_prepare_date_scan_cancel(tmp_path, make_jpeg):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_jpeg(tmp_path / name, "2025:02:20 10:00:00")
    cancel = threading.Event()
    cancel.set()
    assert prepare_date_scan(tmp_path, False, cancel=cancel) is None

    mid = threading.Event()  # cancelled after the first file
    assert prepare_date_scan(tmp_path, False, progress=lambda i, t: mid.set(),
                             cancel=mid) is None


def test_date_mode_prepare_scans(tmp_path, make_jpeg):
    # Date's prepare scans; its flow asks about the busy days, then plans.
    for i in range(12):
        make_jpeg(tmp_path / f"p{i:02d}.jpg", "2025:02:20 10:00:00")
    spec = MODE_SPECS["date"]
    assert spec.kind == "date"
    found = spec.prepare(tmp_path, False)
    assert isinstance(found, DateScan)
    assert list(busy_days(found)) == [D20]
    cancel = threading.Event()
    cancel.set()
    assert spec.prepare(tmp_path, False, cancel=cancel) is None


def test_a_file_in_any_day_folder_of_its_date_stays(tmp_path):
    # Two day folders for one date: neither is emptied into the other.
    month = tmp_path / "2025/02-February"
    (month / "20-02-2025").mkdir(parents=True)
    (month / "20-02-2025 Puri").mkdir()
    dates = {"2025/02-February/20-02-2025 Puri/a.jpg": D20,
             "2025/02-february/20-02-2025 puri/b.jpg": D20,  # any case
             "2025/02-February/20-02-2025/c.jpg": D20,
             "2025/02-February/20-02-2025 Puri/old.jpg": date(2025, 2, 21),
             "Other/20-02-2025 Puri/d.jpg": D20,
             "new.jpg": D20}
    plan = plan_date(tmp_path, sorted(dates), dates)
    assert _dsts(plan) == {
        "2025/02-February/20-02-2025 Puri/old.jpg": "2025/02-February/old.jpg",
        "Other/20-02-2025 Puri/d.jpg": "2025/02-February/20-02-2025/d.jpg",
        "new.jpg": "2025/02-February/20-02-2025/new.jpg"}
    assert plan.unchanged == 3
    assert plan.summary[-1] == "3 already in place"


@pytest.mark.usefixtures("case_sensitive_fs")
def test_existing_day_folder_first_by_folded_name(tmp_path):
    month = tmp_path / "2025/02-February"
    (month / "20-02-2025 Zoo").mkdir(parents=True)
    (month / "20-02-2025 beach").mkdir()  # "Z" < "b" by code point; b before z by name
    assert existing_day_folder(tmp_path, D20) == "2025/02-February/20-02-2025 beach"
    (month / "20-02-2025 Beach").mkdir()  # the same folded: by code point
    assert existing_day_folder(tmp_path, D20) == "2025/02-February/20-02-2025 Beach"
