"""Bad photos mode: find blurry, very dark, burnt-out and accidental shots.

Each photo's light and sharpness (quality.measure_quality) gives the reason
it is bad, if it is (quality.bad_reason). Videos are skipped. The owner
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
from photosort.naming import TargetAllocator, resolve_existing_dir
from photosort.plan import Move, Plan
from photosort.quality import bad_reason, measure_quality
from photosort.scanner import scan

BAD_FOLDER = "Bad Photos"


def find_bad_photos(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Candidate] | None:
    """Return the bad photos under root, sorted by path, or None if cancelled.

    Each one's note is the reason it is bad. progress(i, total) is called
    after each photo; cancel is checked before each one and once more at
    the end. Photos that can't be read are skipped. Photos measured on an
    earlier run are taken from root's analysis cache.
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


def _bad_photo(root: Path, rel: str, cache: AnalysisCache) -> Candidate | None:
    """The Candidate for rel if it is a bad photo; None if it is fine or unreadable."""
    path = root / rel
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
