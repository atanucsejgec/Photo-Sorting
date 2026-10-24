"""The sorting modes: MODES lists them as (key, label, available); MODE_SPECS says how to run each.

A mode of kind "plan" prepares a Plan straight away. A mode of kind
"groups" finds Groups for the owner to review; the files not kept are then
set aside, flat, in set_aside_folder. Every prepare function is called as
prepare(root, include_subfolders, progress=..., cancel=...) and returns None
if cancelled. The mode modules are imported when a mode first runs.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MODES: list[tuple[str, str, bool]] = [
    ("date", "Date", True),
    ("face", "Face", False),
    ("duplicates", "Duplicates", True),
    ("repeated", "Repeated shots", True),
    ("notes", "Notes & Pages", False),
    ("type", "Type / source", False),
    ("content", "Content", False),
    ("bad", "Bad photos", False),
    ("events", "Events", False),
    ("location", "Location", False),
    ("camera", "Camera", False),
]

Progress = Callable[[int, int], None] | None


@dataclass(frozen=True)
class ModeSpec:
    kind: Literal["plan", "groups"]
    prepare: Callable[..., object]  # -> Plan (kind "plan") or list[Group]; None if cancelled
    set_aside_folder: str | None = None  # kind "groups": where unkept files go
    none_found: str | None = None  # kind "groups": the summary when no group is found


def _prepare_date(root: Path, include_subfolders: bool, progress: Progress = None,
                  cancel: threading.Event | None = None):
    from photosort.modes import date_mode

    return date_mode.prepare_date_plan(root, include_subfolders, progress=progress,
                                       cancel=cancel)


def _find_duplicates(root: Path, include_subfolders: bool, progress: Progress = None,
                     cancel: threading.Event | None = None):
    from photosort.modes import duplicates

    return duplicates.find_duplicate_groups(root, include_subfolders, progress=progress,
                                            cancel=cancel)


def _find_repeated(root: Path, include_subfolders: bool, progress: Progress = None,
                   cancel: threading.Event | None = None):
    from photosort.modes import repeated

    return repeated.find_repeated_groups(root, include_subfolders, progress=progress,
                                         cancel=cancel)


MODE_SPECS: dict[str, ModeSpec] = {
    "date": ModeSpec("plan", _prepare_date),
    "duplicates": ModeSpec("groups", _find_duplicates, "Duplicates", "No duplicates found."),
    "repeated": ModeSpec("groups", _find_repeated, "Repeated Shots",
                         "No repeated shots found."),
}
