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

A Preview run may have moved files into folders outside its root (journal
paths there are absolute, the folders listed in the run's also_roots). Undo
then refuses, moving nothing, while any of those folders is not there (an
unplugged drive), and a newer run in or around one of them that moved a
file this run placed there blocks it, as inside the root.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from photosort.executor import PHOTOSORT_DIR, move_file
from photosort.journal import (
    JournalMove, RunInfo, abs_path, load_all_runs, load_dirs, load_moves, load_runs,
    mark_undone, mount_of,
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


class FolderMissing(Exception):
    """None of the run's files are in its folder (a drive unplugged, its
    empty mount point left behind), or, with outside, a folder outside the
    root that the run moved files into is not there: nothing was done.
    str() is for the owner."""

    def __init__(self, root: Path, outside: bool = False) -> None:
        if outside:
            super().__init__(f"{root} isn't there — is the drive plugged in?")
        else:
            super().__init__(f"None of this sort's files are in {root}. "
                             "Is the drive plugged in?")
        self.root = root


@dataclass
class UndoResult:
    restored: int
    missing: list[str] = field(default_factory=list)  # srcs whose file is gone
    renamed: list[tuple[str, str]] = field(default_factory=list)  # (src, put back as)
    # (src, error message) of files that could not be moved back this time;
    # when any, the run is left not undone so undo can be tried again.
    failed: list[tuple[str, str]] = field(default_factory=list)


def _outside(p: str) -> bool:
    """Whether a recorded path is absolute: outside the run's root."""
    return Path(p).is_absolute()


def files_in(folder: Path, moves: list[JournalMove], back_home: bool = True) -> bool:
    """Whether folder holds at least one file of the done moves, at its
    recorded place (dst) or, with back_home, back where it was (src).
    Absolute paths (outside the run's root) say nothing about folder and
    are left out."""
    folder = Path(folder)
    return any(m.done and ((not _outside(m.dst) and os.path.lexists(abs_path(folder, m.dst)))
                           or (back_home and not _outside(m.src)
                               and os.path.lexists(abs_path(folder, m.src))))
               for m in moves)


def _left(moves: list[JournalMove], where: Callable[[str], bool]) -> list[JournalMove]:
    """The done moves whose dst is where(dst), and that no later move of the
    run took on (a put-back, or a move on from there): where the run left
    its files."""
    taken_on: set[str] = set()
    left = []
    for m in reversed(moves):
        if m.done and where(m.dst) and m.dst.casefold() not in taken_on:
            left.append(m)
        taken_on.add(m.src.casefold())
    return left


def _holds_its_files(root: Path, moves: list[JournalMove]) -> bool:
    """Whether root (a folder) holds at least one of the run's files, if it
    should: files_in for a run all within root, as always. For a run that
    also moved files outside root, the files it left within root count (at
    dst, or back at src), and with none left there the folder is enough."""
    if not any(_outside(m.src) or _outside(m.dst) for m in moves):
        return not any(m.done for m in moves) or files_in(root, moves)
    left = _left(moves, lambda dst: not _outside(dst))
    return not left or files_in(root, left)


def unmounted(run: RunInfo) -> Path | None:
    """A folder of the run (its root or an also_root) that is not on the
    mount point it was recorded on (a drive unplugged, its empty mount point
    or nothing left), or None. A run recorded before mounts were kept is
    never checked."""
    for folder, mount in run.mounts or ():
        if Path(mount_of(folder)) != mount:
            return folder
    return None


def root_present(run: RunInfo) -> bool:
    """Whether the run's folder is there: a folder, holding at least one
    file of its done moves (_holds_its_files). An empty mount point, left
    behind by an unplugged drive, is not, nor is any folder of the run off
    the mount point it was recorded on (unmounted). A run that moved
    nothing (or left nothing within its folder) needs the folder only; one whose record
    can't be read is taken as there (its undo says what is wrong)."""
    if not run.root.is_dir() or unmounted(run) is not None:
        return False
    try:
        moves = load_moves(run.root, run.run_id)
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return _holds_its_files(run.root, moves)


def outside_folders(run: RunInfo, moves: list[JournalMove]) -> list[Path]:
    """The folders outside run's root that it moved files into (or back out
    of): its also_roots, and the folder of every absolute path recorded."""
    found = dict.fromkeys(Path(os.path.abspath(a)) for a in run.also_roots)
    for m in moves:
        for p in (m.src, m.dst):
            if _outside(p):
                found.setdefault(Path(os.path.abspath(Path(p).parent)), None)
    return list(found)


def _outside_there(folder: Path, moves: list[JournalMove]) -> bool:
    """Whether the outside folder holds at least one file the run left in it
    (_left), if it left any. An empty mount point, left by an unplugged
    drive, does not."""
    key = _folded(folder)
    left = _left(moves, lambda dst: _outside(dst)
                 and _folded(Path(os.path.abspath(Path(dst).parent))) == key)
    return not left or any(os.path.lexists(m.dst) for m in left)


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


def _related(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether two folders (casefolded parts) are one, or one is inside the other."""
    n = min(len(a), len(b))
    return a[:n] == b[:n]


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
    reuse one lookup for several runs), and for a run that placed files
    outside root (outside_folders), those of every folder related to each
    outside folder. Newest first. Every recorded move counts, done or not,
    and paths are compared case-insensitively.
    """
    root = Path(os.path.abspath(root))
    runs, k = _run_index(root, run_id)
    this = runs[k]
    moves = load_moves(root, run_id)
    placed = {m.dst.casefold() for m in moves}
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

    def newer(run: RunInfo) -> bool:
        return not run.undone and (run.started, run.run_id) > (this.started, this.run_id)

    found: dict[str, tuple[RunInfo, BlockingRun]] = {}
    for folder, shown, to_root in sources:
        for run in runs if folder == root else load_runs(folder):
            if not newer(run) or run.run_id in found:
                continue
            for m in load_moves(folder, run.run_id):
                rel = to_root(m.src)
                if rel is not None and rel.casefold() in placed:
                    found[run.run_id] = (run, BlockingRun(run.run_id, shown))
                    break

    outside = outside_folders(this, moves)
    if outside:
        # Files placed outside the root: the runs of every folder related to
        # each outside folder (and root's own) are checked too, by absolute
        # path, compared case-insensitively.
        placed_at = {_folded(Path(os.path.abspath(abs_path(root, m.dst)))) for m in moves}
        shown_as = {folder: shown for folder, shown, _ in sources}
        folders = dict.fromkeys(shown_as)
        for target in outside:
            folders.update(dict.fromkeys(Path(os.path.abspath(r))
                                         for r in related_roots(target)))
        for folder in folders:
            for run in load_runs(folder):
                if not newer(run) or run.run_id in found:
                    continue
                if any(_folded(Path(os.path.abspath(abs_path(folder, m.src)))) in placed_at
                       for m in load_moves(folder, run.run_id)):
                    found[run.run_id] = (run, BlockingRun(run.run_id,
                                                          shown_as.get(folder, str(folder))))
    ordered = sorted(found.values(), key=lambda f: (f[0].started, f[0].run_id), reverse=True)
    return [blocking for _, blocking in ordered]


def dependents(root: Path, run_id: str) -> list[RunInfo]:
    """Return the older, not undone runs that this run blocks, newest first.

    These are the runs, of root or any related root, or that placed files in
    a folder related to root (their also_roots), that blocking_runs says
    must wait for this one: this run moved files they placed. Forgetting
    this run alone would leave them impossible to undo. Raises ValueError
    if the run is unknown, and OSError or ValueError if a record can't be
    read.
    """
    root = Path(os.path.abspath(root))
    runs, k = _run_index(root, run_id)
    this = runs[k]
    roots = set(related_roots(root))
    mine = _folded(root)
    related: dict[Path, list[Path]] = {}  # each root's related_roots, found once
    found = []
    for run in load_all_runs():  # newest first
        if (run.undone or (run.started, run.run_id) >= (this.started, this.run_id)
                or not (run.root in roots
                        or any(_related(_folded(Path(os.path.abspath(a))), mine)
                               for a in run.also_roots))):
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
    path = abs_path(root, rel)
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

    Raises ValueError if the run is unknown or already undone,
    UndoBlocked if a newer run must be undone first, and FolderMissing
    (nothing done, the run still undoable) if the run had done moves and
    not one of its files is in root, moved or back; for a run that moved
    files outside root, also (checked first, before anything moves, as is
    every folder of the run being on its recorded mount point) if one
    of those folders is not there (outside_folders, _outside_there), or root
    is not, or none of its files within root are. progress(processed,
    total) is called after each recorded move.

    A file that can't be moved back (an OSError, such as a file held open)
    is recorded in result.failed and the others still go back. The run is
    then not marked undone, so undoing it again retries just what is left
    (undo trusts the disk, so files already back are left alone).
    """
    root = Path(root)
    runs, k = _run_index(root, run_id)
    this = runs[k]
    if this.undone:
        raise ValueError(f"Run {run_id} is already undone")
    # All or nothing: an unplugged drive must not leave a half undone run.
    gone = unmounted(this)
    if gone is not None:
        raise FolderMissing(gone, outside=True)
    moves = load_moves(root, run_id)
    outside = outside_folders(this, moves)
    for folder in outside:
        if not folder.is_dir():
            raise FolderMissing(folder, outside=True)
    blocking = blocking_runs(root, run_id)
    if blocking:
        raise UndoBlocked(blocking)
    for folder in outside:
        if not _outside_there(folder, moves):  # an empty mount point
            raise FolderMissing(folder, outside=True)
    if outside and not (root.is_dir() and _holds_its_files(root, moves)):
        # Files would come back from outside: the root itself must be there
        # (not an empty mount point), before anything moves.
        raise FolderMissing(root)

    result = UndoResult(restored=0)
    total = len(moves)
    created, removed = load_dirs(root, run_id)
    ours = {rel.casefold(): rel for rel in created if not _is_ours(rel) and not _outside(rel)}
    cleared: set[str] = set()  # casefolded: the run's folders removed to make room
    # One allocator per folder put back into (root, or a folder outside it)
    # lists each folder once (casefolded) and remembers the names it gave
    # out. Its listing can be stale only by still holding names undo has
    # since moved away, so a clash is re-checked against the disk.
    allocators: dict[Path, TargetAllocator] = {}
    for n, move in enumerate(reversed(moves), start=1):
        src, dst = abs_path(root, move.src), abs_path(root, move.dst)
        # Where the file goes back: a path under root, or a name in the
        # folder outside root it came from (a put-back from there).
        base = src.parent if _outside(move.src) else root
        if os.path.lexists(dst):
            try:
                if base is root:
                    folder, _, name = move.src.rpartition("/")
                    # A "SUB" made after the run is the same folder (on NTFS) as
                    # the run's "sub": reuse it rather than create a twin beside it.
                    folder = resolve_existing_dir(root, folder)
                    wanted = f"{folder}/{name}" if folder else name
                    gone = _clear_our_empty_folder(root, wanted, ours)
                    if gone is not None:
                        cleared.add(gone)
                else:
                    wanted = src.name
                allocator = allocators.setdefault(base, TargetAllocator(base))
                target = allocator.allocate(wanted)
                if target != wanted:
                    allocators[base] = allocator = TargetAllocator(base)
                    target = allocator.allocate(wanted)
                if base is root:
                    (root / target).parent.mkdir(parents=True, exist_ok=True)
                move_file(dst, base / target)  # never made outside: it was checked
            except OSError as e:
                # E.g. the file is open in another program (Windows locks it).
                # Carry on with the rest; the run stays undoable for a retry.
                result.failed.append((move.src, str(e) or type(e).__name__))
                allocators.pop(base, None)  # release the name it reserved
            else:
                back = target if base is root else (base / target).as_posix()
                if back != move.src:
                    # The original place is taken (both copies of a half-finished
                    # cross-drive move, a new file, or a case twin): keep both.
                    result.renamed.append((move.src, back))
                result.restored += 1
        elif not os.path.lexists(src):
            result.missing.append(move.src)
        # else: the file never left (or is already back); nothing to do.
        if progress is not None:
            progress(n, total)

    if (any(m.done for m in moves) and result.restored == 0 and not result.failed
            and len(result.missing) == total):
        # Not one file is there, not even back home: the folder is not the
        # one sorted (a drive unplugged, its empty mount point left). Undo
        # nothing, make nothing there, and keep the run undoable.
        raise FolderMissing(root)

    for rel in sorted(removed, key=lambda r: r.count("/")):  # parents first
        if _is_ours(rel):
            continue
        try:
            if _outside(rel):  # only within a folder there: never a drive's path
                Path(rel).mkdir(exist_ok=True)
            else:
                (root / resolve_existing_dir(root, rel)).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # a file sits there now; the files themselves are back
    for rel in sorted(created, key=lambda r: r.count("/"), reverse=True):
        if _is_ours(rel) or rel.casefold() in cleared:
            continue
        try:
            os.rmdir(abs_path(root, rel))  # only succeeds if empty
        except OSError:
            pass  # holds something (or is already gone): leave it
    if not result.failed:
        mark_undone(root, run_id)
    return result
