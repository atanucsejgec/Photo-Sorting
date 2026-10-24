"""Work out the date (or date and time) a photo or video was taken.

Order: the file's own metadata, then a date in its filename. The file's
modified date is never used: copying a file changes it.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import hachoir.core.config as hachoir_config
import pillow_heif
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image

from photosort.media import kind

pillow_heif.register_heif_opener()
hachoir_config.quiet = True  # hachoir otherwise prints warnings for odd files

EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 36867
DATETIME_DIGITIZED = 36868
DATETIME = 306
EXIF_FORMAT = "%Y:%m:%d %H:%M:%S"

METADATA_MIN = date(1990, 1, 1)
FILENAME_MIN = date(2000, 1, 1)

_MS_EPOCH = re.compile(r"^(\d{13})(?!\d)")
# Lookahead so every start position is tried, not only non-overlapping ones.
_YMD = re.compile(r"(?<!\d)(?=(\d{4})[-_.]?(\d{2})[-_.]?(\d{2}))")
# HHMMSS straight after a date, with the same optional separators.
_HMS = re.compile(r"[-_.]?(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})")


@dataclass(frozen=True)
class DateResult:
    date: date | None
    source: Literal["metadata", "filename", "none"]


def date_from_filename(name: str, today: date | None = None) -> date | None:
    """Return the date written in a filename, or None.

    A name starting with exactly 13 digits is milliseconds since the Unix
    epoch (local time); outside 2000-01-01..today it gives None. Otherwise
    the first YYYY[-_.]?MM[-_.]?DD not preceded by a digit that is a real
    calendar date within 2000-01-01..today is used.
    """
    found = _read_filename(name, today or date.today())
    return found[0] if found else None


def datetime_from_filename(name: str, today: date | None = None) -> datetime | None:
    """Return the date and time written in a filename, or None.

    The date is the one date_from_filename finds; it needs a time
    (HHMMSS, optionally separated like the date) straight after it. A
    13-digit milliseconds epoch gives its full local time. A date with
    no time gives None.
    """
    found = _read_filename(name, today or date.today())
    return found[1] if found else None


def _read_filename(name: str, today: date) -> tuple[date, datetime | None] | None:
    """Return the filename's date, and its datetime when a time is written too."""
    m = _MS_EPOCH.match(name)
    if m:
        try:
            found = datetime.fromtimestamp(int(m.group(1)) / 1000)
        except (OverflowError, OSError, ValueError):
            return None
        return (found.date(), found) if FILENAME_MIN <= found.date() <= today else None

    for m in _YMD.finditer(name):
        try:
            found = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if FILENAME_MIN <= found <= today:
            return found, _time_after(name, m.end(3), found)
    return None


def _time_after(name: str, pos: int, day: date) -> datetime | None:
    t = _HMS.match(name, pos)
    if t is None:
        return None
    try:
        return datetime(day.year, day.month, day.day, int(t[1]), int(t[2]), int(t[3]))
    except ValueError:
        return None


def date_from_metadata(path: Path) -> datetime | None:
    """Return when the file was taken according to its metadata, or None.

    Photos use EXIF; videos use the container's creation date (UTC, returned
    in local time). Unreadable, junk or out-of-range values give None.
    """
    try:
        match kind(path):
            case "photo":
                found = _photo_datetime(Path(path))
            case "video":
                found = _video_datetime(Path(path))
            case _:
                return None
    except Exception:
        return None
    if found is None or not _in_metadata_range(found):
        return None
    return found


def find_date(path: Path) -> DateResult:
    """Return the file's date and where it came from."""
    found = date_from_metadata(path)
    if found is not None:
        return DateResult(found.date(), "metadata")
    from_name = date_from_filename(Path(path).name)
    if from_name is not None:
        return DateResult(from_name, "filename")
    return DateResult(None, "none")


def find_datetime(path: Path) -> datetime | None:
    """Return when the file was taken, as a naive local datetime.

    Metadata first (video times converted to local), then a date and time
    in the filename, else None.
    """
    found = date_from_metadata(path)
    if found is not None:
        return found.replace(tzinfo=None)
    return datetime_from_filename(Path(path).name)


def _in_metadata_range(value: datetime) -> bool:
    return METADATA_MIN <= value.date() <= date.today() + timedelta(days=1)


def _photo_datetime(path: Path) -> datetime | None:
    with Image.open(path) as img:
        exif = img.getexif()
        sub = exif.get_ifd(EXIF_IFD)
        candidates = [sub.get(DATETIME_ORIGINAL), sub.get(DATETIME_DIGITIZED), exif.get(DATETIME)]
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        try:
            value = datetime.strptime(raw.strip("\x00 "), EXIF_FORMAT)
        except ValueError:
            continue
        if _in_metadata_range(value):
            return value
    return None


def _video_datetime(path: Path) -> datetime | None:
    parser = createParser(str(path))
    if parser is None:
        return None
    with parser:
        metadata = extractMetadata(parser)
    if metadata is None:
        return None
    value = metadata.get("creation_date")
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()
