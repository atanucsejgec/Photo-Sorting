"""Carry out a plan: move the owner's files, journalled so every run can be undone.

Safety rules: a file is never overwritten and never deleted. A move across
drives copies, checks the copy (size and SHA-256), and only then removes the
source. Each move is journalled before it happens.
"""

import errno
import hashlib
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from photosort.journal import RunJournal, fsync_dir
from photosort.naming import TargetAllocator
from photosort.plan import Plan

PHOTOSORT_DIR = ".photosort"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _exists(path: Path) -> bool:
    """True if anything (even a broken symlink) is at path."""
    return os.path.lexists(path)


def move_file(src: Path, dst: Path) -> None:
    """Move src to dst. Raises FileExistsError if dst exists; never overwrites.

    On a case-insensitive filesystem a case variant of dst counts as existing.
    """
    src, dst = Path(src), Path(dst)
    if _exists(dst):
        raise FileExistsError(errno.EEXIST, "Target already exists", str(dst))
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        _copy_verify_move(src, dst)


def _copy_verify_move(src: Path, dst: Path) -> None:
    """Cross-drive move: copy to a temp name beside dst, verify, rename, unlink src.

    The source is removed only once a verified copy sits at dst.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=dst.parent, prefix=f".{dst.name}.", suffix=".photosort-tmp"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(src, tmp)
        with open(tmp, "r+b") as f:  # writable: Windows cannot fsync read-only
            os.fsync(f.fileno())
        if (tmp.stat().st_size != src.stat().st_size
                or _sha256(tmp) != _sha256(src)):
            raise IOError(f"Copy of {src} does not match the original")
        if _exists(dst):
            raise FileExistsError(errno.EEXIST, "Target already exists", str(dst))
        os.rename(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    fsync_dir(dst.parent)
    os.unlink(src)


@dataclass
class ExecResult:
    moved: int
    skipped: list[str]
    cancelled: bool
    run_id: str


def _make_parents(root: Path, dst: str, journal: RunJournal) -> None:
    """Create dst's missing parent folders top-down, journalling each one first.

    Journal first: a record for a folder that then is not made (or appeared
    meanwhile) is harmless, as undo only removes folders that are empty; a
    folder made with no record would be left behind by undo.
    """
    parts = dst.split("/")[:-1]
    for depth in range(1, len(parts) + 1):
        rel = "/".join(parts[:depth])
        path = root / rel
        if path.is_dir():
            continue
        journal.record_dir_created(rel)
        try:
            os.mkdir(path)
        except FileExistsError:
            continue  # appeared meanwhile


def _is_empty_dir(path: Path) -> bool:
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def _remove_empty_dirs(
    root: Path, moved: list[tuple[str, str]], planned_dsts: list[str],
    journal: RunJournal,
) -> None:
    """Remove source folders the moves left empty, walking up towards root.

    Never removes root, .photosort or anything in it, or a parent of any
    planned or actual dst.
    """
    keep = {""}
    for dst in planned_dsts + [dst for _, dst in moved]:
        parts = dst.split("/")[:-1]
        keep.update("/".join(parts[:d]) for d in range(1, len(parts) + 1))
    keep_folded = {k.casefold() for k in keep}

    candidates = {src.rpartition("/")[0] for src, _ in moved}
    # Deepest first, so a parent is tried after its children.
    for start in sorted(candidates, key=lambda r: r.count("/"), reverse=True):
        rel = start
        while rel and rel.casefold() not in keep_folded:
            if rel.split("/")[0].casefold() == PHOTOSORT_DIR:
                break
            path = root / rel
            if not _is_empty_dir(path):
                break
            # Journal first: a record for a folder that then stays is harmless
            # (undo's mkdir is a no-op), a removed folder with no record is lost.
            journal.record_dir_removed(rel)
            try:
                os.rmdir(path)  # fails unless empty
            except OSError:
                break
            rel = rel.rpartition("/")[0]


def execute(
    root: Path,
    plan: Plan,
    journal: RunJournal,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> ExecResult:
    """Carry out plan's moves under root, journalling each one.

    progress(processed, total) is called after each planned move is handled
    (moved or skipped). cancel is checked before every move.
    """
    root = Path(root)
    total = len(plan.moves)
    result = ExecResult(moved=0, skipped=[], cancelled=False, run_id=journal.run_id)
    moved: list[tuple[str, str]] = []
    # One allocator for the run: it lists each target folder once (listing a
    # big folder per move would be quadratic) and remembers the names it gave
    # out. move_file still refuses a target that appeared meanwhile.
    allocator = TargetAllocator(root)
    try:
        for n, move in enumerate(plan.moves, start=1):
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            src_path = root / move.src
            if not _exists(src_path):
                result.skipped.append(move.src)
            else:
                dst = allocator.allocate(move.dst)
                _make_parents(root, dst, journal)
                i = journal.record_intent(move.src, dst)
                move_file(src_path, root / dst)
                journal.record_done(i)
                moved.append((move.src, dst))
                result.moved += 1
            if progress is not None:
                progress(n, total)
        _remove_empty_dirs(root, moved, [m.dst for m in plan.moves], journal)
    except BaseException:
        try:
            journal.finish("failed")
        except Exception:
            pass  # keep the original error; the .jsonl still allows undo
        raise
    journal.finish("cancelled" if result.cancelled else "done")
    return result
