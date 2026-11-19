"""Pick faces mode: find every face in a folder, group the faces into people,
and move the photos of the people the owner picks into folders they name.

Nothing needs to be set up first: the faces come from the same engine and
analysis cache as Face mode (a folder Face mode has scanned groups at once),
photos only, videos skipped.

Grouping (group_people) is average-linkage agglomerative clustering, done
in rounds with numpy alone and in bounded memory:

1. Each face starts as a group of its own. A group is kept as the sum of its
   faces' unit embeddings and their count, so the average similarity of
   every face of group A to every face of group B (average linkage) is
   sum_A . sum_B / (n_A * n_B), with no pair of faces ever looked at again.
2. A round lists candidate pairs of groups: the groups' mean embeddings are
   compared block by block (at most _BLOCK rows at a time, against the
   groups after them), and for each group the up to _NEIGHBOURS most alike
   later groups at or above the join are kept. No matrix larger than
   _BLOCK x (number of groups) is ever held. The cap on candidates keeps
   memory bounded when a person has hundreds of faces; it can, rarely, miss
   a merge (a pair outside both groups' top _NEIGHBOURS whose average
   linkage is still at or above the join, once the round's merges are
   done). The next round compares the merged groups afresh, so such a pair
   is usually found then.
3. The candidates are merged, most alike first, if the two groups are still
   apart, their average linkage (worked out afresh, as earlier merges of the
   round may have changed it) is still at or above the join, and they share
   no photo: two faces of one photo are never one person.
4. Rounds repeat, on the merged groups, until one merges nothing.

The groups come out most photos first, then by their best face's photo.
"""

import functools
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from photosort.cache import AnalysisCache
from photosort.faces import Face
from photosort.media import kind
from photosort.modes.face_mode import Analyser, faces_of
from photosort.naming import TargetAllocator, fold, resolve_existing_dir
from photosort.people import clean_name, name_problem
from photosort.plan import Move, Plan
from photosort.scanner import scan
from photosort.thresholds import PICK_FACES_JOIN

MODE = "pick_faces"

_BLOCK = 512  # rows of similarities worked out at once (<= 1024)
# Candidate merges kept per group and round: enough for any one merge to be
# found within a few rounds, few enough to keep memory bounded (see step 2).
_NEIGHBOURS = 20
_TOP_ROWS = 128  # rows ranked at once when a row has more candidates than that


@dataclass(frozen=True)
class FoundFace:
    """Face number index (in the engine's order) of the photo rel."""

    rel: str
    index: int
    face: Face


def _area(face: Face) -> float:
    x1, y1, x2, y2 = face.box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


@dataclass(frozen=True)
class PersonGroup:
    """One person's faces, as group_people found them, sorted by (rel, index)."""

    id: int
    faces: tuple[FoundFace, ...]

    @functools.cached_property
    def photos(self) -> frozenset[str]:
        return frozenset(f.rel for f in self.faces)

    @functools.cached_property
    def best(self) -> FoundFace:
        """The clearest face: the detector's highest score, then the largest box."""
        return max(self.faces, key=lambda f: (f.face.score, _area(f.face)))


@dataclass(frozen=True)
class PickFolder:
    """A folder the owner made: the faces (rel, index) picked for it, and
    whether to remember them as a person afterwards."""

    name: str
    faces: frozenset[tuple[str, int]]
    remember: bool


