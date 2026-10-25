"""Camera mode: one folder per device, from the camera make and model.

Photos and videos go to a folder such as "OPPO CPH2581" (metadata.camera_folder).
Files with no camera info stay where they are.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from photosort.metadata import camera_folder, camera_of
from photosort.naming import TargetAllocator, fold
from photosort.plan import Move, Plan
from photosort.scanner import scan


def plan_camera(root: Path, files: list[str], folders: dict[str, str]) -> Plan:
    """Plan the camera-mode moves for files (relative POSIX paths under root).

    folders gives each file's camera folder, "" for a file with no camera info.
    """
    allocator = TargetAllocator(root)
    moves: list[Move] = []
    used: set[str] = set()
    no_camera = 0
    unchanged = 0

    for rel in files:
        folder = folders[rel]
        if not folder:
            no_camera += 1
            continue
        parent, _, name = rel.rpartition("/")
        # A folder on disk spelt another way matches by fold, as resolve_existing_dir does.
        if fold(parent) == fold(folder):
            unchanged += 1
            continue
        dst = allocator.allocate(f"{folder}/{name}")
        moves.append(Move(rel, dst))
        used.add(fold(dst.rpartition("/")[0]))

    summary = []
    if moves:
        summary.append(f"{len(moves)} files → {len(used)} camera folders")
    if no_camera:
        summary.append(f"{no_camera} files have no camera info and stay where they are")
    if unchanged:
        summary.append(f"{unchanged} already in place")
    return Plan("camera", moves, summary, unchanged)


def prepare_camera_plan(
    root: Path,
    include_subfolders: bool,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Plan | None:
    """Scan root, read every file's camera and plan the moves. None if cancelled."""
    root = Path(root)
    files = scan(root, include_subfolders)
    total = len(files)
    folders: dict[str, str] = {}
    for i, rel in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            return None
        camera = camera_of(root / rel)
        folders[rel] = camera_folder(*camera) if camera is not None else ""
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return plan_camera(root, files, folders)
