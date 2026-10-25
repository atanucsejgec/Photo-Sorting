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
from photosort.naming import TargetAllocator, fold
from photosort.plan import Move, Plan
from photosort.scanner import scan

CAMERA_PHOTOS = "Camera Photos"
SCREENSHOTS = "Screenshots"
WHATSAPP = "WhatsApp"
VIDEOS = "Videos"
OTHER = "Other"
FOLDERS: tuple[str, ...] = (CAMERA_PHOTOS, SCREENSHOTS, WHATSAPP, VIDEOS, OTHER)

_WHATSAPP_NAME = re.compile(r"-WA\d+", re.IGNORECASE)  # IMG-20250315-WA0036.jpg
_NAME_GAPS = re.compile(r"[\s_-]")


def source_of(path: Path) -> str:
    """The folder (one of FOLDERS) a photo or video belongs in.

    The first rule that matches wins:
    1. a name with "-WA" and a number (WhatsApp's naming): WhatsApp;
    2. a photo whose name says "screenshot" (any case, spaces, "_" or "-"
       ignored), or a readable .png with no camera make or model: Screenshots;
    3. any other video: Videos;
    4. a photo with a camera make or model: Camera Photos;
    5. anything else, including photos that can't be read: Other.
    """
    path = Path(path)
    media = kind(path)
    if _WHATSAPP_NAME.search(path.name):
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
    root = Path(root)
    files = scan(root, include_subfolders)
    total = len(files)
    sources: dict[str, str] = {}
    for i, rel in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            return None
        sources[rel] = source_of(root / rel)
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return plan_type(root, files, sources)
