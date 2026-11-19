"""Undo a run: put every file it moved back where it was.

Undo trusts the disk, never the done marks: a done mark can exist while the
file never left its source (power loss), and a move with no done mark can
have its file at the source, the target, or both (a cross-drive copy whose
source could not be removed). Every recorded move is therefore checked
against the disk. Nothing is ever overwritten or deleted; when the original
place is taken, even by a name differing only in case (the owner's drive is
NTFS), the file comes back as "name (1).ext" beside it. The one exception
is an empty folder the run itself made there (a file called "PDF" moved
away, then a PDF folder made in its place): that folder is removed first,
so the file comes back under its own name.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from photosort.executor import PHOTOSORT_DIR, move_file
from photosort.journal import (
    RunInfo, load_all_runs, load_dirs, load_moves, load_runs, mark_undone,
)
from photosort.naming import TargetAllocator, resolve_existing_dir


@dataclass(frozen=True)
class BlockingRun:
    """A run to undo first, and the folder whose undo history holds it.

    folder is "" for the folder being undone, a POSIX path relative to it
    for a folder inside it, and an absolute path for a folder around it (or
    for the same folder recorded in another case).
    """

    run_id: str
    folder: str

    def __str__(self) -> str:
        return f"{self.run_id} (in {self.folder})" if self.folder else self.run_id


class UndoBlocked(Exception):
    """A newer run, not yet undone, moved files this run put in place."""

    def __init__(self, blocking: list[BlockingRun]) -> None:
        super().__init__(f"Undo these runs first: {', '.join(map(str, blocking))}")
        self.blocking = blocking


@dataclass
class UndoResult:
    restored: int
    missing: list[str] = field(default_factory=list)  # srcs whose file is gone
    renamed: list[tuple[str, str]] = field(default_factory=list)  # (src, put back as)
    # (src, error message) of files that could not be moved back this time;
    # when any, the run is left not undone so undo can be tried again.
    failed: list[tuple[str, str]] = field(default_factory=list)


def _run_index(root: Path, run_id: str) -> tuple[list, int]:
    runs = load_runs(root)
    for k, run in enumerate(runs):
        if run.run_id == run_id:
            return runs, k
    raise ValueError(f"No such run: {run_id}")


def _folded(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


def related_roots(root: Path) -> list[Path]:
    """Return the roots in the central store whose runs can touch root's files.

    They are root itself, the folders around it (a run there with subfolders
    on) and the folders inside it, compared by whole path parts and
    case-insensitively: on a case-insensitive drive "A" and "a" are one
    folder, and relating too much only adds runs to check, since
    blocking_runs still compares the actual moves. "/x/ab" is never related
    to "/x/a". No disk walk: the roots are those of the recorded runs, each
    listed once, absolute. Order: root itself, then those around it (nearest
    first), then those inside it.
    """
    root = Path(os.path.abspath(root))
    mine = _folded(root)
    n = len(mine)
    same, around, inside = [], [], []
    for r in sorted({run.root for run in load_all_runs()}):
        parts = _folded(r)
        if parts == mine:
            same.append(r)
        elif len(parts) < n and mine[:len(parts)] == parts:
            around.append(r)
        elif len(parts) > n and parts[:n] == mine:
            inside.append(r)
    around.sort(key=lambda r: len(r.parts), reverse=True)  # stable: ties by path
    return same + around + inside


def _in_root(prefix: tuple[str, ...], src: str) -> str | None:
    """Return src (relative to a folder around root) relative to root, or None.

    prefix is root's path below that folder, casefolded part by part.
    """
    parts = src.split("/")
    n = len(prefix)
    if len(parts) > n and tuple(p.casefold() for p in parts[:n]) == prefix:
        return "/".join(parts[n:])
    return None


def blocking_runs(
    root: Path, run_id: str, related: list[Path] | None = None,
) -> list[BlockingRun]:
    """Return the newer, not undone runs that moved any file this run placed.

    The runs of root and of its related_roots all count (pass related in to
    reuse one lookup for several runs). Newest first. Every recorded move
    counts, done or not, and paths are compared case-insensitively.
    """
    root = Path(os.path.abspath(root))
    runs, k = _run_index(root, run_id)
    this = runs[k]
    placed = {m.dst.casefold() for m in load_moves(root, run_id)}
    if related is None:
        related = related_roots(root)

    # (folder, how it is shown, its src -> root-relative path or None)
    sources: list[tuple[Path, str, Callable[[str], str | None]]] = [
        (root, "", lambda src: src)
    ]
    mine = _folded(root)
    n = len(mine)
    for folder in dict.fromkeys(Path(os.path.abspath(f)) for f in related):
        parts = _folded(folder)
        if folder == root:
            continue  # already the first source
        if parts == mine:  # root itself, recorded in another case
            sources.append((folder, str(folder), lambda src: src))
        elif len(parts) < n and mine[:len(parts)] == parts:
            prefix = mine[len(parts):]
            sources.append((folder, str(folder),
                            lambda src, prefix=prefix: _in_root(prefix, src)))
        elif len(parts) > n and parts[:n] == mine:
            inner = "/".join(folder.parts[n:])
            sources.append((folder, inner, lambda src, inner=inner: f"{inner}/{src}"))

    found: list[tuple[RunInfo, BlockingRun]] = []
    for folder, shown, to_root in sources:
        for run in runs if folder == root else load_runs(folder):
            if run.undone or (run.started, run.run_id) <= (this.started, this.run_id):
                continue
            for m in load_moves(folder, run.run_id):
                rel = to_root(m.src)
                if rel is not None and rel.casefold() in placed:
                    found.append((run, BlockingRun(run.run_id, shown)))
                    break
    found.sort(key=lambda f: (f[0].started, f[0].run_id), reverse=True)
    return [blocking for _, blocking in found]


def dependents(root: Path, run_id: str) -> list[RunInfo]:
    """Return the older, not undone runs that this run blocks, newest first.

    These are the runs, of root or any related root, that blocking_runs says
    must wait for this one: this run moved files they placed. Forgetting
    this run alone would leave them impossible to undo. Raises ValueError
    if the run is unknown, and OSError or ValueError if a record can't be
    read.
    """
    root = Path(os.path.abspath(root))
    runs, k = _run_index(root, run_id)
    this = runs[k]
    roots = set(related_roots(root))
    related: dict[Path, list[Path]] = {}  # each root's related_roots, found once
    found = []
    for run in load_all_runs():  # newest first
        if (run.root not in roots or run.undone
                or (run.started, run.run_id) >= (this.started, this.run_id)):
            continue
        if run.root not in related:
            related[run.root] = related_roots(run.root)
        if any(b.run_id == run_id
               for b in blocking_runs(run.root, run.run_id, related[run.root])):
            found.append(run)
    return found


def _is_ours(rel: str) -> bool:
    return rel.split("/")[0].casefold() == PHOTOSORT_DIR


def _clear_our_empty_folder(root: Path, wanted: str, created: dict[str, str]) -> str | None:
    """Remove the folder at wanted (any case) if the run made it and it is empty.

    created maps each folder the run made, casefolded, to its path. Returns
    the casefolded path of the folder removed, else None. A folder the run
    did not make, or that holds anything, is never touched.
    """
    key = wanted.casefold()
    rel = created.get(key)
    if rel is None:
        return None
    path = root / rel
    if path.is_symlink() or not path.is_dir():
        return None
    try:
        os.rmdir(path)  # only succeeds if empty
    except OSError:
        return None
    return key


def undo_run(
    root: Path,
    run_id: str,
    progress: Callable[[int, int], None] | None = None,
) -> UndoResult:
    """Move the run's files back, restore its removed folders, remove its new ones.

    Raises ValueError if the run is unknown or already undone, and
    UndoBlocked if a newer run must be undone first. progress(processed,
    total) is called after each recorded move.

    A file that can't be moved back (an OSError, such as a file held open)
    is recorded in result.failed and the others still go back. The run is
    then not marked undone, so undoing it again retries just what is left
    (undo trusts the disk, so files already back are left alone).
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
    created, removed = load_dirs(root, run_id)
    ours = {rel.casefold(): rel for rel in created if not _is_ours(rel)}
    cleared: set[str] = set()  # casefolded: the run's folders removed to make room
    # One allocator lists each folder once (casefolded) and remembers the
    # names it gave out. Its listing can be stale only by still holding names
    # undo has since moved away, so a clash is re-checked against the disk.
    allocator = TargetAllocator(root)
    for n, move in enumerate(reversed(moves), start=1):
        src, dst = root / move.src, root / move.dst
        if os.path.lexists(dst):
            try:
                folder, _, name = move.src.rpartition("/")
                # A "SUB" made after the run is the same folder (on NTFS) as
                # the run's "sub": reuse it rather than create a twin beside it.
                folder = resolve_existing_dir(root, folder)
                wanted = f"{folder}/{name}" if folder else name
                gone = _clear_our_empty_folder(root, wanted, ours)
                if gone is not None:
                    cleared.add(gone)
                target = allocator.allocate(wanted)
                if target != wanted:
                    allocator = TargetAllocator(root)
                    target = allocator.allocate(wanted)
                (root / target).parent.mkdir(parents=True, exist_ok=True)
                move_file(dst, root / target)
            except OSError as e:
                # E.g. the file is open in another program (Windows locks it).
                # Carry on with the rest; the run stays undoable for a retry.
                result.failed.append((move.src, str(e) or type(e).__name__))
                allocator = TargetAllocator(root)  # release the name it reserved
            else:
                if target != move.src:
                    # The original place is taken (both copies of a half-finished
                    # cross-drive move, a new file, or a case twin): keep both.
                    result.renamed.append((move.src, target))
                result.restored += 1
        elif not os.path.lexists(src):
            result.missing.append(move.src)
        # else: the file never left (or is already back); nothing to do.
        if progress is not None:
            progress(n, total)

    for rel in sorted(removed, key=lambda r: r.count("/")):  # parents first
        if _is_ours(rel):
            continue
        try:
            (root / resolve_existing_dir(root, rel)).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # a file sits there now; the files themselves are back
    for rel in sorted(created, key=lambda r: r.count("/"), reverse=True):
        if _is_ours(rel) or rel.casefold() in cleared:
            continue
        try:
            os.rmdir(root / rel)  # only succeeds if empty
        except OSError:
            pass  # holds something (or is already gone): leave it
    if not result.failed:
        mark_undone(root, run_id)
    return result
