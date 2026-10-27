"""Date (all files): every file into year/month folders, by its modified or created date."""

import ctypes
import os
import sys
import threading
from datetime import date, datetime

import pytest
from fstree import tree

from photosort.executor import execute
from photosort.journal import RunJournal
from photosort.modes import MODE_SPECS, MODES, all_dates
from photosort.modes.all_dates import created_time, file_date, prepare_all_dates_plan
from photosort.undo import undo_run


def _at(day: date, hour: int = 12) -> float:
    """A timestamp on day, in local time."""
    return datetime(day.year, day.month, day.day, hour).timestamp()


def _touch(p, day: date | None = None, data: bytes = b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if day is not None:
        os.utime(p, (_at(day), _at(day)))
    return p


def _dsts(plan):
    return {m.src: m.dst for m in plan.moves}


def _contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


@pytest.fixture
def no_created(monkeypatch):
    """No file has a created date."""
    monkeypatch.setattr(all_dates, "created_time", lambda path: None)


def test_the_mode_is_listed_after_extension():
    keys = [key for key, _, _ in MODES]
    assert keys[keys.index("extension") + 1] == "all_dates"
    assert ("all_dates", "Date (all files)", True) in MODES
    spec = MODE_SPECS["all_dates"]
    assert spec.kind == "files"
    assert spec.warn_subfolders is True
    assert spec.asks_date_source is True
    assert not any(s.asks_date_source for key, s in MODE_SPECS.items() if key != "all_dates")


# ----- the file's own dates -----------------------------------------------------

def test_file_date_modified_is_the_local_date(tmp_path):
    path = _touch(tmp_path / "a.pdf")
    late = datetime(2024, 3, 5, 23, 30).timestamp()  # late evening, local time
    os.utime(path, (late, late))
    assert file_date(path, "modified") == (date(2024, 3, 5), False)


def test_file_date_created_uses_the_created_time(tmp_path, monkeypatch):
    path = _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    monkeypatch.setattr(all_dates, "created_time", lambda p: _at(date(2021, 7, 9)))
    assert file_date(path, "created") == (date(2021, 7, 9), False)
    assert file_date(path, "modified") == (date(2024, 3, 5), False)


def test_no_created_date_falls_back_to_modified(tmp_path, no_created):
    # Review Focus 3
    path = _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    assert file_date(path, "created") == (date(2024, 3, 5), True)


linux_statx = pytest.mark.skipif(
    not sys.platform.startswith("linux") or hasattr(os.stat_result, "st_birthtime"),
    reason="statx is used on Linux only, where os.stat has no st_birthtime")


@linux_statx
def test_a_failing_statx_falls_back_to_modified(tmp_path, monkeypatch):
    # Review Focus 3
    path = _touch(tmp_path / "a.pdf", date(2024, 3, 5))

    def fails(p):
        raise OSError(38, "Function not implemented")

    monkeypatch.setattr(all_dates, "_statx_btime", fails)
    assert created_time(path) is None
    assert file_date(path, "created") == (date(2024, 3, 5), True)


@linux_statx
def test_no_statx_in_the_c_library_gives_none_and_is_looked_up_once(tmp_path, monkeypatch):
    looked_up = []

    class OldLibc:  # glibc before 2.28: ctypes raises AttributeError for statx
        def __getattr__(self, name):
            raise AttributeError(name)

    def cdll(name, use_errno=False):
        looked_up.append(name)
        return OldLibc()

    monkeypatch.setattr(all_dates.ctypes, "CDLL", cdll)
    all_dates._libc_statx.cache_clear()
    try:
        assert created_time(_touch(tmp_path / "a.pdf")) is None
        assert created_time(_touch(tmp_path / "b.pdf")) is None
        assert file_date(tmp_path / "b.pdf", "created")[1] is True
        assert looked_up == [None]  # not again for every file
    finally:
        all_dates._libc_statx.cache_clear()


@linux_statx
def test_statx_raises_oserror_when_the_call_fails(tmp_path):
    if all_dates._libc_statx() is None:  # glibc before 2.28: _statx_btime gives None
        pytest.skip("the C library has no statx")
    with pytest.raises(FileNotFoundError):  # ENOENT, as os.stat would raise
        all_dates._statx_btime(tmp_path / "missing.pdf")


def test_the_statx_struct_has_the_kernel_layout():
    from photosort.modes.all_dates import _Statx

    assert ctypes.sizeof(_Statx) == 256
    assert _Statx.stx_btime.offset == 80
    assert _Statx.stx_mtime.offset == 112


def test_created_time_on_a_real_file_never_raises(tmp_path):
    path = _touch(tmp_path / "a.pdf")
    found = created_time(path)
    # ext4 and btrfs keep a created time; other filesystems may not.
    assert found is None or isinstance(found, float)
    if found is not None:
        assert abs(found - datetime.now().timestamp()) < 24 * 3600
    assert created_time(tmp_path / "missing.pdf") is None


# ----- the plan ----------------------------------------------------------------

def test_files_of_any_type_go_to_their_month(tmp_path, no_created):
    _touch(tmp_path / "app.apk", date(2024, 3, 5))
    _touch(tmp_path / "doc.pdf", date(2024, 3, 6))
    _touch(tmp_path / "README", date(2023, 12, 1))
    _touch(tmp_path / "sub" / "deep.exe", date(2024, 3, 7))
    plan = prepare_all_dates_plan(tmp_path, False, date_source="modified")
    assert plan.mode == "all_dates"
    assert _dsts(plan) == {
        "app.apk": "2024/03-March/app.apk",
        "doc.pdf": "2024/03-March/doc.pdf",
        "README": "2023/12-December/README",
    }
    assert plan.summary == ["3 files → 2 month folders, 0 day folders"]

    with_sub = prepare_all_dates_plan(tmp_path, True, date_source="modified")
    assert _dsts(with_sub)["sub/deep.exe"] == "2024/03-March/deep.exe"


def test_three_files_on_one_day_get_a_day_folder(tmp_path, no_created):
    for name in ("a.pdf", "b.zip", "c.txt"):
        _touch(tmp_path / name, date(2024, 3, 5))
    _touch(tmp_path / "d.txt", date(2024, 3, 6))
    plan = prepare_all_dates_plan(tmp_path, False, date_source="modified")
    assert _dsts(plan) == {
        "a.pdf": "2024/03-March/05-03-2024/a.pdf",
        "b.zip": "2024/03-March/05-03-2024/b.zip",
        "c.txt": "2024/03-March/05-03-2024/c.txt",
        "d.txt": "2024/03-March/d.txt",
    }
    assert plan.summary == ["4 files → 1 month folders, 1 day folders"]


def test_an_existing_day_folder_takes_that_days_files(tmp_path, no_created):
    (tmp_path / "2024" / "03-march" / "05-03-2024").mkdir(parents=True)
    _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    plan = prepare_all_dates_plan(tmp_path, False, date_source="modified")
    assert {k: v.casefold() for k, v in _dsts(plan).items()} == {
        "a.pdf": "2024/03-march/05-03-2024/a.pdf"}


def test_created_dates_and_the_fallback_count(tmp_path, monkeypatch):
    _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    _touch(tmp_path / "b.pdf", date(2024, 3, 5))
    _touch(tmp_path / "c.pdf", date(2024, 3, 5))
    created = {"a.pdf": _at(date(2020, 1, 2))}
    monkeypatch.setattr(all_dates, "created_time", lambda p: created.get(p.name))
    plan = prepare_all_dates_plan(tmp_path, False, date_source="created")
    assert _dsts(plan) == {
        "a.pdf": "2020/01-January/a.pdf",
        "b.pdf": "2024/03-March/b.pdf",
        "c.pdf": "2024/03-March/c.pdf",
    }
    assert plan.summary == ["3 files → 2 month folders, 0 day folders",
                            "2 files had no created date: modified date used"]


def test_modified_never_counts_a_fallback(tmp_path, no_created):
    _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    plan = prepare_all_dates_plan(tmp_path, False, date_source="modified")
    assert plan.summary == ["1 files → 1 month folders, 0 day folders"]


def test_photos_use_the_date_taken(tmp_path, make_jpeg, no_created):
    os.utime(make_jpeg(tmp_path / "taken.jpg", "2019:06:01 10:00:00"),
             (_at(date(2024, 3, 5)), _at(date(2024, 3, 5))))
    named = _touch(tmp_path / "IMG_20180402_101010.jpg", date(2024, 3, 5), b"not a jpeg")
    os.utime(make_jpeg(tmp_path / "undated.jpg"),
             (_at(date(2024, 3, 5)), _at(date(2024, 3, 5))))
    plan = prepare_all_dates_plan(tmp_path, False, date_source="created")
    assert _dsts(plan) == {
        "taken.jpg": "2019/06-June/taken.jpg",
        named.name: f"2018/04-April/{named.name}",
        "undated.jpg": "2024/03-March/undated.jpg",
    }
    assert plan.summary == [
        "3 files → 3 month folders, 0 day folders",
        "1 files had no created date: modified date used",
        "1 photos and videos had no taken date: file date used",
    ]


def test_already_in_place(tmp_path, no_created):
    _touch(tmp_path / "2024" / "03-March" / "a.pdf", date(2024, 3, 5))
    _touch(tmp_path / "b.pdf", date(2024, 3, 5))
    plan = prepare_all_dates_plan(tmp_path, True, date_source="modified")
    assert _dsts(plan) == {"b.pdf": "2024/03-March/b.pdf"}
    assert plan.unchanged == 1
    assert plan.summary == ["1 files → 1 month folders, 0 day folders", "1 already in place"]


def test_clutter_and_partial_downloads_are_left_alone(tmp_path, no_created):
    for name in ("desktop.ini", "Thumbs.db", ".hidden", "big.iso.part", "x.crdownload"):
        _touch(tmp_path / name, date(2024, 3, 5))
    _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    plan = prepare_all_dates_plan(tmp_path, False, date_source="modified")
    assert list(_dsts(plan)) == ["a.pdf"]


def test_nothing_to_sort(tmp_path):
    plan = prepare_all_dates_plan(tmp_path, False, date_source="modified")
    assert plan.moves == []
    assert plan.summary == ["0 files → 0 month folders, 0 day folders"]


def test_progress_and_cancel(tmp_path, no_created):
    for name in ("a.pdf", "b.pdf"):
        _touch(tmp_path / name, date(2024, 3, 5))
    calls = []
    prepare_all_dates_plan(tmp_path, False, date_source="modified",
                           progress=lambda i, t: calls.append((i, t)))
    assert calls == [(1, 2), (2, 2)]
    cancel = threading.Event()
    cancel.set()
    assert prepare_all_dates_plan(tmp_path, False, date_source="modified",
                                  cancel=cancel) is None


def _sort(root, include_subfolders=False):
    plan = prepare_all_dates_plan(root, include_subfolders, date_source="modified")
    journal = RunJournal.create(root, "all_dates", include_subfolders, len(plan.moves))
    result = execute(root, plan, journal)
    return plan, result, journal.run_id


def test_a_file_in_the_way_of_a_year_folder_keeps_that_years_files_in_place(
        tmp_path, no_created):
    # A root file called 2024 can't become the 2024 folder: 2024's files stay.
    _touch(tmp_path / "2024", date(2024, 3, 5), b"in the way")
    _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    _touch(tmp_path / "b.pdf", date(2023, 1, 1))
    before = _contents(tmp_path)
    plan, result, run_id = _sort(tmp_path)
    assert _dsts(plan) == {"b.pdf": "2023/01-January/b.pdf"}
    assert plan.unchanged == 0
    assert plan.summary == ["1 files → 1 month folders, 0 day folders",
                            "2 files stay: a file or link with their folder's name is in the way"]
    assert result.moved == 1 and not result.skipped
    undone = undo_run(tmp_path, run_id)
    assert undone.failed == [] and undone.renamed == []
    assert _contents(tmp_path) == before


def test_a_file_named_like_a_day_folder_never_takes_its_place(tmp_path, no_created):
    # "05-03-2024" is dated 6 March, so it goes loose in March, where 5 March's
    # day folder is made: it must not take that folder's name.
    _touch(tmp_path / "05-03-2024", date(2024, 3, 6), b"named like a day")
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        _touch(tmp_path / name, date(2024, 3, 5))
    before = _contents(tmp_path)
    plan, result, run_id = _sort(tmp_path)
    assert _dsts(plan)["05-03-2024"] == "2024/03-March/05-03-2024 (1)"
    assert result.moved == 4 and not result.skipped
    assert (tmp_path / "2024/03-March/05-03-2024").is_dir()
    undo_run(tmp_path, run_id)
    assert _contents(tmp_path) == before


def test_go_then_undo_restores_every_file(tmp_path, no_created):
    names = ["a.pdf", "b.zip", "c.txt", "README", "sub/d.exe", "IMG_20180402_101010.jpg"]
    for name in names:
        _touch(tmp_path / name, date(2024, 3, 5), name.encode())
    before = _contents(tmp_path)
    plan, result, run_id = _sort(tmp_path, include_subfolders=True)
    assert result.moved == 6
    undone = undo_run(tmp_path, run_id)
    assert undone.failed == [] and undone.renamed == []
    assert _contents(tmp_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [".photosort", "a.pdf", "b.zip", "c.txt", "README", "sub",
         "IMG_20180402_101010.jpg"])



def test_a_file_that_cannot_be_read_stays_and_is_counted(tmp_path, monkeypatch, no_created):
    _touch(tmp_path / "a.pdf", date(2024, 3, 5))
    _touch(tmp_path / "locked.pdf", date(2024, 3, 5))
    real = all_dates.file_date

    def unreadable(path, source):
        if path.name == "locked.pdf":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, source)

    monkeypatch.setattr(all_dates, "file_date", unreadable)
    plan = prepare_all_dates_plan(tmp_path, False, date_source="modified")
    assert _dsts(plan) == {"a.pdf": "2024/03-March/a.pdf"}
    assert plan.summary == ["1 files → 1 month folders, 0 day folders",
                            "1 files could not be read (they stay)"]


@pytest.mark.parametrize("link", ["2024", "2024/03-March", "2024/03-march"])
def test_nothing_moves_through_a_link_to_a_folder(tmp_path, link, no_created):
    root, outside = tmp_path / "root", tmp_path / "outside"
    _touch(outside / "kept.txt", data=b"k")
    _touch(root / "a.pdf", date(2024, 3, 5), b"a")
    _touch(root / "b.pdf", date(2023, 1, 1), b"b")
    (root / link).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, root / link, target_is_directory=True)
    before, outside_before = tree(root), tree(outside)
    plan, result, run_id = _sort(root)
    assert _dsts(plan) == {"b.pdf": "2023/01-January/b.pdf"}
    assert plan.summary == ["1 files → 1 month folders, 0 day folders",
                            "1 files stay: a file or link with their folder's name is in the way"]
    assert result.moved == 1 and not result.skipped
    assert tree(outside) == outside_before
    undone = undo_run(root, run_id)
    assert undone.renamed == [] and undone.failed == []
    assert tree(root) == before
    assert tree(outside) == outside_before
