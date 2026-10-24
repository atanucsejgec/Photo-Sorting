"""Groups of look-alike files, the owner's keep choices, and set-aside plans.

The duplicate and repeated-shot finders both produce Groups; the review
screen shows them; plan_set_aside turns the owner's choices into moves into
one flat folder. Nothing is ever deleted, and every group keeps a file.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import numpy as np

from photosort.naming import TargetAllocator
from photosort.plan import Move, Plan
from photosort.thresholds import SHARP_BLURRY, SHARP_SLIGHTLY


@dataclass(frozen=True)
class Candidate:
    """One file in a group, with what the review screen shows about it."""

    rel: str  # POSIX path relative to the root
    kind: Literal["photo", "video"]
    width: int | None  # the right way up; None for videos and unreadable photos
    height: int | None
    size: int  # bytes
    taken: datetime | None  # from the file's metadata or name
    sharpness: float | None  # higher is sharper; None when not measured
    note: str = ""  # e.g. "Exact copy"


@dataclass
class Group:
    kind: Literal["exact", "resized", "repeated"]
    members: list[Candidate]
    suggested_keep: set[str]  # rels pre-ticked to keep


def _pixels(c: Candidate) -> int:
    return (c.width or 0) * (c.height or 0)


def _sharpness(c: Candidate) -> float:
    return c.sharpness if c.sharpness is not None else float("-inf")


def best_copy(members: Iterable[Candidate]) -> Candidate:
    """Return the copy to keep among duplicates.

    Most pixels, then a date inside the file, then the larger file, then the
    sharper, then the first in path order.
    """
    return min(
        sorted(members, key=lambda c: c.rel),
        key=lambda c: (-_pixels(c), c.taken is None, -c.size, -_sharpness(c)),
    )


def sharpest(members: Iterable[Candidate]) -> Candidate:
    """Return the sharpest member (unmeasured ones last; ties: first in path order)."""
    return min(sorted(members, key=lambda c: c.rel), key=lambda c: -_sharpness(c))


def sharpness_label(c: Candidate, group: Group) -> str:
    """Return "best", "sharp", "slightly blurry" or "blurry" relative to the group's sharpest.

    "" when c's sharpness was not measured.
    """
    if c.sharpness is None:
        return ""
    best = sharpest(group.members)
    if c == best:
        return "best"
    top = best.sharpness or 0.0
    ratio = c.sharpness / top if top > 0 else 1.0
    if ratio < SHARP_BLURRY:
        return "blurry"
    if ratio < SHARP_SLIGHTLY:
        return "slightly blurry"
    return "sharp"


# _POPCOUNT[b] is the number of set bits in byte b.
_POPCOUNT = np.array([bin(b).count("1") for b in range(256)], dtype=np.uint8)
_BLOCK_CELLS = 1 << 20  # hash comparisons per block (8 MiB of bytes at a time)


def close_pairs(hashes: Sequence[int], max_distance: int) -> list[tuple[int, int]]:
    """Return the index pairs (i, j), i < j, whose 64-bit hashes differ in <= max_distance bits.

    Compares every pair with numpy, a block of rows at a time so memory
    stays bounded for thousands of hashes. Pairs come back sorted.
    """
    values = np.array(hashes, dtype=np.uint64)
    n = len(values)
    pairs: list[tuple[int, int]] = []
    rows = max(1, _BLOCK_CELLS // max(n, 1))
    for start in range(0, n, rows):
        stop = min(start + rows, n)
        # Each row i is compared with columns start.. n; only j > i is kept.
        xor = values[start:stop, None] ^ values[None, start:]
        bits = _POPCOUNT[xor.view(np.uint8)].reshape(*xor.shape, 8).sum(axis=2)
        i, j = np.nonzero(bits <= max_distance)
        i += start
        j += start
        upper = j > i
        pairs.extend(zip(i[upper].tolist(), j[upper].tolist()))
    pairs.sort()
    return pairs


def plan_set_aside(
    root: Path, mode: str, folder: str, groups: list[Group], keep: dict[int, set[str]]
) -> Plan:
    """Plan moving the files the owner did not keep into folder, flat.

    keep maps a group's index to the rels to keep; a group with no entry
    (skipped) moves nothing. Raises ValueError if a group's keep set holds
    none of its members, so a group's last copy is never set aside.
    """
    allocator = TargetAllocator(Path(root))
    moves: list[Move] = []
    from_groups = 0
    for index, group in enumerate(groups):
        if index not in keep:
            continue
        kept = keep[index]
        if not any(c.rel in kept for c in group.members):
            raise ValueError(f"group {index}: at least one file must be kept")
        unkept = [c.rel for c in group.members if c.rel not in kept]
        for rel in unkept:
            moves.append(Move(rel, allocator.allocate(f"{folder}/{rel.rpartition('/')[2]}")))
        if unkept:
            from_groups += 1
    summary = [
        f"{len(moves)} files → {folder}/ (from {from_groups} groups)",
        f"Nothing is deleted. You can look through {folder}/ later.",
    ]
    return Plan(mode, moves, summary)
