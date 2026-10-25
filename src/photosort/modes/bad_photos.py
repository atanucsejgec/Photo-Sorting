"""Bad photos mode: find blurry, very dark, burnt-out and accidental shots.

Each photo's light and sharpness (quality.measure_quality) gives the reason
it is bad, if it is (quality.bad_reason). Only camera photos are judged:
videos, screenshots and graphics (stickers, saved chat screens) are skipped,
since a sharp white or near-black screen is not a bad photo. The owner
ticks the ones to set aside; those go, flat, to Bad Photos/. Nothing is
deleted, and the files already in Bad Photos/ are never scanned again.
"""

import os
import threading
from collections.abc import Callable
from pathlib import Path

from photosort.cache import AnalysisCache
from photosort.dates import find_datetime
from photosort.groups import Candidate
from photosort.imageinfo import UnreadableImage, image_size
from photosort.media import kind
from photosort.metadata import camera_of
from photosort.modes import MODE_SPECS
from photosort.naming import TargetAllocator, resolve_existing_dir
from photosort.plan import Move, Plan
from photosort.quality import bad_reason, measure_quality
from photosort.modes.type_mode import SCREENSHOTS, source_of
from photosort.scanner import scan

BAD_FOLDER: str = MODE_SPECS["bad"].set_aside_folder  # "Bad Photos"; spelt once, there
# Graphics formats: a photo in one of these counts only if it names its camera.
# (A GIF is not a photo at all, so it is never scanned.)
_GRAPHICS = frozenset({".png", ".webp"})


def find_bad_photos(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Candidate] | None:
    """Return the bad photos under root, sorted by path, or None if cancelled.

    Each one's note is the reason it is bad. progress(i, total) is called
    after each photo; cancel is checked before each one and once more at
    the end. Photos that can't be read are skipped, and so are those that
    aren't camera photos (see _judged). Photos measured on an earlier run are
    taken from root's analysis cache.
    """
    root = Path(root)
    photos = [rel for rel in scan(root, include_subfolders) if kind(rel) == "photo"]
    total = len(photos)
    bad: list[Candidate] = []
    with AnalysisCache(root) as cache:
        for i, rel in enumerate(photos, start=1):
            if cancel is not None and cancel.is_set():
                return None
            found = _bad_photo(root, rel, cache)
            if found is not None:
                bad.append(found)
            if progress is not None:
                progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return sorted(bad, key=lambda c: c.rel)


def _judged(path: Path) -> bool:
    """Whether path is a camera photo, so its light and sharpness mean something.

    Not when Type / source mode would call it a screenshot (type_mode.source_of),
    nor when it is a PNG or WebP with no camera make or model.
    """
    if source_of(path) == SCREENSHOTS:
        return False
    return path.suffix.lower() not in _GRAPHICS or camera_of(path) is not None


def _bad_photo(root: Path, rel: str, cache: AnalysisCache) -> Candidate | None:
    """The Candidate for rel if it is a bad photo; None if it is fine, unreadable
    or not a camera photo."""
    path = root / rel
    if not _judged(path):
        return None
    q = measure_quality(path, cache)
    reason = bad_reason(q) if q is not None else None
    if reason is None:
        return None
    try:
        size = os.stat(path).st_size
    except OSError:
        return None  # gone since it was measured
    try:
        width, height = image_size(path)
    except UnreadableImage:
        width = height = None
    return Candidate(rel, "photo", width, height, size, find_datetime(path), q.sharpness,
                     reason)


def plan_bad(root: Path, bad: list[Candidate], move: set[str]) -> Plan:
    """Plan moving the bad photos whose rel is in move into Bad Photos/, flat.

    An existing folder spelt another way ("bad photos") is used as it is.
    The others stay where they are.
    """
    root = Path(root)
    folder = resolve_existing_dir(root, BAD_FOLDER)
    allocator = TargetAllocator(root)
    moves = [Move(c.rel, allocator.allocate(f"{folder}/{c.rel.rpartition('/')[2]}"))
             for c in bad if c.rel in move]
    summary = [f"{len(moves)} photos → {folder}"]
    stay = len(bad) - len(moves)
    if stay:
        summary.append(f"{stay} photos stay where they are")
    return Plan("bad", moves, summary)
