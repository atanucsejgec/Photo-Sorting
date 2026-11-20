"""Preview: every file of a folder, one at a time, each moved at once by the owner.

This is the part with no Qt: which files are listed and in what order, what
kind each one is (for its viewer), and the session that moves them, puts them
back (last first) and journals every move, so that the run can be undone
from Done or History like any sort.

Moves stay inside the root, because the journal keeps paths relative to it.
A file is never overwritten (a taken name gets " (n)", as in a sort) and
never deleted. A put-back is journalled as a reverse move in the same run;
undo goes through a run's moves in reverse, so undoing the run afterwards is
still right.
"""

import errno
import functools
import os
import re
import threading
from pathlib import Path
from typing import Literal

from photosort import media
from photosort.executor import PHOTOSORT_DIR, make_parents, move_file, remove_if_empty
from photosort.journal import RunJournal
from photosort.naming import TargetAllocator
from photosort.scanner import walk_all_files
from photosort.settings import PREVIEW_SLOTS

MODE = "preview"
NOT_NEEDED = "Not needed"  # <root>/Not needed, where N sends a file
SLOTS = PREVIEW_SLOTS  # the listed folders: keys 1-9, then 0 for the tenth

FileKind = Literal["image", "pdf", "video", "audio", "text", "other"]

AUDIO_EXTS: frozenset[str] = frozenset({
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".amr",
})
TEXT_EXTS: frozenset[str] = frozenset({
    ".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".ini", ".yaml", ".yml", ".srt",
})
# Extensions Pillow knows but can't show as a picture here: PostScript needs
# Ghostscript, and its MPEG plugin reads no frames.
_NOT_PICTURES: frozenset[str] = frozenset({".pdf", ".ps", ".eps", ".mpg", ".mpeg"})


@functools.cache
def _image_exts() -> frozenset[str]:
    """The extensions imageinfo.open_image reads: those Pillow (with the HEIF
    opener imageinfo registers) can open, and the app's photo types."""
    from PIL import Image

    from photosort import imageinfo  # noqa: F401  (registers the HEIF opener)

    Image.init()
    openable = {ext.lower() for ext, fmt in Image.registered_extensions().items()
                if fmt in Image.OPEN}
    return frozenset((openable | media.PHOTO_EXTS) - _NOT_PICTURES)


def file_kind(path: Path) -> FileKind:
    """Which viewer shows path, by its extension alone."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if media.kind(path) == "video":
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in TEXT_EXTS:
        return "text"
    if ext in _image_exts():
        return "image"
    return "other"


def inside_root(root: Path, folder: Path) -> bool:
    """Whether folder is root or inside it, both with symlinks resolved."""
    root, folder = Path(root).resolve(), Path(folder).resolve()
    return folder == root or root in folder.parents


def slot_problem(root: Path, folder: str | None) -> str | None:
    """Why a slot can't take a file from root, for the owner (the caller puts
    the slot number in front); None if it can. A folder inside root that is
    missing is fine: a move makes it again."""
    if folder is None:
        return "Slot is empty"
    if not inside_root(root, Path(folder)):
        return f"{Path(folder).name} isn't inside this folder"
    return None


def outside_message(root: Path) -> str:
    """What the owner is told when a folder picked is not inside root."""
    return (f"Pick a folder inside {Path(root).resolve().name} — moves stay inside "
            "the folder you opened, so Undo can put them back.")


def _rel_in(root: Path, folder: Path) -> str:
    """folder (inside root) as a POSIX path relative to root; "" for root."""
    rel = Path(folder).resolve().relative_to(Path(root).resolve()).as_posix()
    return "" if rel == "." else rel


_DIGITS = re.compile(r"(\d+)")


def _natural(name: str) -> tuple:
    """name as names are ordered: case-folded, runs of digits as numbers."""
    parts = _DIGITS.split(name.casefold())
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts))


def _order(rel: str) -> tuple:
    """rel's place in the list: by folder, then by natural name."""
    folder, _, name = rel.rpartition("/")
    folders = tuple(_natural(part) for part in folder.split("/")) if folder else ()
    return folders, _natural(name), rel


