"""The app's saved preferences, kept as JSON in the user's config dir.

Loading never fails: a missing or unreadable file gives defaults, and any
field that is missing, has the wrong type or is out of range gives that
field's default. Unknown keys are ignored. Saving is atomic, so a crash
mid-write never leaves a half-written file.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import platformdirs

from photosort.modes import MODES
from photosort.naming import fit_name, fold, safe_name
from photosort.scanner import skipped_folder
from photosort.thresholds import (
    CATEGORY_NAME_MAX,
    CONTENT_CATEGORIES_MAX,
    EVENT_GAP_HOURS,
    EVENT_GAP_HOURS_RANGE,
    EVENT_MIN_FILES,
    EVENT_MIN_FILES_RANGE,
    FOLDER_NAME_MAX,
    FOLDER_NAME_MAX_BYTES,
)

# The Content mode's categories until the owner edits them, in this order.
DEFAULT_CONTENT_CATEGORIES: tuple[str, ...] = (
    "Food", "Pets & Animals", "Nature & Landscapes", "Selfies & People",
    "Buildings & City", "Vehicles", "Events & Celebrations", "Text & Documents",
)


@dataclass
class Settings:
    include_subfolders: bool = False
    use_gpu: bool = True
    last_folder: str | None = None
    # Was the people last ticked for Face mode; no longer used (everyone now
    # comes up unticked), kept so that settings files that have it still load.
    face_people: list[str] = field(default_factory=list)
    event_gap_hours: int = EVENT_GAP_HOURS
    event_min_files: int = EVENT_MIN_FILES
    content_categories: list[str] = field(
        default_factory=lambda: list(DEFAULT_CONTENT_CATEGORIES))
    last_mode: str | None = None  # the MODES key last started; its tile has focus first
    # The date Date (all files) last sorted by, focused first when it asks again.
    file_date: str = "modified"


def _int_in(low: int, high: int):
    """A check that a value is an int (not a bool) from low to high."""
    return lambda v: type(v) is int and low <= v <= high


def _empty(name: str) -> bool:
    """Whether nothing is left of name as a folder name (safe_name gives "_" then)."""
    return safe_name(name) == "_"


def _folder_key(name: str) -> str:
    """The Content folder of category name, as folders are matched."""
    return fold(fit_name(name, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES))


def category_problem(name: str, others: list[str]) -> str | None:
    """What is wrong with name as a new or renamed category beside others
    (the rest of the list), for the owner; None if nothing is."""
    if not name.strip():
        return "Type a name."
    if not any(c.isalnum() for c in name):  # "_" or "?" would make a folder of nothing
        return "Use at least one letter or number."
    if len(name) > CATEGORY_NAME_MAX:
        return "That name is too long."
    if skipped_folder(safe_name(name)):
        return "Photo Sorter uses that name for its own folder. Pick another name."
    key = _folder_key(name)
    for other in others:
        if _folder_key(other) == key:
            return f"There's already a category called {other}."
    return None


def valid_category_list(names) -> bool:
    """Whether names (as loaded) is a usable category list: a list of 1 to
    CONTENT_CATEGORIES_MAX strings, none empty as a folder name, and no two
    going to the same folder.

    Looser than category_problem: a name the editor would refuse as too long
    or as one of the app's own folders still makes a valid folder (fit_name
    cuts it short or prefixes "_"), so a list holding one is kept.
    """
    if not (isinstance(names, list) and 1 <= len(names) <= CONTENT_CATEGORIES_MAX
            and all(isinstance(n, str) for n in names)):
        return False
    if any(_empty(n) for n in names):
        return False
    return len({_folder_key(n) for n in names}) == len(names)


_MODE_KEYS = frozenset(key for key, _, _ in MODES)
_FILE_DATES = frozenset({"modified", "created"})

_VALID = {
    "include_subfolders": lambda v: isinstance(v, bool),
    "use_gpu": lambda v: isinstance(v, bool),
    "last_folder": lambda v: v is None or isinstance(v, str),
    # An unknown key (from an old or edited file) gives None, so the Date tile.
    "last_mode": lambda v: v is None or (isinstance(v, str) and v in _MODE_KEYS),
    "face_people": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v),
    "event_gap_hours": _int_in(*EVENT_GAP_HOURS_RANGE),
    "event_min_files": _int_in(*EVENT_MIN_FILES_RANGE),
    "content_categories": valid_category_list,
    "file_date": lambda v: isinstance(v, str) and v in _FILE_DATES,
}


def default_path() -> Path:
    return Path(platformdirs.user_config_dir("photo-sorter")) / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    path = default_path() if path is None else Path(path)
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    kwargs = {
        f.name: data[f.name]
        for f in fields(Settings)
        if f.name in data and _VALID[f.name](data[f.name])
    }
    return Settings(**kwargs)


def save_settings(s: Settings, path: Path | None = None) -> None:
    """Write s atomically: temp file in the same dir, fsync, os.replace."""
    path = default_path() if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(asdict(s), indent=1).encode("ascii"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
