"""Duplicates mode: find exact copies, and resized or recompressed copies of photos.

Exact copies (photos and videos) share their bytes: only files whose byte
size collides are SHA-256 hashed. Resized copies of a photo have close
perceptual hashes and the same shape, and were not taken at different
moments (those are repeated shots, not duplicates).
"""

import hashlib
import os
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from photosort.dates import find_datetime
from photosort.groups import Candidate, Group, best_copy, close_pairs
from photosort.imageinfo import UnreadableImage, analyse, image_size
from photosort.media import kind
from photosort.scanner import scan
from photosort.thresholds import DUP_ASPECT_TOL, DUP_HASH_MAX, SAME_MOMENT_S

EXACT_NOTE = "Exact copy"
SMALLER_NOTE = "Smaller copy"
WHATSAPP_NOTE = "Smaller copy (WhatsApp)"
_WHATSAPP = re.compile(r"-WA\d+")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same_shape(a: Candidate, b: Candidate) -> bool:
    ra, rb = a.width / a.height, b.width / b.height
    return abs(ra - rb) <= DUP_ASPECT_TOL * max(ra, rb)


def _different_moments(a: Candidate, b: Candidate) -> bool:
    if a.taken is None or b.taken is None:
        return False
    return abs((a.taken - b.taken).total_seconds()) > SAME_MOMENT_S


def find_duplicate_groups(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Group] | None:
    """Return the groups of duplicate files under root, or None if cancelled.

    progress(i, total) is called after each file is looked at; cancel is
    checked before each one. Files that can't be read are skipped (an
    unreadable photo can still be an exact copy).
    """
    root = Path(root)
    files = scan(root, include_subfolders)
    sizes: dict[str, int] = {}
    for rel in files:
        try:
            sizes[rel] = os.stat(root / rel).st_size
        except OSError:
            continue  # gone or unreadable: not a duplicate of anything
    size_counts = Counter(sizes.values())

    total = len(files)
    digests: dict[str, str] = {}  # only for files whose byte size collides
    hashes: dict[str, int] = {}  # perceptual hashes of readable photos
    candidates: dict[str, Candidate] = {}  # files that may be in a group
    for i, rel in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            return None
        if rel in sizes:
            path = root / rel
            if size_counts[sizes[rel]] > 1:
                try:
                    digests[rel] = _sha256(path)
                except OSError:
                    pass
            file_kind = kind(rel)
            width = height = sharp = None
            if file_kind == "photo":
                measured = _measure_photo(path)
                if measured is not None:
                    width, height, hashes[rel], sharp = measured
            if rel in hashes or rel in digests:
                candidates[rel] = Candidate(
                    rel, file_kind, width, height, sizes[rel], find_datetime(path), sharp,
                )
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return _group(candidates, digests, hashes)


def _measure_photo(path: Path) -> tuple[int, int, int, float] | None:
    """Return (width, height, perceptual hash, sharpness), or None if unreadable."""
    try:
        width, height = image_size(path)
        phash, sharp = analyse(path)
    except UnreadableImage:
        return None
    return width, height, phash, sharp


def _group(
    candidates: dict[str, Candidate], digests: dict[str, str], hashes: dict[str, int]
) -> list[Group]:
    rels = sorted(candidates)
    index = {rel: n for n, rel in enumerate(rels)}
    parent = list(range(len(rels)))

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_digest: dict[str, list[int]] = defaultdict(list)
    for rel, digest in digests.items():
        if rel in index:
            by_digest[digest].append(index[rel])
    for same in by_digest.values():
        for n in same[1:]:
            union(same[0], n)

    hashed = [rel for rel in rels if rel in hashes]
    for a, b in close_pairs([hashes[rel] for rel in hashed], DUP_HASH_MAX):
        ca, cb = candidates[hashed[a]], candidates[hashed[b]]
        if _same_shape(ca, cb) and not _different_moments(ca, cb):
            union(index[ca.rel], index[cb.rel])

    components: dict[int, list[Candidate]] = defaultdict(list)
    for rel in rels:  # in path order, so members come out sorted
        components[find(index[rel])].append(candidates[rel])
    groups = [_make_group(members, digests) for members in components.values()
              if len(members) >= 2]
    groups.sort(key=lambda g: g.members[0].rel)
    return groups


def _make_group(members: list[Candidate], digests: dict[str, str]) -> Group:
    best = best_copy(members)
    shared = {digests.get(c.rel) for c in members}
    if len(shared) == 1 and None not in shared:
        members = [replace(c, note=EXACT_NOTE) for c in members]
        return Group("exact", members, {best.rel})
    noted = []
    for c in members:
        note = ""
        if c.rel != best.rel:
            if _WHATSAPP.search(c.rel.rpartition("/")[2]):
                note = WHATSAPP_NOTE
            elif _pixels(c) < _pixels(best):
                note = SMALLER_NOTE
        noted.append(replace(c, note=note))
    return Group("resized", noted, {best.rel})


def _pixels(c: Candidate) -> int:
    return (c.width or 0) * (c.height or 0)