def preview_files(root: Path, include_subfolders: bool, slots: list[str | None]) -> list[str]:
    """Every file under root to preview, as POSIX paths relative to root,
    sorted by folder and then by natural name (IMG_2 before IMG_10).

    As scanner.scan_all_files, so the folders the app always skips are left
    out; so are Not needed and the slot folders inside root (other than root
    itself): what is in them has been dealt with.
    """
    root = Path(root)
    skip = {NOT_NEEDED.casefold()}
    for folder in slots:
        if folder is not None and inside_root(root, Path(folder)):
            rel = _rel_in(root, Path(folder))
            if rel:
                skip.add(rel.casefold())

    def kept(rel: str) -> bool:
        parts = rel.casefold().split("/")[:-1]
        return not any("/".join(parts[:n]) in skip for n in range(1, len(parts) + 1))

    return sorted((rel for rel in walk_all_files(root, include_subfolders) if kept(rel)),
                  key=_order)


def _in_not_needed(rel: str) -> bool:
    folder = rel.rpartition("/")[0]
    return folder.casefold() == NOT_NEEDED.casefold()


class PreviewSession:
    """One Preview of root: its moves, journalled as one run (mode "preview").

    The run is made at the first move. Calls may come from a worker thread;
    they are taken one at a time.
    """

    def __init__(self, root: Path, include_subfolders: bool) -> None:
        self.root = Path(root)
        self.include_subfolders = include_subfolders
        self.moved: list[tuple[str, str]] = []  # the live moves (src, dst), in order
        self._journal: RunJournal | None = None
        self._made: list[str] = []  # the folders the session made
        self._finished = False
        self._lock = threading.Lock()

    def move(self, rel: str, target_folder: Path) -> str:
        """Move the file rel into target_folder; return where it went (relative
        to root; a taken name gets " (n)").

        Raises ValueError if target_folder is not inside root, and
        FileNotFoundError if the file is gone; nothing is moved then. Missing
        folders on the way are made, journalled. A file already in
        target_folder stays where it is (rel is returned, nothing recorded).
        """
        with self._lock:
            self._check_open()
            target_folder = Path(target_folder)
            if not inside_root(self.root, target_folder):
                raise ValueError(outside_message(self.root))
            folder = _rel_in(self.root, target_folder)
            if folder.split("/")[0].casefold() == PHOTOSORT_DIR:
                raise ValueError("Photo Sorter keeps its own records there. "
                                 "Pick another folder.")
            src = self.root / rel
            if not os.path.lexists(src):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(src))
            if _same_folder(src.parent, self.root / folder):
                return rel
            name = rel.rpartition("/")[2]
            dst = TargetAllocator(self.root).allocate(f"{folder}/{name}" if folder else name)
            self._move(rel, dst)
            self.moved.append((rel, dst))
            return dst

    def put_back(self) -> str | None:
        """Move the last file moved back where it was; return where it is now
        (a new name if its place is taken meanwhile), or None if nothing is
        left to put back. Journalled as a reverse move.

        If the file is no longer where it was moved to, it is dropped from
        the moves (so the next put-back goes on) and FileNotFoundError is
        raised. Any other failure keeps it, to try again.
        """
        with self._lock:
            self._check_open()
            if not self.moved:
                return None
            src, dst = self.moved.pop()
            if not os.path.lexists(self.root / dst):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT),
                                        str(self.root / dst))
            back = TargetAllocator(self.root).allocate(src)
            try:
                self._move(dst, back)
            except BaseException:
                self.moved.append((src, dst))
                raise
            return back

    def counts(self) -> tuple[int, int]:
        """(to Not needed, to other folders), counting the live moves."""
        to_not_needed = sum(1 for _, dst in self.moved if _in_not_needed(dst))
        return to_not_needed, len(self.moved) - to_not_needed

    def finish(self) -> str | None:
        """End the session: remove the folders it made that are empty now
        (journalled), finish the run as done, and return its run_id; None if
        nothing was ever moved. Safe to call again."""
        with self._lock:
            if not self._finished:
                self._finished = True
                if self._journal is not None:
                    try:
                        # Deepest first, so a parent is tried after its children.
                        for rel in sorted(set(self._made), key=lambda r: r.count("/"),
                                          reverse=True):
                            remove_if_empty(self.root, rel, self._journal)
                    finally:
                        self._journal.finish("done")
            return None if self._journal is None else self._journal.run_id

    def _check_open(self) -> None:
        if self._finished:
            raise RuntimeError("This Preview has ended")

    def _move(self, src: str, dst: str) -> None:
        """Move src to dst (both relative to root), journalled as in a sort."""
        if self._journal is None:
            self._journal = RunJournal.create(self.root, MODE, self.include_subfolders, 0)
        self._made += make_parents(self.root, dst, self._journal)
        i = self._journal.record_intent(src, dst)
        move_file(self.root / src, self.root / dst)
        self._journal.record_done(i)


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False  # b doesn't exist (yet)
