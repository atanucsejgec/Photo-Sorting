"""Find the files in a folder: the media files (scan) or every file (scan_all_files)."""

import os
import threading
from collections.abc import Iterator
from pathlib import Path

from photosort.media import kind

# Folders never scanned (compared with casefold()): the ones the app itself
# creates, plus Windows system folders found at the root of NTFS drives.
SKIP_DIRS: frozenset[str] = frozenset({
    ".photosort", "duplicates", "repeated shots", "bad photos",
    "$recycle.bin", "system volume information",
})


# Files scan_all_files leaves alone. Unfinished downloads, by suffix (compared
# with casefold()): they are still being written.
PARTIAL_SUFFIXES: frozenset[str] = frozenset({
    ".part", ".crdownload", ".download", ".partial", ".!qb",
})
# System clutter that belongs where it is, by name (compared with casefold()).
CLUTTER_NAMES: frozenset[str] = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})

# Windows' FILE_ATTRIBUTE_HIDDEN and FILE_ATTRIBUTE_SYSTEM.
_HIDDEN_OR_SYSTEM = 0x2 | 0x4
# Only Windows' stat results have st_file_attributes.
_HAS_FILE_ATTRIBUTES = hasattr(os.stat_result, "st_file_attributes")


def skipped_folder(name: str) -> bool:
    """Whether a folder called name is never scanned: hidden, or one of SKIP_DIRS."""
    return name.startswith(".") or name.casefold() in SKIP_DIRS


def scan(root: Path, include_subfolders: bool) -> list[str]:
    """Return media files under root as sorted POSIX paths relative to root.

    Hidden files and dirs, symlinks, and SKIP_DIRS are skipped; skipped dirs
    are never entered. A subfolder that cannot be listed (OSError, e.g.
    PermissionError) is skipped silently; an unreadable root still raises.
    """
    return sorted(walk_media(root, include_subfolders))


def walk_media(root: Path, include_subfolders: bool,
               cancel: threading.Event | None = None) -> Iterator[str]:
    """The files scan() finds, unsorted, as the folders are listed. With
    cancel, the walk stops (early, so incomplete) once cancel is set: it is
    checked before each folder is listed."""

    def walk(directory: str, prefix: str) -> Iterator[str]:
        if cancel is not None and cancel.is_set():
            return
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            if not prefix:  # the root itself: let the caller see the error
                raise
            return
        for entry in entries:
            name = entry.name
            if name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if include_subfolders and not skipped_folder(name):
                    yield from walk(entry.path, f"{prefix}{name}/")
            elif entry.is_file(follow_symlinks=False) and kind(name) is not None:
                yield prefix + name

    yield from walk(os.fspath(root), "")


def _hidden_on_windows(entry: os.DirEntry) -> bool:
    """Whether Windows marks entry hidden or system; always False elsewhere."""
    if not _HAS_FILE_ATTRIBUTES:
        return False
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _HIDDEN_OR_SYSTEM)


def _clutter(name: str) -> bool:
    """Whether a file called name is an unfinished download or system clutter."""
    folded = name.casefold()
    return folded in CLUTTER_NAMES or os.path.splitext(folded)[1] in PARTIAL_SUFFIXES


def scan_all_files(root: Path, include_subfolders: bool) -> list[str]:
    """Return every file under root, of any type, as sorted POSIX paths relative to root.

    As scan(), but for every file, not only media: hidden files and dirs,
    symlinks and SKIP_DIRS are skipped, and so are unfinished downloads
    (PARTIAL_SUFFIXES), system clutter (CLUTTER_NAMES) and, on Windows,
    entries marked hidden or system. Skipped dirs are never entered. A
    subfolder that cannot be listed is skipped silently; an unreadable root
    still raises.
    """
    return sorted(walk_all_files(root, include_subfolders))


def walk_all_files(root: Path, include_subfolders: bool,
                   cancel: threading.Event | None = None) -> Iterator[str]:
    """The files scan_all_files() finds, unsorted, as the folders are listed.
    With cancel, the walk stops (early, so incomplete) once cancel is set: it
    is checked before each folder is listed."""

    def walk(directory: str, prefix: str) -> Iterator[str]:
        if cancel is not None and cancel.is_set():
            return
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            if not prefix:  # the root itself: let the caller see the error
                raise
            return
        for entry in entries:
            name = entry.name
            if name.startswith(".") or entry.is_symlink() or _hidden_on_windows(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                if include_subfolders and not skipped_folder(name):
                    yield from walk(entry.path, f"{prefix}{name}/")
            elif entry.is_file(follow_symlinks=False) and not _clutter(name):
                yield prefix + name

    yield from walk(os.fspath(root), "")
