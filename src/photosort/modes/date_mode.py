"""Date mode: sort files into year/month folders, with a day folder for busy days.

A file goes to "YYYY/MM-Month"; when 3 or more files (photos and videos
together) share its date it goes to "YYYY/MM-Month/DD-MM-YYYY" instead. A day
folder that already exists for its date ("DD-MM-YYYY", or a named one,
"DD-MM-YYYY <name>") takes it whatever the count. A busy day (at least
BUSY_DAY_MIN_PHOTOS photos) the owner names goes to "DD-MM-YYYY <name>".
Files with no date go to "No Date".
"""

import os
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from photosort import media
from photosort.dates import find_date
from photosort.naming import TargetAllocator, fit_name, fold, resolve_existing_dir, safe_name
from photosort.plan import Move, Plan
from photosort.scanner import scan
from photosort.thresholds import BUSY_DAY_MIN_PHOTOS, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES

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


def _day_name(d: date) -> str:
    return f"{d.day:02d}-{d.month:02d}-{d.year:04d}"


def existing_day_folder(root: Path, d: date) -> str | None:
    """The day folder for d already under root (relative POSIX), if any.

    It is a folder in d's month folder (found case-insensitively) named
    "DD-MM-YYYY" or "DD-MM-YYYY <anything>", in any case; of several, the
    first by name (as names are matched, see naming.fold; names the same
    that way, by code point). None if there is none.
    """
    root = Path(root)
    month = resolve_existing_dir(root, date_target(d, 0))
    try:
        with os.scandir(root / month) as entries:
            names = sorted((entry.name for entry in entries
                            if _is_day_folder_name(entry.name, d) and entry.is_dir()),
                           key=lambda name: (fold(name), name))
    except OSError:
        return None
    return f"{month}/{names[0]}" if names else None


def _is_day_folder_name(name: str, d: date) -> bool:
    """Whether name is d's day folder's: "DD-MM-YYYY" or "DD-MM-YYYY <anything>", any case."""
    day = fold(_day_name(d))
    return fold(name) == day or fold(name).startswith(day + " ")


def in_day_folder_of(rel: str, d: date | None) -> bool:
    """Whether the file rel (relative POSIX) sits in a day folder of its own
    date d, named or not, in d's month folder (any case): it is in place
    there, never moved to another day folder of d (the owner's named folder
    is never emptied into another)."""
    if d is None:
        return False
    parent, _, _ = rel.rpartition("/")
    month, _, folder = parent.rpartition("/")
    return fold(month) == fold(date_target(d, 0)) and _is_day_folder_name(folder, d)


def day_folder_rule(root: Path, files: list[str],
                    dates: dict[str, date | None]) -> Callable[[date], str | None]:
    """The day-folder rule for files (relative POSIX paths under root, dated
    by dates): for a date d, the day folder (relative POSIX) the files dated
    d go in, or None if they go loose in the month folder. A day folder of
    d already under root (an earlier run made it, named or not, see
    existing_day_folder) takes them, whatever their count; otherwise they go
    in "DD-MM-YYYY" when 3 or more of files share d."""
    root = Path(root)
    day_counts = Counter(dates[rel] for rel in files if dates[rel] is not None)
    folders: dict[date, str | None] = {}

    def day_folder(d: date) -> str | None:
        if d not in folders:
            existing = existing_day_folder(root, d)
            if existing is not None:
                folders[d] = existing
            elif day_counts[d] >= DAY_FOLDER_MIN:
                folders[d] = date_target(d, DAY_FOLDER_MIN)
            else:
                folders[d] = None
        return folders[d]

    return day_folder


def _named_day_folder(d: date, name: str) -> str | None:
    """d's day folder named name ("YYYY/MM-Month/DD-MM-YYYY name"), the name
    cleaned as folder names are; None if it cleans to nothing."""
    name = name.strip()
    if not name.rstrip(". "):
        return None
    folder = fit_name(f"{_day_name(d)} {safe_name(name)}", FOLDER_NAME_MAX,
                      FOLDER_NAME_MAX_BYTES)
    return f"{date_target(d, 0)}/{folder}"


def plan_date(root: Path, files: list[str], dates: dict[str, date | None],
              names: dict[date, str] | None = None) -> Plan:
    """Plan the date-mode moves for files (relative POSIX paths under root).

    names gives busy days their names: such a day's files go to
    "YYYY/MM-Month/DD-MM-YYYY <name>", unless a day folder of that date
    already exists (it takes them, as for any day). A name that cleans to
    nothing leaves the day unnamed.
    """
    root = Path(root)
    day_folder_of = day_folder_rule(root, files, dates)
    named: dict[date, str] = {}
    for d, name in (names or {}).items():
        folder = _named_day_folder(d, name)
        if folder is not None and existing_day_folder(root, d) is None:
            named[d] = folder
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    months: set[str] = set()
    days: set[str] = set()
    named_days: set[date] = set()
    no_date = 0
    unchanged = 0

    for rel in files:
        d = dates[rel]
        day = None if d is None else named.get(d) or day_folder_of(d)
        target = day if day is not None else date_target(d, 0)
        parent, _, name = rel.rpartition("/")
        if parent.casefold() == target.casefold() or in_day_folder_of(rel, d):
            unchanged += 1
            continue
        moves.append(Move(rel, allocator.allocate(f"{target}/{name}")))
        if d is None:
            no_date += 1
        else:
            months.add(date_target(d, 0))
            if day is not None:
                days.add(fold(day))
            if d in named:
                named_days.add(d)

    summary = [f"{len(moves)} files → {len(months)} month folders, "
               f"{len(days)} day folders, {no_date} No Date"]
    if named_days:
        n = len(named_days)
        summary.append(f"{n} busy day{'' if n == 1 else 's'} named")
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan("date", moves, summary, unchanged)


@dataclass(frozen=True)
class DateScan:
    """A Date run's scan: the files (relative POSIX paths under root) and
    each one's date (None if it has none)."""

    root: Path
    files: list[str]
    dates: dict[str, date | None]


def busy_days(scan: DateScan) -> dict[date, list[str]]:
    """The busy days of scan, oldest first, each with all its files (photos
    and videos, in scan order): days with at least BUSY_DAY_MIN_PHOTOS
    photos (videos don't count) and no day folder under root yet (see
    existing_day_folder; that day's files join it, unasked)."""
    photos = Counter(d for rel, d in scan.dates.items()
                     if d is not None and media.kind(rel) == "photo")
    days: dict[date, list[str]] = {}
    for d in sorted(d for d, n in photos.items() if n >= BUSY_DAY_MIN_PHOTOS):
        if existing_day_folder(scan.root, d) is None:
            days[d] = [rel for rel in scan.files if scan.dates[rel] == d]
    return days


def prepare_date_scan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> DateScan | None:
    """Scan root and date every file. None if cancelled.

    progress(i, total) is called after each file is dated.
    """
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
    return DateScan(root, files, dates)


def prepare_date_plan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root, date every file and plan the moves, no busy day named
    (prepare_date_scan, then plan_date). None if cancelled."""
    found = prepare_date_scan(root, include_subfolders, progress=progress, cancel=cancel)
    if found is None:
        return None
    return plan_date(found.root, found.files, found.dates, names=None)
