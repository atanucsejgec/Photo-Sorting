"""No people: photos where no face is found move into a "No people" folder.

The faces are found as in Face mode, with its analyser (one photo failing
never stops the scan, but the first photos all failing means the face
models are at fault: an error, never "no faces") and its face cache, so a
folder Face mode has looked at is quick, and both modes agree on what a
face is. Any face found counts: a photo with one stays where it is, and so
does a photo that can't be read. Videos and other files are not looked at.
The folder is made in the sorted folder itself; a photo already directly in
a No people folder (any case, at any depth) is in place.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from photosort.cache import AnalysisCache
from photosort.faces import Face
from photosort.media import kind
from photosort.modes.face_mode import Analyser, faces_of
from photosort.naming import TargetAllocator, fold, resolve_existing_dir
from photosort.plan import Move, Plan
from photosort.scanner import scan

NO_PEOPLE = "No people"


def find_no_people(
    root: Path,
    include_subfolders: bool,
    analyse: Callable[[Path], list[Face]],
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    cache: AnalysisCache | None = None,
) -> Plan | None:
    """Plan the moves of the photos under root with no face into No people;
    None if cancelled.

    analyse(path) returns a photo's faces (raising UnreadableImage if it
    can't); see face_mode.Analyser for the other errors. progress(i, total)
    is called after each photo. With a cache, a photo analysed before (by
    this mode or Face mode) is not analysed again.
    """
    root = Path(root)
    analyser = Analyser(analyse)
    photos = [rel for rel in scan(root, include_subfolders) if kind(rel) == "photo"]
    total = len(photos)
    folder = resolve_existing_dir(root, NO_PEOPLE)
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    with_people = unreadable = in_place = 0
    for i, rel in enumerate(photos, start=1):
        if cancel is not None and cancel.is_set():
            return None
        parent, _, name = rel.rpartition("/")
        if fold(parent.rpartition("/")[2]) == fold(NO_PEOPLE):
            in_place += 1
        else:
            faces = faces_of(root, rel, analyser, cache)
            if faces is None:
                unreadable += 1
            elif faces:
                with_people += 1
            else:
                moves.append(Move(rel, allocator.allocate(f"{folder}/{name}")))
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None

    summary = [f"{len(moves)} photos without faces → {NO_PEOPLE}",
               f"{with_people} photos with people stay"]
    if unreadable:
        summary.append(f"{unreadable} photos could not be read (they stay)")
    if in_place:
        summary.append(f"{in_place} already in place")
    return Plan("no_people", moves, summary, in_place)
