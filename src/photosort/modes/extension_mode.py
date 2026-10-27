"""Extension mode: every file, of any type, into a folder named after its extension.

All .apk files go into APK, all .exe into EXE, and so on; a file with no
extension (or one that is not a plain extension, see extension_folder) goes
into No extension. The folders are made in the sorted folder itself.
"""

import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

from photosort.naming import TargetAllocator, fit_name, fold, real_dir, resolve_existing_dir
from photosort.plan import Move, Plan
from photosort.scanner import walk_all_files
from photosort.thresholds import FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES

NO_EXTENSION = "No extension"

# What counts as an extension: 1 to 10 ASCII letters, digits, "_", "+" or "-".
_EXTENSION = re.compile(r"[A-Za-z0-9_+-]{1,10}")


def extension_folder(name: str) -> str:
    """The folder a file called name goes in: its last extension, upper-cased,
    without the dot ("archive.tar.gz" → "GZ"), or NO_EXTENSION when it has
    none or the part after the last dot is not a plain extension (a space,
    a letter outside ASCII, or more than 10 characters). The name is made
    safe as other modes' folder names are (fit_name): a name Windows
    reserves, or one the scanner skips, gets a "_" first ("thesis.aux" →
    "_AUX", "x.duplicates" → "_DUPLICATES")."""
    dot = name.rfind(".")
    if dot <= 0:  # no dot, or only a leading one
        return NO_EXTENSION
    suffix = name[dot + 1:]
    if not _EXTENSION.fullmatch(suffix):
        return NO_EXTENSION
    return fit_name(suffix.upper(), FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)


def _taken_names(root: Path, leaving: set[str]) -> set[str]:
    """The names (folded) that no folder of this mode can use in root.

    A link (a symlink or junction, even to a folder) takes its name: nothing
    is ever moved through one. Anything else that is not a folder takes its
    name too, unless it is a file named in leaving (it moves away first) or
    a real folder holds the name in another case (that folder is used)."""
    try:
        with os.scandir(root) as entries:
            found = []
            for entry in entries:
                link = entry.is_symlink() or entry.is_junction()
                kind = "link" if link else "dir" if entry.is_dir() else "other"
                found.append((fold(entry.name), entry.name, kind))
    except OSError:
        return set()
    folders = {key for key, _, kind in found if kind == "dir"}
    return {key for key, name, kind in found
            if kind == "link" or (kind == "other" and name not in leaving
                                  and key not in folders)}


def _real_folder(root: Path, folder: str) -> bool:
    """Whether the folder used for folder in root (spelt as on disk, the
    exact spelling first, see resolve_existing_dir) is a real folder, not a
    link to one. Where names differ in case, a link "pdf" can sit beside a
    real "PDF" folder, which is used, as in Date (all files)."""
    return real_dir(root / resolve_existing_dir(root, folder))


def plan_extension(root: Path, files: list[str],
                   progress: Callable[[int, int], None] | None = None) -> Plan:
    """Plan the Extension moves for files (relative POSIX paths under root).

    A file already in its folder (spelt any way: "apk/x.apk") stays and
    counts as already in place. The files with no extension move first: a
    file called "PDF", say, must be gone before a PDF folder can be made.
    When a file called "No extension" (any case) is in root, the No
    extension folder can't be made: the files with no extension stay.
    Likewise, when something that stays in root (such as a file "PDF" that
    can't go to No extension) holds a folder's name, or a link does, that
    folder can't be made or used: its files stay, so the run never stops
    half-way and never moves anything through a link. But a real folder
    the moves would go into (see _real_folder) is used. The summary counts
    those apart from the files already in place. progress(i, total) is
    called after each file.
    """
    root = Path(root)
    allocator = TargetAllocator(root)
    no_extension_blocked = (fold(NO_EXTENSION) in _taken_names(root, set())
                            and not _real_folder(root, NO_EXTENSION))
    leaving = set() if no_extension_blocked else {
        rel for rel in files
        if "/" not in rel and extension_folder(rel) == NO_EXTENSION}
    taken = _taken_names(root, leaving)
    blocked_folders: dict[str, bool] = {}  # folder -> whether its files stay

    def is_blocked(folder: str) -> bool:
        if folder not in blocked_folders:
            blocked_folders[folder] = (no_extension_blocked if folder == NO_EXTENSION
                                       else fold(folder) in taken
                                       and not _real_folder(root, folder))
        return blocked_folders[folder]

    moves: list[Move] = []
    folders: set[str] = set()
    unchanged = stuck = 0
    total = len(files)

    in_order = sorted(files, key=lambda rel: extension_folder(rel.rpartition("/")[2])
                      != NO_EXTENSION)
    for i, rel in enumerate(in_order, start=1):
        parent, _, name = rel.rpartition("/")
        folder = extension_folder(name)
        # A folder on disk spelt another way matches by fold, as resolve_existing_dir does.
        if fold(parent) == fold(folder):
            unchanged += 1
        elif is_blocked(folder):
            stuck += 1
        else:
            moves.append(Move(rel, allocator.allocate(f"{folder}/{name}")))
            folders.add(fold(folder))
        if progress is not None:
            progress(i, total)

    summary = []
    if moves:
        summary.append(f"{len(moves)} files → {len(folders)} folders")
    if unchanged:
        summary.append(f"{unchanged} already in place")
    if stuck:
        summary.append(f"{stuck} files stay: a file or link with their folder's name "
                       "is in the way")
    return Plan("extension", moves, summary, unchanged)


def prepare_extension_plan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root for every file and plan the Extension moves. None if cancelled.

    progress(i, total) is called after each file is planned (the total is
    known once the folders are listed).
    """
    root = Path(root)
    files = sorted(walk_all_files(root, include_subfolders, cancel))
    if cancel is not None and cancel.is_set():
        return None
    plan = plan_extension(root, files, progress)
    if cancel is not None and cancel.is_set():
        return None
    return plan
