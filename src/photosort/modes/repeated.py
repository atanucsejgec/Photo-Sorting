"""Repeated shots mode: find bursts of near-identical photos taken seconds apart.

Dated, readable photos are walked in the order they were taken. A photo
joins the burst of the photo before it when it was taken at most
REPEAT_WINDOW_S seconds later, looks almost the same (perceptual hashes
within REPEAT_HASH_MAX bits) and has the same orientation. Two photos that
are copies of one shot (groups.is_copy_pair: exact copies and same-second
re-saves) are a duplicates-mode case, so they never link.
"""

import os
import threading
from collections.abc import Callable
from pathlib import Path

from photosort.dates import find_datetime
from photosort.groups import Candidate, Group, is_copy_pair, sharpest
from photosort.media import kind
from photosort.modes._analysis import measure_photo
from photosort.scanner import scan
from photosort.thresholds import REPEAT_HASH_MAX, REPEAT_WINDOW_S


def find_repeated_groups(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Group] | None:
    """Return the groups of repeated shots under root, or None if cancelled.

    progress(i, total) is called after each photo is looked at; cancel is
    checked before each one. Videos, unreadable photos and photos with no
    taken time are skipped.
    """
    root = Path(root)
    photos = [rel for rel in scan(root, include_subfolders) if kind(rel) == "photo"]
    total = len(photos)
    shots: list[tuple[Candidate, int]] = []  # each with its perceptual hash
    for i, rel in enumerate(photos, start=1):
        if cancel is not None and cancel.is_set():
            return None
        shot = _look_at(root / rel, rel)
        if shot is not None:
            shots.append(shot)
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return _chains(shots)


def _look_at(path: Path, rel: str) -> tuple[Candidate, int] | None:
    """Return the photo as a Candidate with its perceptual hash, or None to skip it."""
    taken = find_datetime(path)
    if taken is None:
        return None  # undated: no way to tell it was taken seconds after another
    try:
        size = os.stat(path).st_size
    except OSError:
        return None
    measured = measure_photo(path)
    if measured is None:
        return None
    width, height, phash, sharp = measured
    return Candidate(rel, "photo", width, height, size, taken, sharp), phash


def _chains(shots: list[tuple[Candidate, int]]) -> list[Group]:
    shots = sorted(shots, key=lambda shot: (shot[0].taken, shot[0].rel))
    groups: list[Group] = []
    chain: list[Candidate] = []
    previous: tuple[Candidate, int] | None = None
    for shot in shots:
        if previous is not None and _follows(previous, shot):
            chain.append(shot[0])
        else:
            _close(chain, groups)
            chain = [shot[0]]
        previous = shot
    _close(chain, groups)
    return groups  # in time order: chains are found in the order they were taken


def _follows(previous: tuple[Candidate, int], shot: tuple[Candidate, int]) -> bool:
    """Whether shot is a repeat of the photo taken just before it."""
    (a, hash_a), (b, hash_b) = previous, shot
    if is_copy_pair(a, b, hash_a, hash_b):
        return False  # a copy, not a second shot
    return (
        (b.taken - a.taken).total_seconds() <= REPEAT_WINDOW_S
        and (hash_a ^ hash_b).bit_count() <= REPEAT_HASH_MAX
        and _orientation(a) == _orientation(b)
    )


def _orientation(c: Candidate) -> str:
    if c.width > c.height:
        return "landscape"
    if c.height > c.width:
        return "portrait"
    return "square"


def _close(chain: list[Candidate], groups: list[Group]) -> None:
    """Add chain to groups if it holds at least two shots."""
    if len(chain) >= 2:
        groups.append(Group("repeated", chain, {sharpest(chain).rel}))
