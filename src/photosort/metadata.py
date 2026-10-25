"""What a photo or video says about where it was taken, and with which camera.

Photos are read with Pillow (EXIF); videos with mp4meta. Anything
unreadable, junk or out of range gives None.
"""

import math
import re
import string
from pathlib import Path

import pillow_heif
from PIL import Image

from photosort.media import kind
from photosort.mp4meta import mp4_tags
from photosort.naming import fit_name, fold, safe_name
from photosort.thresholds import FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES

pillow_heif.register_heif_opener()

EXIF_MAKE = 271
EXIF_MODEL = 272
GPS_IFD = 0x8825
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4

# Video tags naming the camera, (make, model), in the order they are tried.
_VIDEO_CAMERA_TAGS = (
    ("com.apple.quicktime.make", "com.apple.quicktime.model"),
    ("com.android.manufacturer", "com.android.model"),
    ("©mak", "©mod"),
)
_VIDEO_LOCATION_TAGS = ("©xyz", "com.apple.quicktime.location.ISO6709")
# ISO 6709 in decimal degrees: ±DD.DDDD±DDD.DDDD, then altitude and/or "/".
_ISO6709 = re.compile(r"([-+]\d{2}(?:\.\d+)?)([-+]\d{3}(?:\.\d+)?)(?=[-+/]|$)")
_STRIP = string.whitespace + "\x00"


def camera_of(path: Path) -> tuple[str, str] | None:
    """(make, model) of the camera that took the file, or None.

    Photos use EXIF Make and Model. Videos use the Apple make/model keys,
    else the Android ones, else the ©mak/©mod user data. Each part is
    stripped of whitespace and NULs and may be empty, but not both.
    """
    try:
        match kind(path):
            case "photo":
                with Image.open(path) as img:
                    exif = img.getexif()
                    found = _clean(exif.get(EXIF_MAKE)), _clean(exif.get(EXIF_MODEL))
            case "video":
                tags = mp4_tags(Path(path))
                found = ("", "")
                for make_tag, model_tag in _VIDEO_CAMERA_TAGS:
                    found = _clean(tags.get(make_tag)), _clean(tags.get(model_tag))
                    if any(found):
                        break
            case _:
                return None
    except Exception:
        return None
    return found if any(found) else None


def gps_of(path: Path) -> tuple[float, float] | None:
    """(latitude, longitude) where the file was taken, in degrees, or None.

    Photos use the EXIF GPS block; videos an ISO 6709 location (©xyz, or
    Apple's location key). Out of range values, and exactly (0, 0) (what
    some phones store when they have no fix), give None.
    """
    try:
        match kind(path):
            case "photo":
                with Image.open(path) as img:
                    found = _exif_gps(img.getexif().get_ifd(GPS_IFD))
            case "video":
                tags = mp4_tags(Path(path))
                found = next((_iso6709(tags[t]) for t in _VIDEO_LOCATION_TAGS if t in tags),
                             None)
            case _:
                return None
    except Exception:
        return None
    if found is None:
        return None
    lat, lon = found
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if abs(lat) > 90 or abs(lon) > 180 or (lat, lon) == (0.0, 0.0):
        return None
    return lat, lon


def camera_folder(make: str, model: str) -> str:
    """The folder for a camera's files: "<make> <model>", or the model alone
    when it starts with the make ("Apple iPhone 12"), or whichever is given.

    The name is Windows-valid, fits FOLDER_NAME_MAX characters and
    FOLDER_NAME_MAX_BYTES bytes, and gets a "_" prefix if the scanner would
    skip it. With neither make nor model it is "" (no folder).
    """
    make, model = make.strip(_STRIP), model.strip(_STRIP)
    if make and fold(model).startswith(fold(make)):
        name = model
    else:
        name = " ".join(part for part in (make, model) if part)
    if not name:
        return ""
    return fit_name(safe_name(name), FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)


def _clean(value: object) -> str:
    return value.strip(_STRIP) if isinstance(value, str) else ""


def _exif_gps(gps: dict) -> tuple[float, float] | None:
    lat = _degrees(gps.get(GPS_LAT), gps.get(GPS_LAT_REF), "N", "S")
    lon = _degrees(gps.get(GPS_LON), gps.get(GPS_LON_REF), "E", "W")
    return None if lat is None or lon is None else (lat, lon)


def _degrees(value: object, ref: object, plus: str, minus: str) -> float | None:
    """An EXIF GPS coordinate (degrees, minutes, seconds as rationals) in
    signed degrees, or None."""
    sign = {plus: 1.0, minus: -1.0}.get(_clean(ref).upper())
    if sign is None or not isinstance(value, tuple) or not 1 <= len(value) <= 3:
        return None
    total = sum(float(part) / 60**n for n, part in enumerate(value))
    return sign * total


def _iso6709(text: str) -> tuple[float, float] | None:
    found = _ISO6709.match(text.strip(_STRIP))
    return (float(found[1]), float(found[2])) if found else None
