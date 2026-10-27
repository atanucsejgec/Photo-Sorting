"""Extension mode: every file, of any type, into a folder named after its extension.

All .apk files go into APK, all .exe into EXE, and so on; a file with no
extension (or one that is not a plain extension, see extension_folder) goes
into No extension. The folders are made in the sorted folder itself.
"""

import re
import threading
from collections.abc import Callable
from pathlib import Path

from photosort.naming import TargetAllocator, fold
from photosort.plan import Move, Plan
from photosort.scanner import walk_all_files

NO_EXTENSION = "No extension"

# What counts as an extension: 1 to 10 ASCII letters, digits, "_", "+" or "-".
_EXTENSION = re.compile(r"[A-Za-z0-9_+-]{1,10}")


def extension_folder(name: str) -> str:
    """The folder a file called name goes in: its last extension, upper-cased,
    without the dot ("archive.tar.gz" → "GZ"), or NO_EXTENSION when it has
    none or the part after the last dot is not a plain extension (a space,
    a letter outside ASCII, or more than 10 characters)."""
    dot = name.rfind(".")
    if dot <= 0:  # no dot, or only a leading one
        return NO_EXTENSION
    suffix = name[dot + 1:]
    if not _EXTENSION.fullmatch(suffix):
        return NO_EXTENSION
    return suffix.upper()


def plan_extension(root: Path, files: list[str]) -> Plan:
    """Plan the Extension moves for files (relative POSIX paths under root).

    A file already in its folder (spelt any way: "apk/x.apk") stays and
    counts as already in place. The files with no extension move first: a
    file called "PDF", say, must be gone before a PDF folder can be made.
    """
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    folders: set[str] = set()
    unchanged = 0

    for rel in sorted(files, key=lambda rel: extension_folder(rel.rpartition("/")[2])
                      != NO_EXTENSION):
        parent, _, name = rel.rpartition("/")
        folder = extension_folder(name)
        # A folder on disk spelt another way matches by fold, as resolve_existing_dir does.
        if fold(parent) == fold(folder):
            unchanged += 1
            continue
        moves.append(Move(rel, allocator.allocate(f"{folder}/{name}")))
        folders.add(fold(folder))

    summary = []
    if moves:
        summary.append(f"{len(moves)} files → {len(folders)} folders")
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan("extension", moves, summary, unchanged)


def prepare_extension_plan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root for every file and plan the Extension moves. None if cancelled.

    Nothing is read but the names, so there is no progress to report.
    """
    root = Path(root)
    files = sorted(walk_all_files(root, include_subfolders, cancel))
    if cancel is not None and cancel.is_set():
        return None
    plan = plan_extension(root, files)
    if cancel is not None and cancel.is_set():
        return None
    return plan
