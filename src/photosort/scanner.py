"""Find the media files in a folder."""

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
