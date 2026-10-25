"""Location mode: <Country>/<City>/ folders, from where each file was taken.

The GPS position of each photo and video (metadata.gps_of) is looked up in
an offline city list (geo.place_of). Files with no location stay where they
are, and the summary says how many before Go.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from photosort import geo
from photosort.metadata import gps_of
from photosort.modes._scan import read_each
from photosort.naming import TargetAllocator, fit_name, fold
from photosort.plan import Move, Plan
from photosort.thresholds import FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES


def place_folder(country: str, city: str) -> str:
    """The folder for a place: "<country>/<city>", the country alone with no city.

    Each part is Windows-valid, fits FOLDER_NAME_MAX characters and
    FOLDER_NAME_MAX_BYTES bytes, and gets a "_" prefix if the scanner would
    skip it. A part with no letter or digit counts as missing; with no
    country the folder is "" (no folder).
    """
    country, city = _part(country), _part(city)
    if not country:
        return ""
    return f"{country}/{city}" if city else country


def _part(name: str) -> str:
    if not any(char.isalnum() for char in name):
        return ""  # nothing usable, "..." or "::" say
    return fit_name(name.strip(), FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)


def plan_location(root: Path, files: list[str],
                  places: dict[str, tuple[str, str] | None]) -> Plan:
    """Plan the location-mode moves for files (relative POSIX paths under root).

    places gives each file's (country, city), None for a file with no location.
    """
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    countries: set[str] = set()
    cities: set[str] = set()
    no_location = 0
    unchanged = 0

    for rel in files:
        place = places[rel]
        folder = place_folder(*place) if place is not None else ""
        if not folder:
            no_location += 1
            continue
        parent, _, name = rel.rpartition("/")
        # A folder on disk spelt another way matches by fold, as resolve_existing_dir does.
        if fold(parent) == fold(folder):
            unchanged += 1
            continue
        dst = allocator.allocate(f"{folder}/{name}")
        moves.append(Move(rel, dst))
        target = fold(dst.rpartition("/")[0])
        countries.add(target.partition("/")[0])
        if "/" in target:
            cities.add(target)

    summary = []
    if moves:
        summary.append(f"{len(moves)} files → {len(countries)} countries, {len(cities)} cities")
    if no_location:
        summary.append(f"{no_location} files have no location and stay where they are")
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan("location", moves, summary, unchanged)


def prepare_location_plan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root, find where every file was taken and plan the moves.
    None if cancelled."""
    found = read_each(root, include_subfolders, _place_of, progress, cancel)
    if found is None:
        return None
    return plan_location(Path(root), *found)


def _place_of(path: Path) -> tuple[str, str] | None:
    """(country, city) where path was taken, None if it has no location."""
    position = gps_of(path)
    return geo.place_of(*position) if position is not None else None
