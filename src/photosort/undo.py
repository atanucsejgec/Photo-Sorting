"""Undo a run: put every file it moved back where it was.

Undo trusts the disk, never the done marks: a done mark can exist while the
file never left its source (power loss), and a move with no done mark can
have its file at the source, the target, or both (a cross-drive copy whose
source could not be removed). Every recorded move is therefore checked
against the disk. Nothing is ever overwritten or deleted; when the original
place is taken, even by a name differing only in case (the owner's drive is
NTFS), the file comes back as "name (1).ext" beside it.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from photosort.executor import PHOTOSORT_DIR, move_file
from photosort.journal import load_dirs, load_moves, load_runs, mark_undone
from photosort.naming import TargetAllocator, resolve_existing_dir


class UndoBlocked(Exception):
    """A newer run, not yet undone, moved files this run put in place."""

    def __init__(self, blocking: list[str]) -> None:
        super().__init__(f"Undo these runs first: {', '.join(blocking)}")
        self.blocking = blocking


@dataclass
class UndoResult:
    restored: int
    missing: list[str] = field(default_factory=list)  # srcs whose file is gone
    renamed: list[tuple[str, str]] = field(default_factory=list)  # (src, put back as)


def _run_index(root: Path, run_id: str) -> tuple[list, int]:
    runs = load_runs(root)
    for k, run in enumerate(runs):
        if run.run_id == run_id:
            return runs, k
    raise ValueError(f"No such run: {run_id}")


def blocking_runs(root: Path, run_id: str) -> list[str]:
    """Return the newer, not undone runs that moved any file this run placed.

    Newest first. Every recorded move counts, done or not.
    """
    root = Path(root)
    runs, k = _run_index(root, run_id)
    placed = {m.dst.casefold() for m in load_moves(root, run_id)}
    return [
        newer.run_id for newer in runs[:k]
        if not newer.undone
        and any(m.src.casefold() in placed for m in load_moves(root, newer.run_id))
    ]


def _is_ours(rel: str) -> bool:
    return rel.split("/")[0].casefold() == PHOTOSORT_DIR


def undo_run(
    root: Path,
    run_id: str,
    progress: Callable[[int, int], None] | None = None,
) -> UndoResult:
    """Move the run's files back, restore its removed folders, remove its new ones.

    Raises ValueError if the run is unknown or already undone, and
    UndoBlocked if a newer run must be undone first. progress(processed,
    total) is called after each recorded move.
    """
    root = Path(root)
    runs, k = _run_index(root, run_id)
    if runs[k].undone:
        raise ValueError(f"Run {run_id} is already undone")
    blocking = blocking_runs(root, run_id)
    if blocking:
        raise UndoBlocked(blocking)

    result = UndoResult(restored=0)
    moves = load_moves(root, run_id)
    total = len(moves)
    # One allocator lists each folder once (casefolded) and remembers the
    # names it gave out. Its listing can be stale only by still holding names
    # undo has since moved away, so a clash is re-checked against the disk.
    allocator = TargetAllocator(root)
    for n, move in enumerate(reversed(moves), start=1):
        src, dst = root / move.src, root / move.dst
        if os.path.lexists(dst):
            folder, _, name = move.src.rpartition("/")
            # A "SUB" made after the run is the same folder (on NTFS) as the
            # run's "sub": reuse it rather than create a twin beside it.
            folder = resolve_existing_dir(root, folder)
            wanted = f"{folder}/{name}" if folder else name
            target = allocator.allocate(wanted)
            if target != wanted:
                allocator = TargetAllocator(root)
                target = allocator.allocate(wanted)
            if target != move.src:
                # The original place is taken (both copies of a half-finished
                # cross-drive move, a new file, or a case twin): keep both.
                result.renamed.append((move.src, target))
            (root / target).parent.mkdir(parents=True, exist_ok=True)
            move_file(dst, root / target)
            result.restored += 1
        elif not os.path.lexists(src):
            result.missing.append(move.src)
        # else: the file never left (or is already back); nothing to do.
        if progress is not None:
            progress(n, total)

    created, removed = load_dirs(root, run_id)
    for rel in sorted(removed, key=lambda r: r.count("/")):  # parents first
        if _is_ours(rel):
            continue
        try:
            (root / resolve_existing_dir(root, rel)).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # a file sits there now; the files themselves are back
    for rel in sorted(created, key=lambda r: r.count("/"), reverse=True):
        if _is_ours(rel):
            continue
        try:
            os.rmdir(root / rel)  # only succeeds if empty
        except OSError:
            pass  # holds something (or is already gone): leave it
    mark_undone(root, run_id)
    return result
