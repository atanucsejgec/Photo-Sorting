"""Type / source mode: camera photos, screenshots, WhatsApp, videos, the rest.

source_of decides each file's folder from its name, its camera info (EXIF
or video tags) and whether the photo can be read at all.
"""

import re
import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from photosort.imageinfo import UnreadableImage, image_size
from photosort.media import kind
from photosort.metadata import camera_of
from photosort.modes._scan import read_each
from photosort.naming import TargetAllocator, fold
from photosort.plan import Move, Plan

CAMERA_PHOTOS = "Camera Photos"
SCREENSHOTS = "Screenshots"
WHATSAPP = "WhatsApp"
VIDEOS = "Videos"
OTHER = "Other"
FOLDERS: tuple[str, ...] = (CAMERA_PHOTOS, SCREENSHOTS, WHATSAPP, VIDEOS, OTHER)

# WhatsApp's own names: IMG-20250315-WA0036.jpg (and " (1)" copies), or
# "WhatsApp Image 2025-03-15 at 10.15.00.jpeg" as WhatsApp Desktop and iPhones save them.
_WHATSAPP_NAME = re.compile(r"(IMG|VID|AUD|PTT|STK|DOC)-\d{8}-WA\d+", re.IGNORECASE)
_NAME_GAPS = re.compile(r"[\s_-]")


def source_of(path: Path) -> str:
    """The folder (one of FOLDERS) a photo or video belongs in.

    The first rule that matches wins:
    1. a name in WhatsApp's style, "IMG-20250315-WA0036..." (IMG, VID, AUD,
       PTT, STK or DOC, a date, "-WA" and a number) or starting "WhatsApp"
       (any case): WhatsApp;
    2. a photo whose name says "screenshot" (any case, spaces, "_" or "-"
       ignored), or a readable .png with no camera make or model: Screenshots;
    3. any other video: Videos;
    4. a photo with a camera make or model: Camera Photos;
    5. anything else, including photos that can't be read: Other.
    """
    path = Path(path)
    media = kind(path)
    if _WHATSAPP_NAME.match(path.name) or path.name.casefold().startswith("whatsapp"):
        return WHATSAPP
    if media == "photo" and "screenshot" in _NAME_GAPS.sub("", path.name.casefold()):
        return SCREENSHOTS
    if media == "video":
        return VIDEOS
    if media != "photo":
        return OTHER
    if camera_of(path) is not None:
        return CAMERA_PHOTOS
    if path.suffix.lower() == ".png" and _readable(path):
        return SCREENSHOTS
    return OTHER


def _readable(path: Path) -> bool:
    try:
        image_size(path)
    except UnreadableImage:
        return False
    return True


def plan_type(root: Path, files: list[str], sources: dict[str, str]) -> Plan:
    """Plan the type-mode moves for files (relative POSIX paths under root).

    sources gives each file's folder (see source_of).
    """
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    counts: Counter[str] = Counter()
    unchanged = 0

    for rel in files:
        folder = sources[rel]
        parent, _, name = rel.rpartition("/")
        # A folder on disk spelt another way matches by fold, as resolve_existing_dir does.
        if fold(parent) == fold(folder):
            unchanged += 1
            continue
        moves.append(Move(rel, allocator.allocate(f"{folder}/{name}")))
        counts[folder] += 1

    summary = []
    if moves:
        summary.append(f"{len(moves)} files → " + ", ".join(
            f"{folder} {counts[folder]}" for folder in FOLDERS if counts[folder]))
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan("type", moves, summary, unchanged)


def prepare_type_plan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root, sort every file by type and source and plan the moves.
    None if cancelled."""
    found = read_each(root, include_subfolders, source_of, progress, cancel)
    if found is None:
        return None
    return plan_type(Path(root), *found)
