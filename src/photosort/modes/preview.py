"""Preview: every file of a folder, one at a time, each moved at once by the owner.

This is the part with no Qt: which files are listed and in what order, what
kind each one is (for its viewer), and the session that moves them, puts them
back (last first) and journals every move, so that the run can be undone
from Done or History like any sort.

A file may go to any folder: inside the root (journalled relative to it, a
missing folder made again) or outside it, even on another drive (journalled
absolute, the folder listed in the run's also_roots; executor.move_file
copies, verifies and only then removes the original across drives). A
folder outside the root that is missing is never made: it is most likely an
unplugged drive. App folders are refused. A file is never overwritten (a taken name gets " (n)", as in a sort) and
never deleted. A put-back is journalled as a reverse move in the same run;
undo goes through a run's moves in reverse, so undoing the run afterwards is
still right.

The files moved by one key press in the day grid form a group, put back
together; a move with no group is a group of its own. Each file's day is the
local date of its modified time.
"""

import errno
import functools
import os
import re
import threading
from datetime import date
from pathlib import Path
from typing import Literal

from photosort import appdata, media
from photosort.executor import PHOTOSORT_DIR, make_parents, move_file, remove_if_empty
from photosort.journal import RunJournal, abs_path, norm_root
from photosort.naming import TargetAllocator
from photosort.scanner import walk_all_files
from photosort.undo import drive_missing
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


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def file_day(path: Path) -> date | None:
    """The day path was last modified, in local time: a link's file's, or a
    broken link's own; None if neither can be read."""
    for stat in (os.stat, os.lstat):
        try:
            return date.fromtimestamp(stat(path).st_mtime)
        except (OSError, OverflowError, ValueError):
            continue
    return None


def day_text(d: date) -> str:
    """d as the owner reads it, "Fri 28 Feb 2025", in English whatever the locale."""
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]} {d.year}"


class PutBackError(Exception):
    """A group put back part way: back lists where the files put back before
    the failure are now (they stay back); error is what went wrong."""

    def __init__(self, back: list[str], error: BaseException) -> None:
        super().__init__(str(error))
        self.back = back
        self.error = error


def inside_root(root: Path, folder: Path) -> bool:
    """Whether folder is root or inside it, both with symlinks resolved."""
    root, folder = Path(root).resolve(), Path(folder).resolve()
    return folder == root or root in folder.parents


APP_FOLDER = "Photo Sorter keeps its own records there. Pick another folder."


def _in_app_folder(folder: Path) -> bool:
    """Whether folder (resolved) is in a .photosort folder or in the app's
    data folder (its undo records)."""
    folder = norm_root(folder)
    if any(part.casefold() == PHOTOSORT_DIR for part in folder.parts):
        return True
    data = norm_root(appdata.data_dir())
    return folder == data or data in folder.parents


def target_problem(root: Path, folder) -> str | None:
    """Why folder can't take a file from root, for the owner; None if it
    can. Any folder can, but one in an app folder; and one outside root
    must be there (a missing one inside root is made again by the move)."""
    if folder is None:
        return "Slot is empty"
    folder = Path(folder)
    if _in_app_folder(folder):
        return APP_FOLDER
    if not inside_root(root, folder) and not folder.is_dir():
        return drive_missing(folder)
    return None


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
        self._groups: list[object] = []  # the group of each live move, as in moved
        self._journal: RunJournal | None = None
        self._made: list[str] = []  # the folders the session made
        self._finished = False
        self._lock = threading.Lock()

    def move(self, rel: str, target_folder: Path, group: int | None = None) -> str:
        """Move the file rel into target_folder; return where it went (a taken
        name gets " (n)"): relative to root inside it, an absolute POSIX
        path outside it. Moves tagged with the same group, one after another,
        are put back together by put_back_group; with no group the move is a
        group of its own. The caller gives each key press a fresh group id:
        equal ids next to each other merge into one group.

        Raises ValueError if target_folder is in an app folder or the file is
        not inside root, and FileNotFoundError if the file is gone or
        target_folder is outside root and missing (undo.drive_missing); nothing
        is moved then. Missing folders on the way inside root are made,
        journalled. A file already in target_folder stays where it is (rel
        is returned, nothing recorded).
        """
        with self._lock:
            self._check_open()
            target_folder = Path(target_folder)
            if _in_app_folder(target_folder):
                raise ValueError(APP_FOLDER)
            inside = inside_root(self.root, target_folder)
            src = self.root / rel
            # The file itself may be a link (it is moved, never followed); the
            # folder it is in must be inside root, as every path journalled is.
            if not inside_root(self.root, src.parent):
                raise ValueError(f"{rel} isn't inside {self.root.name}")
            if not inside and not target_folder.is_dir():
                raise FileNotFoundError(drive_missing(target_folder))
            if not os.path.lexists(src):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(src))
            name = rel.rpartition("/")[2]
            if inside:
                folder = _rel_in(self.root, target_folder)
                if _same_folder(src.parent, self.root / folder):
                    return rel
                dst = TargetAllocator(self.root).allocate(
                    f"{folder}/{name}" if folder else name)
            else:
                outside = norm_root(target_folder)
                if _same_folder(src.parent, outside):
                    return rel
                dst = (outside / TargetAllocator(outside).allocate(name)).as_posix()
            self._move(rel, dst)
            self.moved.append((rel, dst))
            self._groups.append(object() if group is None else group)
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
            return self._put_back_last()

    def put_back_group(self) -> list[str]:
        """Put back every file of the last group, last first; return where
        each is now, in that order ([] if nothing is left to put back).

        If one fails, the ones already back stay back, it and the rest of the
        group stay moved (one no longer where it was moved to is dropped, as
        in put_back) and PutBackError is raised, listing those put back. An
        interrupt (not an Exception) passes through as it is.
        """
        with self._lock:
            self._check_open()
            back: list[str] = []
            if not self.moved:
                return back
            group = self._groups[-1]
            while self.moved and self._groups[-1] == group:
                try:
                    back.append(self._put_back_last())
                except Exception as error:
                    raise PutBackError(back, error) from error
            return back

    def _put_back_last(self) -> str:
        """put_back's work, with the lock held and a move left."""
        src, dst = self.moved.pop()
        group = self._groups.pop()
        if not os.path.lexists(abs_path(self.root, dst)):
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT),
                                    str(abs_path(self.root, dst)))
        back = TargetAllocator(self.root).allocate(src)
        try:
            self._move(dst, back)
        except BaseException:
            self.moved.append((src, dst))
            self._groups.append(group)
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
        """Move src to dst (each relative to root, or absolute outside it),
        journalled as in a sort. A folder outside root is listed in the run's
        also_roots before the move is recorded; none is ever made there."""
        if self._journal is None:
            self._journal = RunJournal.create(self.root, MODE, self.include_subfolders, 0)
        if Path(dst).is_absolute():
            self._journal.add_also_root(Path(dst).parent)
        else:
            self._made += make_parents(self.root, dst, self._journal)
        i = self._journal.record_intent(src, dst)
        move_file(abs_path(self.root, src), abs_path(self.root, dst))
        self._journal.record_done(i)


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False  # b doesn't exist (yet)
