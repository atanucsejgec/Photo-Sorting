"""Find the media files in a folder."""

import os
from pathlib import Path

from photosort.media import kind

# Folders the app itself creates; never scanned (compared with casefold()).
SKIP_DIRS: frozenset[str] = frozenset({".photosort", "duplicates", "repeated shots", "bad photos"})


def scan(root: Path, include_subfolders: bool) -> list[str]:
    """Return media files under root as sorted POSIX paths relative to root.

    Hidden files and dirs, symlinks, and app folders (SKIP_DIRS) are skipped;
    skipped dirs are never entered.
    """
    found: list[str] = []

    def walk(directory: str, prefix: str) -> None:
        with os.scandir(directory) as entries:
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
