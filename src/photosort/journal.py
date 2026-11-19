"""The undo record of a run, kept in the app's central runs folder
(appdata.runs_dir()), one folder for every sorted folder.

Each run has two files:

- "<run_id>.jsonl": append-only, one JSON object per line, flushed and
  fsynced after every line. A move is recorded ("move") before it happens and
  marked ("done") after, so a crashed or cancelled run can still be undone.
- "<run_id>.json": the run's metadata, rewritten atomically.

The metadata's "root" is the sorted folder's absolute path, symlinks
resolved: every root enters the store through norm_root. A run belongs to
the folder whose path equals it.
Move paths are POSIX strings relative to root, so a moved folder only needs
its root re-pointed (relocate_root). JSON is written ASCII-escaped, so Linux
names that are not valid UTF-8 (lone surrogates in Python) round-trip
unchanged.

A folder that was moved is re-pointed (relocate_root) under a marker,
"relocating.json": the {old, new} pair is written there first and taken off
only once every record is re-pointed, so a pair left half done (a failed
write, the app closed) is finished by finish_relocations, which every undo
calls first.

Runs the owner forgets are listed in "forgotten.json" (rewritten atomically)
by run_id, started time and root, so an old copy in that folder is never
imported again (a copied folder's records are a run of their own; an entry
written before roots were kept matches any root). A list
that can't be read is never written over: forgetting moves it aside first, as
"forgotten.json.broken-<n>".
"""

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from photosort import appdata

# Where runs were kept before, inside each sorted folder: for migration only.
RUNS_DIR = Path(".photosort") / "runs"

_FORGOTTEN = "forgotten.json"
_RELOCATING = "relocating.json"  # {old, new} pairs not yet fully re-pointed
_NOT_RUNS = (_FORGOTTEN, _RELOCATING)

# One relocation (or finish_relocations) at a time, by any thread.
_relocating = threading.Lock()

_log = logging.getLogger("photosort")


def _runs_dir() -> Path:
    return appdata.runs_dir()


def _abs(root: Path) -> str:
    return os.path.abspath(root)


def norm_root(path) -> Path:
    """The one form a folder's path takes when it enters the store (a new
    run, a moved folder picked, an old record brought in): absolute, with
    symlinks resolved (Path.resolve, strict=False), as Home resolves the
    folder picked. A path that can't be resolved (a symlink loop) is kept
    absolute."""
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(path))


def root_key(root) -> tuple[str, ...]:
    """root as roots are compared (as undo.related_roots does): absolute,
    part by part, casefolded."""
    return tuple(part.casefold() for part in Path(os.path.abspath(root)).parts)


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def fsync_dir(directory: Path) -> None:
    """Best effort: make a rename or new file in directory durable (POSIX only)."""
    if os.name != "posix":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems (e.g. FUSE) cannot fsync a directory
    finally:
        os.close(fd)


