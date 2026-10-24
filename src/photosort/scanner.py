"""Find the media files in a folder."""

import os
from pathlib import Path

from photosort.media import kind

# Folders never scanned (compared with casefold()): the ones the app itself
# creates, plus Windows system folders found at the root of NTFS drives.
SKIP_DIRS: frozenset[str] = frozenset({
    ".photosort", "duplicates", "repeated shots", "bad photos",
    "$recycle.bin", "system volume information",
})


def scan(root: Path, include_subfolders: bool) -> list[str]:
    """Return media files under root as sorted POSIX paths relative to root.

    Hidden files and dirs, symlinks, and SKIP_DIRS are skipped; skipped dirs
    are never entered. A subfolder that cannot be listed (OSError, e.g.
    PermissionError) is skipped silently; an unreadable root still raises.
    """
    found: list[str] = []

    def walk(directory: str, prefix: str) -> None:
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
                if include_subfolders and name.casefold() not in SKIP_DIRS:
                    walk(entry.path, f"{prefix}{name}/")
            elif entry.is_file(follow_symlinks=False) and kind(name) is not None:
                found.append(prefix + name)

    walk(os.fspath(root), "")
    return sorted(found)
