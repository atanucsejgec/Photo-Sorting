"""Bring old undo records and analysis caches in to the central store.

Before, every sorted folder kept its own ".photosort" folder: the undo
records of its runs in "runs/" (<run_id>.json and .jsonl) and its analysis
cache in "cache.db". Now they live in one place (appdata.data_dir()), and
bring_in moves the old ones there: those of the folders around root (up to
the filesystem root), of root itself and of every folder inside it.

Nothing is ever lost. An old run is deleted only once its copy has been read
back and verified (journal.import_run), or when it is already imported or the
owner forgot it. An old cache is deleted only once every entry is merged
(cache.merge_cache). Anything that fails is logged, left where it is, and
tried again next time; a read-only folder keeps its old files, and its runs
are not copied twice. ".photosort" and its "runs/" are removed once empty,
never while they hold anything else.

Two bring_in calls may run at once (a quiet one when a folder is picked on
Home, and one before a sort or an undo): one folder is brought in at a time,
under a lock, so a run is never imported twice.
"""

import errno
import logging
import os
import threading
from pathlib import Path

from photosort import appdata, journal
from photosort.cache import merge_cache
from photosort.executor import PHOTOSORT_DIR

_log = logging.getLogger("photosort")

_RUNS = "runs"
_CACHE = "cache.db"
_CACHE_SIDE_SUFFIXES = ("-journal", "-wal", "-shm")  # SQLite's side files

# One old folder is brought in at a time, by any thread.
_lock = threading.Lock()


def _cancelled(cancel) -> bool:
    return cancel is not None and cancel.is_set()


def old_folders(root: Path, cancel=None) -> list[Path]:
    """The folders around root, root itself and every folder inside it that
    hold an old .photosort folder; absolute paths.

    Folders around root are checked up to the filesystem root, outermost
    first. Inside root, .photosort folders themselves are not walked into,
    nor are symlinks or folders that can't be read.
    """
    root = Path(os.path.abspath(root))
    found = [a for a in reversed(root.parents) if os.path.isdir(a / PHOTOSORT_DIR)]
    if os.path.isdir(root / PHOTOSORT_DIR):
        found.append(root)
    stack = [root]
    while stack and not _cancelled(cancel):
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                subdirs = [Path(e.path) for e in entries
                           if e.is_dir(follow_symlinks=False)
                           and e.name.casefold() != PHOTOSORT_DIR]
        except OSError:
            continue
        for path in subdirs:
            if os.path.isdir(path / PHOTOSORT_DIR):
                found.append(path)
            stack.append(path)
    return found


def bring_in(root: Path, progress=None, cancel=None) -> int:
    """Bring in the old records of root's related folders (see old_folders);
    return how many runs were imported.

    Never raises for trouble with a folder or file: it is logged and skipped.
    progress(i, total) is called after each folder; cancel (an Event) stops
    it between folders.
    """
    try:
        folders = old_folders(root, cancel)
    except Exception:
        _log.exception("Old records of %s not brought in", root)
        return 0
    imported = 0
    for i, folder in enumerate(folders):
        if _cancelled(cancel):
            break
        try:
            with _lock:
                imported += _bring_in_folder(folder)
        except Exception:  # never let an old record stop a sort or an undo
            _log.exception("Old records in %s not brought in", folder / PHOTOSORT_DIR)
        if progress is not None:
            progress(i + 1, len(folders))
    if imported:
        _log.info("Brought %d old undo records in, for %s", imported, root)
    return imported


def _is_central(path: Path) -> bool:
    """Whether path is (or holds) the central store itself."""
    try:
        central = Path(os.path.realpath(appdata.data_dir()))
        here = Path(os.path.realpath(path))
    except (OSError, ValueError):
        return True  # can't tell: leave it alone
    return here == central or central.is_relative_to(here) or here.is_relative_to(central)


def _bring_in_folder(folder: Path) -> int:
    """Bring in folder/.photosort; return how many runs were imported."""
    old = folder / PHOTOSORT_DIR
    if _is_central(old):
        return 0
    imported = 0
    runs = old / _RUNS
    if os.path.isdir(runs):
        imported = _bring_in_runs(runs, folder)
    if os.path.lexists(old / _CACHE):
        _bring_in_cache(old / _CACHE)
    _remove_if_empty(runs)
    _remove_if_empty(old)
    return imported


def _bring_in_runs(runs: Path, folder: Path) -> int:
    try:
        with os.scandir(runs) as entries:
            run_ids = sorted(e.name[:-len(".json")] for e in entries
                             if e.name.endswith(".json") and e.is_file(follow_symlinks=False))
    except OSError as e:
        _log.warning("Old undo records in %s not read: %s", runs, e)
        return 0
    imported = 0
    for run_id in run_ids:
        try:
            new_id = journal.import_run(runs, run_id, folder)
        except (OSError, ValueError) as e:
            _log.warning("Old undo record %s in %s kept, not brought in: %s",
                         run_id, runs, e)
            continue
        if new_id is not None:
            imported += 1
        # Verified, already imported or forgotten: the old copy can go. The
        # .json first, so a half-deleted pair is never seen as a run again.
        try:
            for suffix in (".json", ".jsonl"):
                (runs / f"{run_id}{suffix}").unlink(missing_ok=True)
        except OSError as e:
            _log.info("Old undo record %s in %s is brought in but could not be "
                      "removed: %s", run_id, runs, e)
    journal.fsync_dir(runs)
    return imported


def _bring_in_cache(db: Path) -> None:
    if not merge_cache(db):
        _log.warning("Old analysis cache %s kept: it could not be read (hot journal?) "
                     "or merged; it is tried again next time", db)
        return
    try:
        db.unlink()
        for suffix in _CACHE_SIDE_SUFFIXES:
            db.with_name(db.name + suffix).unlink(missing_ok=True)
    except OSError as e:
        _log.info("Old analysis cache %s is merged but could not be removed: %s", db, e)


def _remove_if_empty(path: Path) -> None:
    try:
        os.rmdir(path)  # only succeeds if empty
    except FileNotFoundError:
        pass
    except OSError as e:
        if e.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            _log.info("Old folder %s not removed: %s", path, e)
