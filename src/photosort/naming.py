"""Names that are valid on Windows/NTFS, and move targets that never clash.

NTFS compares names case-insensitively, so every clash check here uses
casefold(). The owner's drive is NTFS mounted case-sensitively on Linux and
also used from Windows, so a folder is never created beside one whose name
differs only in case, or only in how its letters are encoded (fold): the
existing folder's spelling is used instead.
"""

import os
import re
import unicodedata
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


def fold(name: str) -> str:
    """name as names are matched: case-insensitively, and in Unicode's NFC form
    (Bengali, say, can type one letter as one character or as two)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def _existing_part(folder: Path, part: str) -> str:
    """Return part as spelt on disk in folder, if a folder by that name exists.

    An exact match wins; otherwise the first (sorted) variant (same fold) that
    is a folder. With no match part is returned unchanged.
    """
    if os.path.isdir(folder / part):
        return part
    try:
        with os.scandir(folder) as entries:
            matches = sorted(
                entry.name for entry in entries
                if fold(entry.name) == fold(part) and entry.is_dir()
            )
    except OSError:
        matches = []
    return matches[0] if matches else part


def resolve_existing_dir(root: Path, rel_dir: str) -> str:
    """Return folder rel_dir (POSIX, under root) with each existing part spelt as on disk.

    Matching is case-insensitive, as on NTFS: with "no date" on disk,
    "No Date" resolves to "no date". Parts with no match keep the requested
    spelling (they are yet to be created).
    """
    if not rel_dir:
        return rel_dir
    resolved: list[str] = []
    current = Path(root)
    for part in rel_dir.split("/"):
        part = _existing_part(current, part)
        resolved.append(part)
        current = current / part
    return "/".join(resolved)


class TargetAllocator:
    """Hands out move targets under root that clash with nothing.

    A result never clashes (case-insensitively) with an entry already on disk
    or with an earlier result from this allocator. Paths in and out are POSIX
    strings relative to root. A result's folder is spelt as on disk where a
    case variant exists (see resolve_existing_dir), and otherwise as first
    requested, so one allocator never gives out two spellings of a folder.
    Each target directory is resolved and listed once and cached; a directory
    that does not exist yet counts as empty. Nothing is created.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._resolved: dict[str, str] = {}  # folded dir -> spelling to use
        self._taken: dict[str, set[str]] = {}  # casefolded dir -> casefolded names

    def allocate(self, dst: str) -> str:
        """Return dst, or "stem (n).ext" with the smallest free n >= 1.

        The folder part may come back spelt differently (as on disk).
        """
        directory, _, name = dst.rpartition("/")
        directory = self._resolve(directory)
        taken = self._names_in(directory)
        dot = name.rfind(".")
        stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
        candidate, n = name, 0
        while candidate.casefold() in taken:
            n += 1
            candidate = f"{stem} ({n}){ext}"
        taken.add(candidate.casefold())
        return f"{directory}/{candidate}" if directory else candidate

    def _resolve(self, directory: str) -> str:
        """resolve_existing_dir, cached per folder level (a big root is listed once)."""
        if not directory:
            return directory
        key = fold(directory)
        if key not in self._resolved:
            parent, _, part = directory.rpartition("/")
            parent = self._resolve(parent)
            part = _existing_part(self._root / parent, part)
            self._resolved[key] = f"{parent}/{part}" if parent else part
        return self._resolved[key]

    def _names_in(self, directory: str) -> set[str]:
        key = directory.casefold()
        if key not in self._taken:
            try:
                names = os.listdir(self._root / directory)
            except (FileNotFoundError, NotADirectoryError):
                names = []
            self._taken[key] = {n.casefold() for n in names}
        return self._taken[key]
