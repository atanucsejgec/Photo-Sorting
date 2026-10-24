"""Names that are valid on Windows/NTFS, and move targets that never clash.

NTFS compares names case-insensitively, so every clash check here uses
casefold().
"""

import os
import re
from pathlib import Path

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

RESERVED_NAMES: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def safe_name(name: str) -> str:
    """Return name made valid as a Windows file or folder name.

    Replaces \\ / : * ? " < > | and control characters with "_", strips
    leading spaces and trailing dots/spaces, and prefixes "_" when the stem
    (the part before the first dot) is a reserved name such as CON. An empty
    result becomes "_".
    """
    name = _BAD_CHARS.sub("_", name).lstrip(" ").rstrip(". ")
    if not name:
        return "_"
    # Windows ignores trailing spaces in the stem, so "CON .txt" is reserved too.
    stem = name.split(".", 1)[0].rstrip(" ")
    if stem.casefold() in RESERVED_NAMES:
        name = "_" + name
    return name


class TargetAllocator:
    """Hands out move targets under root that clash with nothing.

    A result never clashes (case-insensitively) with an entry already on disk
    or with an earlier result from this allocator. Paths in and out are POSIX
    strings relative to root. Each target directory is listed once and cached;
    a directory that does not exist yet counts as empty. Nothing is created.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._taken: dict[str, set[str]] = {}  # casefolded dir -> casefolded names

    def allocate(self, dst: str) -> str:
        """Return dst, or "stem (n).ext" with the smallest free n >= 1."""
        directory, _, name = dst.rpartition("/")
        taken = self._names_in(directory)
        dot = name.rfind(".")
        stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
        candidate, n = name, 0
        while candidate.casefold() in taken:
            n += 1
            candidate = f"{stem} ({n}){ext}"
        taken.add(candidate.casefold())
        return f"{directory}/{candidate}" if directory else candidate

    def _names_in(self, directory: str) -> set[str]:
        key = directory.casefold()
        if key not in self._taken:
            try:
                names = os.listdir(self._root / directory)
            except (FileNotFoundError, NotADirectoryError):
                names = []
            self._taken[key] = {n.casefold() for n in names}
        return self._taken[key]
