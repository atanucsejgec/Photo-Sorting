"""The sorting modes: MODES lists them as (key, label, available); MODE_SPECS says how to run each.

A mode of kind "plan" prepares a Plan straight away. A mode of kind
"files" does too, but for every file, not only media; with warn_subfolders
the owner is first warned when subfolders are included. A mode of kind
"groups" finds Groups for the owner to review; the files not kept are then
set aside, flat, in set_aside_folder. A mode of kind "face" finds face
matches to ask the owner about, then plans (see modes.face_mode); it needs
the people picked and a face engine, so the main window runs it through
modes.face_mode, never through prepare. A mode of kind "bad" finds bad
photos (a list of Candidates, see modes.bad_photos) for the owner to tick;
the ticked ones are set aside, flat, in set_aside_folder. A mode of kind
"events" finds events (see modes.events) for the owner to name, merge,
split or skip, then plans. Modes of kind "content" and "notes" judge photos
with CLIP, ask about the unsure ones, then plan (see modes.content and
modes.notes); they need a CLIP engine, so the main window runs them too,
never through prepare. Every prepare function is called as prepare(root,
include_subfolders, progress=..., cancel=...) and returns None if
cancelled; the events one also takes gap_hours and min_files keywords,
from Settings. The modules of the "plan", "files", "groups", "bad" and
"events" modes are imported when such a mode first runs; the main window
imports the face, content and notes modules (numpy only, no AI runtime)
itself.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MODES: list[tuple[str, str, bool]] = [
    ("date", "Date", True),
    ("face", "Face", True),
    ("duplicates", "Duplicates", True),
    ("repeated", "Repeated shots", True),
    ("notes", "Notes & Pages", True),
    ("type", "Type / source", True),
    ("content", "Content", True),
    ("bad", "Bad photos", True),
    ("events", "Events", True),
    ("location", "Location", True),
    ("camera", "Camera", True),
    ("extension", "Extension", True),
]

Progress = Callable[[int, int], None] | None


@dataclass(frozen=True)
class ModeSpec:
    kind: Literal["plan", "files", "groups", "face", "bad", "events", "content", "notes"]
    # -> Plan (kinds "plan", "files"), list[Group], list[Candidate] (kind "bad") or
    # (list[Event], files in none) (kind "events"); None if cancelled
    prepare: Callable[..., object]
    set_aside_folder: str | None = None  # kinds "groups", "bad": where files set aside go
    # kinds "groups", "bad", "events": the summary when none is found
    none_found: str | None = None
    # kind "files": warn, before any scan, that files in subfolders may belong
    # to programs or projects (only when subfolders are included)
    warn_subfolders: bool = False


def _prepare_date(root: Path, include_subfolders: bool, progress: Progress = None,
                  cancel: threading.Event | None = None):
    from photosort.modes import date_mode

    return date_mode.prepare_date_plan(root, include_subfolders, progress=progress,
                                       cancel=cancel)


def _prepare_type(root: Path, include_subfolders: bool, progress: Progress = None,
                  cancel: threading.Event | None = None):
    from photosort.modes import type_mode

    return type_mode.prepare_type_plan(root, include_subfolders, progress=progress,
                                       cancel=cancel)


def _prepare_camera(root: Path, include_subfolders: bool, progress: Progress = None,
                    cancel: threading.Event | None = None):
    from photosort.modes import camera_mode

    return camera_mode.prepare_camera_plan(root, include_subfolders, progress=progress,
                                           cancel=cancel)


def _prepare_location(root: Path, include_subfolders: bool, progress: Progress = None,
                      cancel: threading.Event | None = None):
    from photosort.modes import location_mode

    return location_mode.prepare_location_plan(root, include_subfolders, progress=progress,
                                               cancel=cancel)


def _prepare_extension(root: Path, include_subfolders: bool, progress: Progress = None,
                       cancel: threading.Event | None = None):
    from photosort.modes import extension_mode

    return extension_mode.prepare_extension_plan(root, include_subfolders, progress=progress,
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


def _find_bad(root: Path, include_subfolders: bool, progress: Progress = None,
              cancel: threading.Event | None = None):
    from photosort.modes import bad_photos

    return bad_photos.find_bad_photos(root, include_subfolders, progress=progress,
                                      cancel=cancel)


def _find_events(root: Path, include_subfolders: bool, *, gap_hours: int, min_files: int,
                 progress: Progress = None, cancel: threading.Event | None = None):
    from photosort.modes import events

    return events.find_events(root, include_subfolders, gap_hours, min_files,
                              progress=progress, cancel=cancel)


def _find_faces(root: Path, include_subfolders: bool, progress: Progress = None,
                cancel: threading.Event | None = None):
    # Face mode also needs the people picked and a face engine; the main
    # window runs it through modes.face_mode directly.
    raise NotImplementedError("Face mode is run through modes.face_mode")


def _run_by_window(root: Path, include_subfolders: bool, progress: Progress = None,
                   cancel: threading.Event | None = None):
    # Content and Notes & Pages also need a CLIP engine (and Content the
    # categories); the main window runs them through modes.content and modes.notes.
    raise NotImplementedError("Content and Notes & Pages are run by the main window")


MODE_SPECS: dict[str, ModeSpec] = {
    "date": ModeSpec("plan", _prepare_date),
    "face": ModeSpec("face", _find_faces),
    "duplicates": ModeSpec("groups", _find_duplicates, "Duplicates", "No duplicates found."),
    "repeated": ModeSpec("groups", _find_repeated, "Repeated Shots",
                         "No repeated shots found."),
    "type": ModeSpec("plan", _prepare_type),
    "bad": ModeSpec("bad", _find_bad, "Bad Photos", "No bad photos found."),
    "events": ModeSpec("events", _find_events, None, "No events found."),
    "location": ModeSpec("plan", _prepare_location),
    "camera": ModeSpec("plan", _prepare_camera),
    "notes": ModeSpec("notes", _run_by_window),
    "content": ModeSpec("content", _run_by_window),
    "extension": ModeSpec("files", _prepare_extension, warn_subfolders=True),
}
