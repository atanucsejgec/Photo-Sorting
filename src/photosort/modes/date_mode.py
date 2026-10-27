"""Date mode: sort files into year/month folders, with a day folder for busy days.

A file goes to "YYYY/MM-Month"; when 3 or more files (photos and videos
together) share its date, or that day's folder already exists, it goes to
"YYYY/MM-Month/DD-MM-YYYY" instead. Files with no date go to "No Date".
"""

import os
import threading
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path

from photosort.dates import find_date
from photosort.naming import TargetAllocator, resolve_existing_dir
from photosort.plan import Move, Plan
from photosort.scanner import scan

MONTH_NAMES: list[str] = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

NO_DATE = "No Date"
DAY_FOLDER_MIN = 3


def date_target(d: date | None, day_count: int) -> str:
    """Return the folder (POSIX, relative to root) a file dated d belongs in."""
    if d is None:
        return NO_DATE
    month = f"{d.year:04d}/{d.month:02d}-{MONTH_NAMES[d.month - 1]}"
    if day_count >= DAY_FOLDER_MIN:
        return f"{month}/{d.day:02d}-{d.month:02d}-{d.year:04d}"
    return month


def day_folder_rule(root: Path, files: list[str],
                    dates: dict[str, date | None]) -> Callable[[date], bool]:
    """The day-folder rule for files (relative POSIX paths under root, dated
    by dates): a check of whether the files dated d go in a day folder. They
    do when 3 or more of files share d, or when d's day folder is already
    under root (an earlier run made it: that day's new files join it, any
    case)."""
    root = Path(root)
    day_counts = Counter(dates[rel] for rel in files if dates[rel] is not None)
    day_folder_exists: dict[date, bool] = {}

    def has_day_folder(d: date) -> bool:
        if d not in day_folder_exists:
            day = resolve_existing_dir(root, date_target(d, DAY_FOLDER_MIN))
            day_folder_exists[d] = os.path.isdir(root / day)
        return day_folder_exists[d]

    return lambda d: day_counts[d] >= DAY_FOLDER_MIN or has_day_folder(d)


def plan_date(root: Path, files: list[str], dates: dict[str, date | None]) -> Plan:
    """Plan the date-mode moves for files (relative POSIX paths under root)."""
    root = Path(root)
    in_day_folder_on = day_folder_rule(root, files, dates)
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    months: set[str] = set()
    days: set[str] = set()
    no_date = 0
    unchanged = 0

    for rel in files:
        d = dates[rel]
        in_day_folder = d is not None and in_day_folder_on(d)
        target = date_target(d, DAY_FOLDER_MIN if in_day_folder else 0)
        parent, _, name = rel.rpartition("/")
        if parent.casefold() == target.casefold():
            unchanged += 1
            continue
        moves.append(Move(rel, allocator.allocate(f"{target}/{name}")))
        if d is None:
            no_date += 1
        else:
            months.add(date_target(d, 0))
            if in_day_folder:
                days.add(target)

    summary = [f"{len(moves)} files → {len(months)} month folders, "
               f"{len(days)} day folders, {no_date} No Date"]
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan("date", moves, summary, unchanged)


def prepare_date_plan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root, date every file and plan the moves. None if cancelled."""
    root = Path(root)
    files = scan(root, include_subfolders)
    total = len(files)
    dates: dict[str, date | None] = {}
    for i, rel in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            return None
        dates[rel] = find_date(root / rel).date
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return plan_date(root, files, dates)