def _write_meta(path: Path, meta) -> None:
    """Replace a JSON file atomically: temp file, fsync, os.replace."""
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(json.dumps(meta, indent=1).encode("ascii"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def _read_meta(path: Path) -> dict:
    return json.loads(path.read_bytes())


class RunJournal:
    """Writes the undo record of one run. Create it with RunJournal.create."""

    def __init__(self, root: Path, run_id: str, meta: dict, log) -> None:
        self._root = Path(root)
        self._run_id = run_id
        self._meta = meta
        self._log = log
        self._meta_path = _runs_dir() / f"{run_id}.json"
        self._next_i = 0
        self._done: set[int] = set()

    @classmethod
    def create(
        cls, root: Path, mode: str, include_subfolders: bool, planned: int
    ) -> "RunJournal":
        runs = _runs_dir()
        runs.mkdir(parents=True, exist_ok=True)
        base = f"{datetime.now():%Y%m%d-%H%M%S}-{mode}"
        run_id, n = base, 1
        while True:
            if not (runs / f"{run_id}.json").exists():
                try:
                    log = open(runs / f"{run_id}.jsonl", "xb")  # exclusive create
                    break
                except FileExistsError:
                    pass
            n += 1
            run_id = f"{base}-{n}"
        meta = {
            "run_id": run_id,
            "root": str(norm_root(root)),
            "mode": mode,
            "started": _now(),
            "finished": None,
            "status": "running",
            "include_subfolders": include_subfolders,
            "planned": planned,
            "moved": 0,
            "undone": False,
        }
        journal = cls(root, run_id, meta, log)
        _write_meta(journal._meta_path, meta)
        return journal

    @property
    def run_id(self) -> str:
        return self._run_id

    def _append(self, entry: dict) -> None:
        self._log.write(json.dumps(entry).encode("ascii") + b"\n")
        self._log.flush()
        os.fsync(self._log.fileno())

    def record_intent(self, src: str, dst: str) -> int:
        """Record a move about to happen; return its index for record_done."""
        i = self._next_i
        self._append({"op": "move", "i": i, "src": src, "dst": dst})
        self._next_i += 1
        return i

    def record_done(self, i: int) -> None:
        self._append({"op": "done", "i": i})
        self._done.add(i)

    def record_dir_created(self, rel: str) -> None:
        self._append({"op": "mkdir", "path": rel})

    def record_dir_removed(self, rel: str) -> None:
        self._append({"op": "rmdir", "path": rel})

    def finish(self, status: Literal["done", "cancelled", "failed"]) -> None:
        if not self._log.closed:
            self._log.close()
        self._meta.update(finished=_now(), status=status, moved=len(self._done))
        _write_meta(self._meta_path, self._meta)


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    mode: str
    started: datetime
    status: str
    moved: int
    undone: bool
    root: Path


@dataclass(frozen=True)
class JournalMove:
    src: str
    dst: str
    done: bool


def _metas() -> list[tuple[Path, dict]]:
    """Every readable run metadata in the central folder, as (path, meta)."""
    runs = _runs_dir()
    if not runs.is_dir():
        return []
    result = []
    for path in runs.glob("*.json"):
        if path.name in _NOT_RUNS:
            continue
        try:
            meta = _read_meta(path)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            result.append((path, meta))
    return result


def _run_info(meta: dict) -> RunInfo | None:
    try:
        started = datetime.fromisoformat(meta["started"])
        if started.tzinfo is None:
            return None
        return RunInfo(
            run_id=str(meta["run_id"]),
            mode=str(meta["mode"]),
            started=started,
            status=str(meta["status"]),
            moved=int(meta["moved"]),
            undone=bool(meta["undone"]),
            root=Path(str(meta["root"])),
        )
    except (ValueError, KeyError, TypeError):
        return None


def load_all_runs() -> list[RunInfo]:
    """Return every recorded run of every folder, newest first.
    Unreadable records are skipped."""
    result = [info for _, meta in _metas() if (info := _run_info(meta)) is not None]
    result.sort(key=lambda r: (r.started, r.run_id), reverse=True)
    return result


def load_runs(root: Path) -> list[RunInfo]:
    """Return the runs of the folder root (its path, not those of folders
    inside or around it), newest first. Unreadable records are skipped."""
    wanted = Path(_abs(root))
    return [r for r in load_all_runs() if r.root == wanted]


def _entries(run_id: str) -> list[dict]:
    """Parse the run's .jsonl. A broken last line (a crash mid-write) is dropped;
    a broken line anywhere else raises ValueError."""
    lines = (_runs_dir() / f"{run_id}.jsonl").read_bytes().splitlines()
    entries = []
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            if n == len(lines) - 1:
                break
            raise
    return entries


def load_moves(root: Path, run_id: str) -> list[JournalMove]:
    """Return the run's recorded moves in order, each marked done or not.
    (root is kept for the callers; the record is found by run_id.)"""
    entries = _entries(run_id)
    done = {e["i"] for e in entries if e["op"] == "done"}
    return [
        JournalMove(e["src"], e["dst"], e["i"] in done)
        for e in entries if e["op"] == "move"
    ]


def load_dirs(root: Path, run_id: str) -> tuple[list[str], list[str]]:
    """Return (created, removed): the folders the run made and removed, in order."""
    entries = _entries(run_id)
    created = [e["path"] for e in entries if e["op"] == "mkdir"]
    removed = [e["path"] for e in entries if e["op"] == "rmdir"]
    return created, removed


def mark_undone(root: Path, run_id: str) -> None:
    path = _runs_dir() / f"{run_id}.json"
    meta = _read_meta(path)
    meta["undone"] = True
    _write_meta(path, meta)


def forgotten_keys() -> set[tuple[str, str, str | None]]:
    """The (run_id, started, root) of every run the owner forgot; root is
    None for an entry written before roots were kept (it matches any root)."""
    try:
        entries = json.loads((_runs_dir() / _FORGOTTEN).read_bytes())
    except (OSError, ValueError):
        return set()
    if not isinstance(entries, list):
        return set()
    return {(str(e["run_id"]), str(e["started"]),
             str(e["root"]) if isinstance(e.get("root"), str) else None)
            for e in entries
            if isinstance(e, dict) and "run_id" in e and "started" in e}


def _is_forgotten(key: tuple[str, str, tuple[str, ...]],
                  forgotten: set[tuple[str, str, str | None]]) -> bool:
    """Whether key (run_id, started, root_key) is in the forgotten list; an
    entry with no root matches any root."""
    run_id, started, root = key
    return any(f[0] == run_id and f[1] == started
               and (f[2] is None or root_key(f[2]) == root) for f in forgotten)


def forget_runs(run_ids: list[str]) -> None:
    """Delete the records of these runs. Each is first added to the
    forgotten list (by its run_id, started time and root), so an old copy of
    it in that folder is never imported again; a copy in another folder is
    another run, and is."""
    runs = _runs_dir()
    path = runs / _FORGOTTEN
    entries = _forgotten_for_update(path)
    known = {(e.get("run_id"), e.get("started"), e.get("root"))
             for e in entries if isinstance(e, dict)}
    added = False
    for run_id in run_ids:
        try:
            meta = _read_meta(runs / f"{run_id}.json")
            if not isinstance(meta, dict) or "started" not in meta:
                continue
        except (OSError, ValueError):
            continue  # no readable record: nothing to remember it by
        old_id, started = _import_key(meta)[:2]  # an imported run by the id it had before
        root = meta.get("root") if isinstance(meta.get("root"), str) else None
        if (old_id, started, root) not in known:
            entry = {"run_id": old_id, "started": started}
            if root is not None:
                entry["root"] = root
            entries.append(entry)
            known.add((old_id, started, root))
            added = True
    if added:
        _write_meta(path, entries)
    for run_id in run_ids:
        (runs / f"{run_id}.json").unlink(missing_ok=True)
        (runs / f"{run_id}.jsonl").unlink(missing_ok=True)
    if run_ids and runs.is_dir():
        fsync_dir(runs)


def _forgotten_for_update(path: Path) -> list:
    """The forgotten list at path, to add to. One that can't be parsed is
    renamed to forgotten.json.broken-<n> (logged) and an empty list returned;
    one that can't be read at all raises OSError, so nothing is written over it."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    try:
        entries = json.loads(data)
    except ValueError:
        entries = None
    if isinstance(entries, list):
        return entries
    n = 1
    while os.path.lexists(aside := path.with_name(f"{path.name}.broken-{n}")):
        n += 1
    os.rename(path, aside)
    fsync_dir(path.parent)
    _log.warning("The forgotten-runs list %s could not be read; moved it to %s",
                 path, aside.name)
    return []


def relocate_root(old: Path, new: Path) -> int:
    """Re-point every run whose root is old, or a folder inside it, to the
    same place under new. Return how many runs changed.

    The pair is first written to the pending list (relocating.json); a
    failure to write that raises before any record changes. Every record is
    then tried, even after one fails; any failure is raised at the end (an
    OSError) and the pair stays pending, for finish_relocations. Calling it
    again with the same pair finishes the job too.
    """
    pair = {"old": _abs(old), "new": str(norm_root(new))}
    with _relocating:
        pending = _pending_for_update()
        if pair not in pending:
            _runs_dir().mkdir(parents=True, exist_ok=True)
            _write_meta(_runs_dir() / _RELOCATING, pending + [pair])
        changed, errors = _repoint(Path(pair["old"]), Path(pair["new"]))
        _raise_any(errors)
        _drop_pending(pair)
    return changed


def finish_relocations() -> None:
    """Finish every relocation left pending (see relocate_root), in the
    order they were started. A pair that still fails stays pending; the
    failures are raised at the end (OSError). Raises ValueError, changing
    nothing, when the pending list can't be read: no undo may go ahead then,
    as a newer run may still be recorded under an old path."""
    with _relocating:
        errors: list[OSError] = []
        for pair in _pending_for_update():
            _, failed = _repoint(Path(pair["old"]), Path(pair["new"]))
            if failed:
                errors += failed
            else:
                _drop_pending(pair)
        _raise_any(errors)


def _pending_for_update() -> list[dict]:
    """The pending pairs. ValueError if the list can't be parsed; OSError if
    it can't be read (it is never written over then)."""
    path = _runs_dir() / _RELOCATING
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    try:
        pairs = json.loads(data)
    except ValueError:
        pairs = None
    if not isinstance(pairs, list) or not all(
            isinstance(p, dict) and isinstance(p.get("old"), str)
            and isinstance(p.get("new"), str) for p in pairs):
        raise ValueError(f"The list of moved folders still being re-pointed ({path}) "
                         "can't be read")
    return [{"old": p["old"], "new": p["new"]} for p in pairs]


def _drop_pending(pair: dict) -> None:
    """Take pair off the pending list. A failure is only logged: a pair left
    pending is harmless, as re-pointing it again changes nothing."""
    path = _runs_dir() / _RELOCATING
    try:
        pending = [p for p in _pending_for_update() if p != pair]
        if pending:
            _write_meta(path, pending)
        else:
            path.unlink(missing_ok=True)
            fsync_dir(path.parent)
    except (OSError, ValueError) as e:
        _log.warning("Could not update %s: %s", path, e)


def _repoint(old_path: Path, new_path: Path) -> tuple[int, list[OSError]]:
    """Re-point the records under old_path, trying every one; return how
    many changed and the errors of those that couldn't be."""
    changed, errors = 0, []
    for path, meta in _metas():
        root = meta.get("root")
        if not isinstance(root, str):
            continue
        root_path = Path(root)
        if root_path != old_path and not root_path.is_relative_to(old_path):
            continue
        meta["root"] = str(new_path / root_path.relative_to(old_path))
        try:
            _write_meta(path, meta)
        except OSError as e:
            _log.warning("Could not re-point %s: %s", path.name, e)
            errors.append(e)
        else:
            changed += 1
    return changed, errors


def _raise_any(errors: list[OSError]) -> None:
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise OSError(f"{str(errors[0]) or type(errors[0]).__name__} "
                      f"(and {len(errors) - 1} more)")


def _import_key(meta: dict) -> tuple[str, str, tuple[str, ...] | None]:
    """A run as migration knows it: the id it had in its old folder, its
    started time and its root (root_key; None when it has none)."""
    root = meta.get("root")
    return (str(meta.get("imported_run_id", meta.get("run_id"))), str(meta.get("started")),
            root_key(root) if isinstance(root, str) else None)


def _brought_in_already(key: tuple[str, str, tuple[str, ...]]) -> bool:
    """Whether the run of key (old run_id, started, root_key) was forgotten
    or is in the central store already."""
    return (_is_forgotten(key, forgotten_keys())
            or key in {_import_key(m) for _, m in _metas()})


def _old_key(old_runs_dir: Path, run_id: str, root: Path) -> tuple[str, str, tuple[str, ...]]:
    """(run_id, started, root_key) of an old run; ValueError or OSError
    when its record can't be read."""
    meta = _read_meta(Path(old_runs_dir) / f"{run_id}.json")
    if not isinstance(meta, dict) or "started" not in meta:
        raise ValueError(f"Unreadable run record: {run_id}")
    return (run_id, str(meta["started"]), root_key(norm_root(root)))


def is_brought_in(old_runs_dir: Path, run_id: str, root: Path) -> bool:
    """Whether the old run (in <root>/.photosort/runs) is in the central
    store already or was forgotten: matched by run_id, started and root.
    Raises OSError or ValueError when its record can't be read."""
    return _brought_in_already(_old_key(old_runs_dir, run_id, root))


def import_run(old_runs_dir: Path, run_id: str, root: Path) -> str | None:
    """Copy a run from an old <root>/.photosort/runs folder into the central
    one, recording root (norm_root), and verify the copy. Return its central
    run id (run_id, or run_id-imported-N when that is taken), or None when
    the run was forgotten or is already imported: matched by run_id, started
    and root, so a folder copied with its old records (the same run_id and
    started) is imported as a run of its own.

    The old files are left in place; delete them only after this returns.
    Raises OSError or ValueError when the old run can't be read or the copy
    doesn't match; no partial copy is then left behind.
    """
    old_runs_dir = Path(old_runs_dir)
    root = norm_root(root)
    meta = _read_meta(old_runs_dir / f"{run_id}.json")
    if not isinstance(meta, dict) or "started" not in meta:
        raise ValueError(f"Unreadable run record: {run_id}")
    log = (old_runs_dir / f"{run_id}.jsonl").read_bytes()
    if _brought_in_already((run_id, str(meta["started"]), root_key(root))):
        return None

    runs = _runs_dir()
    runs.mkdir(parents=True, exist_ok=True)
    new_id, n = run_id, 0
    while True:
        if not (runs / f"{new_id}.json").exists():
            try:
                f = open(runs / f"{new_id}.jsonl", "xb")  # exclusive create
                break
            except FileExistsError:
                pass
        n += 1
        new_id = f"{run_id}-imported-{n}"
    new_meta = dict(meta, run_id=new_id, root=str(root), imported_run_id=run_id)
    meta_path = runs / f"{new_id}.json"
    try:
        with f:
            f.write(log)
            f.flush()
            os.fsync(f.fileno())
        _write_meta(meta_path, new_meta)
        if ((runs / f"{new_id}.jsonl").read_bytes() != log
                or _read_meta(meta_path) != new_meta):
            raise ValueError(f"The copy of run {run_id} does not match")
    except BaseException:
        meta_path.unlink(missing_ok=True)
        (runs / f"{new_id}.jsonl").unlink(missing_ok=True)
        raise
    return new_id
