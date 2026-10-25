"""Face mode: move photos of the named people into a folder per person or group.

The people looked for are the ones ticked. With one, every photo with them
goes to "<Name>/". With two or more, only group photos are sorted: a photo
with at least two of them goes to a together folder "<A> & <B>/" (names
sorted case-insensitively; a long one ends "& 3 more"), and a photo with
just one of them stays put. Faces of nobody named (strangers) are ignored,
and a photo with none of the named people stays put. Videos are skipped.

A face matches a person surely at thresholds.FACE_SURE or above; from
FACE_UNSURE up to that, the owner is asked first (a Question); below it,
the face is a stranger's. In each photo a person matches at most one face
and a face at most one person, the most similar pairs first. When only
group photos are sorted, a photo's questions are asked only if it could
still be one: two or more of the people in it, sure or unsure.
"""

import logging
import threading
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from photosort.cache import AnalysisCache
from photosort.faces import FACES_KIND, Box, Face, pack_faces, unpack_faces
from photosort.imageinfo import UnreadableImage
from photosort.media import kind
from photosort.naming import TargetAllocator, fold, resolve_existing_dir, safe_name
from photosort.people import Person, person_scores
from photosort.plan import Move, Plan
from photosort.scanner import scan, skipped_folder
from photosort.thresholds import (
    FACE_ENGINE_FAILS,
    FACE_FOLDER_MAX,
    FACE_FOLDER_MAX_BYTES,
    FACE_SURE,
    FACE_UNSURE,
)

_log = logging.getLogger("photosort")

Answer = Literal["yes", "no", "skip"]

# How sure an unsure match is shown as: FACE_UNSURE..FACE_SURE maps onto these.
_PERCENT_AT_UNSURE = 30
_PERCENT_AT_SURE = 90


@dataclass(frozen=True)
class Question:
    """An unsure match to ask the owner about: is this face in rel that person?"""

    rel: str
    person_id: str
    name: str
    box: Box  # the face, in the decoded photo's pixels
    image_size: tuple[int, int]  # (width, height) of the decoded photo
    similarity: float
    percent: int  # confidence_percent(similarity)


@dataclass
class FaceScan:
    sure: dict[str, set[str]]  # rel -> ids of the people surely in it
    questions: list[Question]  # ordered by rel, then name
    photos: int  # photos looked at, unreadable ones included
    unreadable: int
    groups_only: bool = False  # two or more people looked for: sort group photos only


def confidence_percent(similarity: float) -> int:
    """How sure a match is, for the owner: FACE_UNSURE..FACE_SURE as 30..90 %, kept in 1..99."""
    fraction = (similarity - FACE_UNSURE) / (FACE_SURE - FACE_UNSURE)
    percent = _PERCENT_AT_UNSURE + fraction * (_PERCENT_AT_SURE - _PERCENT_AT_UNSURE)
    return min(99, max(1, round(percent)))


def together_folder(names: Iterable[str]) -> str:
    """The folder for photos of these people: "A & B", sorted case-insensitively.

    It is at most FACE_FOLDER_MAX characters and FACE_FOLDER_MAX_BYTES UTF-8
    bytes: a longer one keeps as many of the first names as fit and ends
    " & <k> more"; a name too long by itself is cut short.
    """
    names = sorted(names, key=str.casefold)
    full = " & ".join(names)
    if _fits(full, FACE_FOLDER_MAX, FACE_FOLDER_MAX_BYTES):
        return full
    if len(names) == 1:
        return _cut(names[0], FACE_FOLDER_MAX, FACE_FOLDER_MAX_BYTES)
    for kept in range(len(names) - 1, 0, -1):
        folder = " & ".join(names[:kept]) + f" & {len(names) - kept} more"
        if _fits(folder, FACE_FOLDER_MAX, FACE_FOLDER_MAX_BYTES):
            return folder
    more = f" & {len(names) - 1} more"
    return _cut(names[0], FACE_FOLDER_MAX - len(more),
                FACE_FOLDER_MAX_BYTES - len(more.encode())) + more


def _fits(name: str, max_chars: int, max_bytes: int) -> bool:
    return len(name) <= max_chars and len(name.encode("utf-8", "surrogatepass")) <= max_bytes


def _cut(name: str, max_chars: int, max_bytes: int) -> str:
    """name cut short to fit, never splitting a character, as a valid folder
    name that the scanner still looks into."""
    end = min(len(name), max_chars)
    while True:
        folder = _clean_cut(name[:end])
        if _fits(folder, max_chars, max_bytes):
            return folder
        end -= 1


def _clean_cut(part: str) -> str:
    """A cut-off part of a name as a folder name: no dangling dot, space or
    combining mark (a vowel sign cut off from its letter, say), Windows-valid,
    and not one the scanner skips."""
    while part and (part[-1] in ". \u200c\u200d"
                    or unicodedata.category(part[-1]).startswith("M")):
        part = part[:-1]
    part = safe_name(part)
    return "_" + part if skipped_folder(part) else part


def _assign(faces: list[Face], people: list[Person]) -> list[tuple[Face, Person, float]]:
    """(face, person, similarity) matches, one-to-one, most similar first.

    Pairs below FACE_UNSURE never match.
    """
    by_id = {p.id: p for p in people}
    pairs = [
        (score, n, person_id)
        for n, f in enumerate(faces)
        for person_id, score in person_scores(f.embedding, people).items()
        if score >= FACE_UNSURE
    ]
    pairs.sort(key=lambda pair: -pair[0])  # stable: ties keep face, then person order
    used_faces: set[int] = set()
    used_people: set[str] = set()
    matches = []
    for score, n, person_id in pairs:
        if n in used_faces or person_id in used_people:
            continue
        used_faces.add(n)
        used_people.add(person_id)
        matches.append((faces[n], by_id[person_id], score))
    return matches


