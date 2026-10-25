"""The scan loop the per-file plan modes share (camera, type, location)."""

import threading
from collections.abc import Callable
from pathlib import Path

from photosort.scanner import scan


def read_each[T](
    root: Path,
    include_subfolders: bool,
    read: Callable[[Path], T],
    progress: Callable[[int, int], None] | None,
    cancel: threading.Event | None,
) -> tuple[list[str], dict[str, T]] | None:
    """Scan root and read each media file found.

    Returns the files (relative POSIX paths, in scan order) and what read
    gave for each (read is called with the file's full path). Cancel is
    checked before each file and once more at the end, and progress(i, total)
    is called after each file. None if cancelled.
    """
    root = Path(root)
    files = scan(root, include_subfolders)
    total = len(files)
    values: dict[str, T] = {}
    for i, rel in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            return None
        values[rel] = read(root / rel)
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return files, values
