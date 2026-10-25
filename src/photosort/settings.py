"""The app's saved preferences, kept as JSON in the user's config dir.

Loading never fails: a missing or unreadable file gives defaults, and any
field that is missing or has the wrong type gives that field's default.
Unknown keys are ignored. Saving is atomic, so a crash mid-write never leaves
a half-written file.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import platformdirs

from photosort.naming import fold, safe_name
from photosort.thresholds import CONTENT_CATEGORIES_MAX

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
    face_people: list[str] = field(default_factory=list)  # ids ticked on the Face setup page
    content_categories: list[str] = field(
        default_factory=lambda: list(DEFAULT_CONTENT_CATEGORIES))


def _valid_categories(names) -> bool:
    """Whether names is a usable category list: 1 to CONTENT_CATEGORIES_MAX
    strings, none that safe_name leaves empty (it gives "_" then), and no two
    the same folder (by fold)."""
    return (isinstance(names, list)
            and 1 <= len(names) <= CONTENT_CATEGORIES_MAX
            and all(isinstance(n, str) and safe_name(n) != "_" for n in names)
            and len({fold(n) for n in names}) == len(names))


_VALID = {
    "include_subfolders": lambda v: isinstance(v, bool),
    "use_gpu": lambda v: isinstance(v, bool),
    "last_folder": lambda v: v is None or isinstance(v, str),
    "face_people": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v),
    "content_categories": _valid_categories,
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
