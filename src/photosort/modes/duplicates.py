"""Duplicates mode: find exact copies, and resized or recompressed copies of photos.

Exact copies (photos and videos) share their bytes: only files whose byte
size collides are SHA-256 hashed. Resized copies of a photo have close
perceptual hashes and the same shape, and were not taken at different
moments (those are repeated shots, not duplicates): see groups.is_copy_pair.
No group's dated members span more than SAME_MOMENT_S seconds, even when
undated copies link them.
"""

import os
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from photosort.dates import find_datetime
from photosort.groups import Candidate, Group, best_copy, close_pairs, is_copy_pair, pixels
from photosort.hashing import sha256_file
from photosort.media import kind
from photosort.modes._analysis import measure_photo
from photosort.scanner import scan
from photosort.thresholds import DUP_HASH_MAX, SAME_MOMENT_S

EXACT_NOTE = "Exact copy"
SMALLER_NOTE = "Smaller copy"
WHATSAPP_NOTE = "Smaller copy (WhatsApp)"
_WHATSAPP = re.compile(r"-WA\d+")


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
                    digests[rel] = sha256_file(path)
                except OSError:
                    pass
            file_kind = kind(rel)
            width = height = sharp = None
            if file_kind == "photo":
                measured = measure_photo(path)
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


def _group(
    candidates: dict[str, Candidate], digests: dict[str, str], hashes: dict[str, int]
) -> list[Group]:
    rels = sorted(candidates)
    index = {rel: n for n, rel in enumerate(rels)}
    parent = list(range(len(rels)))
    # Each component's earliest and latest taken time (None: no dated member).
    span: list[tuple[datetime, datetime] | None] = [
        (c.taken, c.taken) if c.taken is not None else None
        for c in (candidates[rel] for rel in rels)
    ]

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def merged_span(ra: int, rb: int) -> tuple[datetime, datetime] | None:
        a, b = span[ra], span[rb]
        if a is None or b is None:
            return a or b
        return min(a[0], b[0]), max(a[1], b[1])

    def union(a: int, b: int, same_moment: bool) -> None:
        """Join a's and b's components; with same_moment, only within SAME_MOMENT_S."""
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        joined = merged_span(ra, rb)
        if same_moment and joined is not None and (
            (joined[1] - joined[0]).total_seconds() > SAME_MOMENT_S
        ):
            return  # would put shots of different moments in one group
        low, high = min(ra, rb), max(ra, rb)
        parent[high] = low
        span[low] = joined

    # Exact links always join: byte-identical files share their metadata.
    by_digest: dict[str, list[int]] = defaultdict(list)
    for rel in rels:
        if rel in digests:
            by_digest[digests[rel]].append(index[rel])
    for same in by_digest.values():
        for n in same[1:]:
            union(same[0], n, same_moment=False)

    # Resized links, closest first (ties by path order), so the result
    # doesn't depend on the order pairs are found in.
    hashed = [rel for rel in rels if rel in hashes]
    links = []
    for a, b in close_pairs([hashes[rel] for rel in hashed], DUP_HASH_MAX):
        ca, cb = candidates[hashed[a]], candidates[hashed[b]]
        hash_a, hash_b = hashes[ca.rel], hashes[cb.rel]
        if is_copy_pair(ca, cb, hash_a, hash_b):
            links.append(((hash_a ^ hash_b).bit_count(), index[ca.rel], index[cb.rel]))
    for _, a, b in sorted(links):
        union(a, b, same_moment=True)

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
            elif pixels(c) < pixels(best):
                note = SMALLER_NOTE
        noted.append(replace(c, note=note))
    return Group("resized", noted, {best.rel})