def scan_faces(
    root: Path,
    include_subfolders: bool,
    analyse: Callable[[Path], list[Face]],
    cache: AnalysisCache | None,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[FoundFace] | None:
    """Every face in the photos under root, in scan order; None if cancelled.

    analyse(path) is the face engine, as for face_mode.find_face_matches: a
    photo it can't read is passed over, and when the first photos all fail
    the error is raised (face_mode.Analyser). progress(i, total) is called
    after each photo; videos are not looked at. With a cache, a photo
    analysed before is not analysed again.
    """
    root = Path(root)
    analyser = Analyser(analyse)
    photos = [rel for rel in scan(root, include_subfolders) if kind(rel) == "photo"]
    total = len(photos)
    found: list[FoundFace] = []
    for i, rel in enumerate(photos, start=1):
        if cancel is not None and cancel.is_set():
            return None
        for index, face in enumerate(faces_of(root, rel, analyser, cache) or []):
            found.append(FoundFace(rel, index, face))
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return found


def _candidates(means: np.ndarray, join: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pairs (i, j), i < j, of rows of means at least join alike, as arrays
    i, j and similarity, most alike first; at most _NEIGHBOURS per i."""
    k = len(means)
    found_i, found_j, found_s = [], [], []
    for start in range(0, k, _BLOCK):
        stop = min(k, start + _BLOCK)
        # Row r is group start + r; column c is group start + c (only later
        # groups, and the block's own square, whose diagonal and below are
        # ruled out).
        block = means[start:stop] @ means[start:].T
        rows = stop - start
        lower = np.tril_indices(rows)
        block[lower] = -np.inf
        del lower
        hit = block >= join
        per_row = hit.sum(axis=1)
        few = np.flatnonzero((per_row > 0) & (per_row <= _NEIGHBOURS))
        if few.size:
            r, c = np.nonzero(hit[few])
            r = few[r]
            found_i.append(r + start)
            found_j.append(c + start)
            found_s.append(block[r, c])
        del hit
        many = np.flatnonzero(per_row > _NEIGHBOURS)
        for part in range(0, many.size, _TOP_ROWS):
            r = many[part:part + _TOP_ROWS]
            top = np.argpartition(block[r], -_NEIGHBOURS, axis=1)[:, -_NEIGHBOURS:]
            r = np.repeat(r, _NEIGHBOURS)
            c = top.ravel()
            del top
            found_i.append(r + start)
            found_j.append(c + start)
            found_s.append(block[r, c])
        del block
    if not found_i:
        empty = np.empty(0, np.int64)
        return empty, empty, np.empty(0, np.float32)
    i = np.concatenate(found_i)
    j = np.concatenate(found_j)
    s = np.concatenate(found_s)
    order = np.lexsort((j, i, -s))
    return i[order], j[order], s[order]


def group_people(faces: list[FoundFace], join: float = PICK_FACES_JOIN) -> list[PersonGroup]:
    """The faces grouped into people (see the module's docstring), most photos first.

    Groups with as many photos are ordered by their best face's photo (rel),
    then its index. Two faces of one photo are never in one group. Ids are
    the groups' positions in the list.
    """
    if not faces:
        return []
    size = np.asarray(faces[0].face.embedding).size  # the embedding's length (512)
    sums = np.stack([np.asarray(f.face.embedding, np.float32).reshape(size) for f in faces])
    counts = np.ones(len(faces), np.int64)
    members: list[list[int]] = [[n] for n in range(len(faces))]
    photos: list[set[str]] = [{f.rel} for f in faces]
    while True:
        means = sums if np.all(counts == 1) else sums / counts[:, None].astype(np.float32)
        cand_i, cand_j, _ = _candidates(means, join)
        del means
        parent = list(range(len(members)))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        merged = False
        for a, b in zip(cand_i.tolist(), cand_j.tolist()):
            a, b = find(a), find(b)
            if a == b or not photos[a].isdisjoint(photos[b]):
                continue
            if float(np.dot(sums[a], sums[b])) / (counts[a] * counts[b]) < join:
                continue
            if counts[a] < counts[b]:
                a, b = b, a
            sums[a] += sums[b]
            counts[a] += counts[b]
            photos[a] |= photos[b]
            members[a].extend(members[b])
            parent[b] = a
            merged = True
        if not merged:
            break
        keep = [n for n in range(len(members)) if parent[n] == n]
        sums = sums[keep]
        counts = counts[keep]
        members = [members[n] for n in keep]
        photos = [photos[n] for n in keep]
    del sums
    groups = [tuple(sorted((faces[n] for n in group), key=lambda f: (f.rel, f.index)))
              for group in members]
    people = [PersonGroup(0, g) for g in groups]
    people.sort(key=lambda p: (-len(p.photos), p.best.rel, p.best.index))
    return [PersonGroup(n, p.faces) for n, p in enumerate(people)]


def check_folder_name(root: Path, name: str, taken: list[str]) -> str | None:
    """Why name can't be a new folder of the list (taken holds the list's
    names), or None if it can.

    The name is cleaned and refused as a person's is (people.name_problem:
    it may be remembered as one); it also must not be the name
    of a file in root (case-insensitively), nor be in taken. A folder of that
    name in root is fine: it is joined.
    """
    name, problem = name_problem(name)
    if problem is not None:
        return problem
    if fold(name) in {fold(t) for t in taken}:
        return f"There is already a folder called {name} in your list"
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if fold(entry.name) == fold(name) and not entry.is_dir():
                    return f"A file named {name} is already there"
    except OSError:
        pass
    return None


def photo_folders(folders: list[PickFolder]) -> dict[str, list[str]]:
    """Each photo with a picked face -> the names of the folders it is picked
    for, in the folders' order."""
    found: dict[str, list[str]] = {}
    for folder in folders:
        for rel in sorted({rel for rel, _ in folder.faces}):
            found.setdefault(rel, []).append(folder.name)
    return found


def plan_pick(root: Path, folders: list[PickFolder], choices: dict[str, str | None]) -> Plan:
    """The moves of the photos picked for folders.

    A photo picked for one folder goes there; one picked for several goes to
    choices[rel], and stays where it is if that is None, missing or not one
    of its folders. A folder is root/<name>, joining one already there in
    any case (resolve_existing_dir), and a photo already in it stays put.
    Files keep their names, clashes resolved by TargetAllocator.
    """
    root = Path(root)
    allocator = TargetAllocator(root)
    dirs = {f.name: resolve_existing_dir(root, clean_name(f.name)) for f in folders}
    moved: dict[str, int] = {f.name: 0 for f in folders}
    moves: list[Move] = []
    left = unchanged = 0
    for rel, names in sorted(photo_folders(folders).items()):
        if len(names) == 1:
            name = names[0]
        else:
            name = choices.get(rel)
            if name not in names:
                left += 1
                continue
        folder = dirs[name]
        parent, _, file_name = rel.rpartition("/")
        if fold(parent) == fold(folder):
            unchanged += 1
            continue
        moves.append(Move(rel, allocator.allocate(f"{folder}/{file_name}")))
        moved[name] += 1
    summary = [f"{moved[f.name]} photos → {dirs[f.name]}" for f in folders if moved[f.name]]
    if left:
        summary.append(f"{left} left where they are")
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan(MODE, moves, summary, unchanged)
