"""Work out the date a photo or video was taken.

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


@dataclass(frozen=True)
class DateResult:
    date: date | None
    source: Literal["metadata", "filename", "none"]


def date_from_filename(name: str, today: date | None = None) -> date | None:
    """Return the date written in a filename, or None.

    A name starting with exactly 13 digits is milliseconds since the Unix
    epoch (local time). Otherwise the first YYYY[-_.]?MM[-_.]?DD not preceded
    by a digit that is a real calendar date is used. Dates outside
    2000-01-01..today give None.
    """
    today = today or date.today()

    m = _MS_EPOCH.match(name)
    if m:
        try:
            found = datetime.fromtimestamp(int(m.group(1)) / 1000).date()
        except (OverflowError, OSError, ValueError):
            return None
        return found if FILENAME_MIN <= found <= today else None

    for m in _YMD.finditer(name):
        try:
            found = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        return found if FILENAME_MIN <= found <= today else None
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
