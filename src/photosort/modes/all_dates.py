"""Date (all files): every file, of any type, into year/month folders.

The layout is Date mode's (see date_mode): "YYYY/MM-Month", with a day
folder "YYYY/MM-Month/DD-MM-YYYY" when 3 or more files share a day or that
day's folder already exists. Photos and videos use the date they were taken
(find_date); every other file, and a photo or video with no taken date, uses
the date the owner chose: when it was last modified, or when it was created.
A file with no created date (common on Linux, and after a copy between
drives) uses its modified date. So every file gets a date: there is no
No Date folder.
"""

import ctypes
import os
import sys
import threading
from collections.abc import Callable
from datetime import date
from functools import cache
from pathlib import Path
from typing import Literal

from photosort.dates import find_date
from photosort.media import kind
from photosort.modes.date_mode import DAY_FOLDER_MIN, date_target, day_folder_rule
from photosort.naming import TargetAllocator, fold, real_dir, resolve_existing_dir
from photosort.plan import Move, Plan
from photosort.scanner import walk_all_files

DateSource = Literal["modified", "created"]


# ----- the created date --------------------------------------------------------
#
# Windows (and macOS) give a file's created time in os.stat's st_birthtime.
# Linux gives it only through the statx system call, which glibc 2.28 and
# later wrap as statx(); os.stat does not ask for it, so it is asked for here
# through ctypes.

_AT_FDCWD = -100  # a relative path is taken from the current folder, as os.stat does
_STATX_BTIME = 0x800  # the mask bit for the created ("birth") time


class _StatxTimestamp(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32),
                ("_reserved", ctypes.c_int32)]


class _Statx(ctypes.Structure):
    """struct statx, laid out as in the kernel's include/uapi/linux/stat.h
    (256 bytes; the fields after the timestamps are not needed)."""
    _fields_ = [
        ("stx_mask", ctypes.c_uint32),  # which fields the kernel filled in
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("_spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("_rest", ctypes.c_uint8 * 128),  # rdev, dev, mount id and spare room
    ]


@cache
def _libc_statx():
    """The C library's statx(); None when it has none (before glibc 2.28).
    Looked up once: the answer, None too, is kept for every later file."""
    try:
        statx = ctypes.CDLL(None, use_errno=True).statx
    except (OSError, AttributeError):
        return None
    statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint,
                      ctypes.POINTER(_Statx)]
    statx.restype = ctypes.c_int
    return statx


def _statx_btime(path: Path) -> float | None:
    """The created time statx gives for path; None when the filesystem keeps
    none or the C library has no statx. Raises OSError when the call fails."""
    statx = _libc_statx()
    if statx is None:
        return None
    found = _Statx()
    # Flags 0: follow a symlink and sync as stat does.
    if statx(_AT_FDCWD, os.fsencode(path), 0, _STATX_BTIME, ctypes.byref(found)):
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(path))
    if not found.stx_mask & _STATX_BTIME:
        return None
    return found.stx_btime.tv_sec + found.stx_btime.tv_nsec / 1e9


def created_time(path: Path) -> float | None:
    """When the file at path was created, as a timestamp; None when the
    system can't say (no such time kept, an old system, or any error)."""
    try:
        birth = getattr(os.stat(path), "st_birthtime", None)
    except OSError:
        return None
    if birth is not None:
        return float(birth)
    if not sys.platform.startswith("linux"):
        return None
    try:
        return _statx_btime(path)
    except (OSError, AttributeError):
        return None


def _local_date(timestamp: float) -> date | None:
    """timestamp's date in local time; None for one the system can't place."""
    try:
        return date.fromtimestamp(timestamp)
    except (OverflowError, ValueError, OSError):
        return None


def file_date(path: Path, source: DateSource) -> tuple[date, bool]:
    """The file's date by source, in local time, and whether it fell back to
    the modified date (asked for "created", but the file has none).

    Raises OSError if the file can't be read, ValueError if even its
    modified time can't be placed."""
    if source == "created":
        created = created_time(path)
        found = None if created is None else _local_date(created)
        if found is not None:
            return found, False
    modified = _local_date(os.stat(path).st_mtime)
    if modified is None:
        raise ValueError(f"no date for the modified time of {path}")
    return modified, source == "created"


# ----- the plan ----------------------------------------------------------------

