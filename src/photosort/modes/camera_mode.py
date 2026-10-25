"""Camera mode: one folder per device, from the camera make and model.

Photos and videos go to a folder such as "OPPO CPH2581" (metadata.camera_folder).
Files with no camera info stay where they are.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from photosort.metadata import camera_folder, camera_of
from photosort.modes._scan import read_each
from photosort.naming import TargetAllocator, fold
from photosort.plan import Move, Plan


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
    found = read_each(root, include_subfolders, _folder_of, progress, cancel)
    if found is None:
        return None
    return plan_camera(Path(root), *found)


def _folder_of(path: Path) -> str:
    """path's camera folder, "" if it has no camera info."""
    camera = camera_of(path)
    return camera_folder(*camera) if camera is not None else ""
