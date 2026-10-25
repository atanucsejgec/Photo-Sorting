"""Events mode: one folder per event, found from the gaps in time between files.

Every photo and video is dated (dates.find_datetime) and put in time order;
a gap of more than gap_hours hours starts a new run, and a run of at least
min_files files is an event. The owner names, merges, splits or skips the
events (ui.events_page); each one then goes to its own folder, by default
"Event 20-02-2025". Undated files, files in short runs and skipped events
stay where they are.
"""

import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from photosort.dates import find_datetime
from photosort.modes._scan import read_each
from photosort.naming import TargetAllocator, fit_name, fold
from photosort.plan import Move, Plan
from photosort.thresholds import FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES


@dataclass(frozen=True)
class Event:
    """Files taken close together: sorted by taken time, then by path."""

    files: tuple[str, ...]  # relative POSIX paths under the root
    times: tuple[datetime, ...]  # when each was taken, aligned with files

    @property
    def start(self) -> datetime:
        return self.times[0]

    @property
    def end(self) -> datetime:
        return self.times[-1]


def _event_of(pairs: list[tuple[datetime, str]]) -> Event:
    pairs = sorted(pairs)
    return Event(tuple(rel for _, rel in pairs), tuple(t for t, _ in pairs))


def find_events(
    root: Path,
    include_subfolders: bool,
    gap_hours: int,
    min_files: int,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[list[Event], int] | None:
    """Return the events under root in time order, and how many files are in none.

    A file with no taken time is in no event, and neither is a run of fewer
    than min_files files. progress(i, total) is called after each file is
    dated; cancel is checked before each one and once more at the end. None
    if cancelled.
    """
    found = read_each(root, include_subfolders, find_datetime, progress, cancel)
    if found is None:
        return None
    files, times = found
    dated = sorted((times[rel], rel) for rel in files if times[rel] is not None)
    gap = timedelta(hours=gap_hours)
    runs: list[list[tuple[datetime, str]]] = []
    for pair in dated:
        if runs and pair[0] - runs[-1][-1][0] <= gap:
            runs[-1].append(pair)
        else:
            runs.append([pair])
    events = [_event_of(run) for run in runs if len(run) >= min_files]
    return events, len(files) - sum(len(e.files) for e in events)


def merge(a: Event, b: Event) -> Event:
    """One event of a's and b's files, in time order."""
    return _event_of([*zip(a.times, a.files), *zip(b.times, b.files)])


def split(e: Event) -> tuple[Event, Event]:
    """e cut in two at its largest gap between neighbouring files (the
    earliest of equal ones). ValueError if e has fewer than 2 files or
    they were all taken at one moment."""
    gaps = [later - earlier for earlier, later in zip(e.times, e.times[1:])]
    if not gaps or max(gaps) == timedelta(0):
        raise ValueError("an event needs two files taken at different times to split")
    cut = gaps.index(max(gaps)) + 1
    return Event(e.files[:cut], e.times[:cut]), Event(e.files[cut:], e.times[cut:])


def default_names(events: list[Event]) -> list[str]:
    """"Event <start day>" for each event; a later repeat of a name gets " (2)",
    " (3)" and so on."""
    seen: Counter[str] = Counter()
    names = []
    for e in events:
        name = f"Event {e.start:%d-%m-%Y}"
        seen[name] += 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return names


def event_folder(name: str, default: str) -> str:
    """The folder for an event the owner named name: name made Windows-valid
    and cut to FOLDER_NAME_MAX characters and FOLDER_NAME_MAX_BYTES bytes.
    A name with no letter or digit (blank, "...") gives default instead."""
    if not any(char.isalnum() for char in name):
        name = default
    return fit_name(name.strip(), FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)


def plan_events(root: Path, events: list[Event], names: list[str], skipped: set[int],
                left_out: int) -> Plan:
    """Plan moving each event's files, flat, into its folder (see event_folder).

    names gives the name typed for each event; the events whose index is in
    skipped stay where they are, as do the left_out files in no event.
    Events given the same name share one folder.
    """
    root = Path(root)
    defaults = default_names(events)
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    folders: set[str] = set()
    stay = left_out
    unchanged = 0

    for i, e in enumerate(events):
        if i in skipped:
            stay += len(e.files)
            continue
        folder = event_folder(names[i], defaults[i])
        for rel in e.files:
            parent, _, name = rel.rpartition("/")
            # A folder on disk spelt another way matches by fold, as resolve_existing_dir does.
            if fold(parent) == fold(folder):
                unchanged += 1
                continue
            dst = allocator.allocate(f"{folder}/{name}")
            moves.append(Move(rel, dst))
            folders.add(fold(dst.rpartition("/")[0]))

    summary = []
    if moves:
        summary.append(f"{len(moves)} files → {len(folders)} event folders")
    if stay:
        summary.append(f"{stay} files stay where they are")
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan("events", moves, summary, unchanged)