def _prefixes(folder: str) -> list[str]:
    """folder ("a/b/c") and each folder above it: ["a", "a/b", "a/b/c"]."""
    parts = folder.split("/")
    return ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]


def plan_all_dates(root: Path, files: list[str], dates: dict[str, date], *,
                   no_created: int = 0, no_taken: int = 0, unreadable: int = 0) -> Plan:
    """Plan the moves for files (relative POSIX paths under root), dated by dates.

    The folders are Date mode's. A file already in its folder stays and
    counts as already in place. When something that is not a folder holds
    the name of a folder a file needs (a file called "2024", say), or a link
    does (a symlink or junction, even to a folder), that folder can't be
    made or used: the file stays, counted apart, so the run never stops
    half-way and never moves anything through a link. A file is never
    given the name of a folder this plan makes there (a file "05-03-2024"
    in March, beside 5 March's day folder): it gets another name.
    no_created, no_taken and unreadable are counts for the summary: files
    that had no created date, photos and videos with no taken date, and
    files that could not be read (left out of files: they stay).
    """
    root = Path(root)
    in_day_folder_on = day_folder_rule(root, files, dates)
    targets: dict[str, tuple[str, bool]] = {}
    for rel in files:
        in_day_folder = in_day_folder_on(dates[rel])
        targets[rel] = (date_target(dates[rel], DAY_FOLDER_MIN if in_day_folder else 0),
                        in_day_folder)
    made = {fold(folder) for target, _ in targets.values() for folder in _prefixes(target)}

    blocked: dict[str, bool] = {}

    def is_blocked(target: str) -> bool:
        if target not in blocked:
            blocked[target] = any(
                os.path.lexists(path) and not real_dir(path)
                for path in (root / resolve_existing_dir(root, folder)
                             for folder in _prefixes(target)))
        return blocked[target]

    allocator = TargetAllocator(root)
    moves: list[Move] = []
    months: set[str] = set()
    days: set[str] = set()
    unchanged = stuck = 0
    for rel in files:
        target, in_day_folder = targets[rel]
        parent, _, name = rel.rpartition("/")
        if fold(parent) == fold(target):
            unchanged += 1
            continue
        if is_blocked(target):
            stuck += 1
            continue
        dst = allocator.allocate(f"{target}/{name}")
        while fold(dst) in made:  # each call hands out the next free name
            dst = allocator.allocate(f"{target}/{name}")
        moves.append(Move(rel, dst))
        months.add(date_target(dates[rel], 0))
        if in_day_folder:
            days.add(target)

    summary = [f"{len(moves)} files → {len(months)} month folders, {len(days)} day folders"]
    if unchanged:
        summary.append(f"{unchanged} already in place")
    if stuck:
        summary.append(f"{stuck} files stay: a file or link with their folder's name "
                       "is in the way")
    if unreadable:
        summary.append(f"{unreadable} files could not be read (they stay)")
    if no_created:
        summary.append(f"{no_created} files had no created date: modified date used")
    if no_taken:
        summary.append(f"{no_taken} photos and videos had no taken date: file date used")
    return Plan("all_dates", moves, summary, unchanged)


def prepare_all_dates_plan(
    root: Path,
    include_subfolders: bool,
    *,
    date_source: DateSource,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root for every file, date each one and plan the moves. None if cancelled.

    A photo or video with no taken date uses date_source, as other files do.
    A file that can't be read (gone meanwhile, say) stays where it is, and
    is counted.
    progress(i, total) is called after each file is dated.
    """
    root = Path(root)
    files = sorted(walk_all_files(root, include_subfolders, cancel))
    if cancel is not None and cancel.is_set():
        return None
    total = len(files)
    dates: dict[str, date] = {}
    no_created = no_taken = unreadable = 0
    for i, rel in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            return None
        path = root / rel
        media = kind(rel) is not None
        taken = find_date(path).date if media else None
        if taken is not None:
            dates[rel] = taken
        else:
            try:
                dates[rel], fell_back = file_date(path, date_source)
            except (OSError, ValueError):
                unreadable += 1  # it stays where it is
            else:
                no_created += fell_back
                no_taken += media
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return plan_all_dates(root, [rel for rel in files if rel in dates], dates,
                          no_created=no_created, no_taken=no_taken, unreadable=unreadable)