class _Analyser:
    """analyse(path), where one photo failing never stops the scan.

    An error other than UnreadableImage is logged with the photo's path and
    the photo counted as unreadable; but when the first FACE_ENGINE_FAILS
    photos analysed all fail so, the face models are at fault, not the
    photos, and the error is raised.
    """

    def __init__(self, analyse: Callable[[Path], list[Face]]) -> None:
        self._analyse = analyse
        self._worked = False  # a photo has been analysed
        self._fails = 0

    def __call__(self, path: Path, rel: str) -> list[Face] | None:
        try:
            faces = self._analyse(path)
        except UnreadableImage:
            return None
        except Exception:
            self._fails += 1
            if not self._worked and self._fails >= FACE_ENGINE_FAILS:
                raise
            _log.warning("Looking for faces in %s failed", rel, exc_info=True)
            return None
        self._worked = True
        return faces


def _faces_of(root: Path, rel: str, analyse: _Analyser,
              cache: AnalysisCache | None) -> list[Face] | None:
    """The faces in the photo root/rel, or None if it can't be read (not cached)."""
    path = root / rel
    if cache is not None:
        data = cache.get(FACES_KIND, path)
        if data is not None:
            try:
                return unpack_faces(data)
            except ValueError:
                pass  # not ours after all: look again
    faces = analyse(path, rel)
    if faces is not None and cache is not None:
        cache.put(FACES_KIND, path, pack_faces(faces))
    return faces


def find_face_matches(
    root: Path,
    include_subfolders: bool,
    people: list[Person],
    analyse: Callable[[Path], list[Face]],
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    cache: AnalysisCache | None = None,
) -> FaceScan | None:
    """Find which of people are in each photo under root; None if cancelled.

    analyse(path) returns a photo's faces (raising UnreadableImage if it
    can't). A photo it fails on otherwise is counted as unreadable, unless
    the first photos all fail (see _Analyser). progress(i, total) is called after
    each photo; videos are not looked at. With a cache, a photo analysed
    before is not analysed again. With two or more people, only group photos
    are sorted (FaceScan.groups_only), so a photo's unsure matches are asked
    about only if it has at least two of the people, sure and unsure together.
    """
    root = Path(root)
    groups_only = len(people) >= 2
    analyser = _Analyser(analyse)
    photos = [rel for rel in scan(root, include_subfolders) if kind(rel) == "photo"]
    total = len(photos)
    sure: dict[str, set[str]] = {}
    questions: list[Question] = []
    unreadable = 0
    for i, rel in enumerate(photos, start=1):
        if cancel is not None and cancel.is_set():
            return None
        faces = _faces_of(root, rel, analyser, cache)
        if faces is None:
            unreadable += 1
        else:
            unsure = []
            for f, person, score in _assign(faces, people):
                if score >= FACE_SURE:
                    sure.setdefault(rel, set()).add(person.id)
                else:
                    unsure.append(Question(rel, person.id, person.name, f.box,
                                           f.image_size, score, confidence_percent(score)))
            if not groups_only or len(sure.get(rel, ())) + len(unsure) >= 2:
                questions.extend(unsure)
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    questions.sort(key=lambda q: (q.rel, q.name.casefold(), q.name))
    return FaceScan(sure, questions, total, unreadable, groups_only)


def plan_faces(root: Path, scan: FaceScan, people: list[Person],
               answers: dict[tuple[str, str], Answer]) -> Plan:
    """Plan the moves for scan, given the owner's answers to its questions.

    answers maps (rel, person_id) to "yes" (add that person to the photo),
    "no" (leave them out) or "skip" (leave the whole photo where it is). A
    photo with a question not yet answered stays put too. People no longer
    in people, or named like a folder the scanner skips, are left out. When
    scan.groups_only, a photo left with fewer than two people stays put.
    """
    root = Path(root)
    # A name the scanner skips would hide the photos from later runs.
    names = {p.id: p.name for p in people if not skipped_folder(p.name)}
    asked: dict[str, list[Question]] = {}
    for q in scan.questions:
        asked.setdefault(q.rel, []).append(q)

    allocator = TargetAllocator(root)
    moves: list[Move] = []
    person_folders: set[str] = set()
    together_folders: set[str] = set()
    unchanged = 0
    for rel in sorted(scan.sure.keys() | asked.keys()):
        replies = [answers.get((rel, q.person_id)) for q in asked.get(rel, [])]
        if "skip" in replies or None in replies:
            continue
        ids = set(scan.sure.get(rel, set()))
        ids |= {q.person_id for q, reply in zip(asked.get(rel, []), replies) if reply == "yes"}
        in_photo = [names[i] for i in ids if i in names]
        if len(in_photo) < (2 if scan.groups_only else 1):
            continue
        folder = resolve_existing_dir(root, together_folder(in_photo))
        parent, _, name = rel.rpartition("/")
        if fold(parent) == fold(folder):
            unchanged += 1
            continue
        moves.append(Move(rel, allocator.allocate(f"{folder}/{name}")))
        (person_folders if len(in_photo) == 1 else together_folders).add(folder.casefold())

    stay = scan.photos - scan.unreadable - len(moves) - unchanged
    summary = [f"{len(moves)} photos → {len(person_folders)} person folders, "
               f"{len(together_folders)} together folders",
               f"{stay} photos stay where they are"]
    if unchanged:
        summary.append(f"{unchanged} photos are already in their folders")
    if scan.unreadable:
        summary.append(f"{scan.unreadable} photos couldn't be read")
    return Plan("face", moves, summary, unchanged)
