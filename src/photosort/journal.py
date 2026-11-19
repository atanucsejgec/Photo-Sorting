"""The undo record of a run, kept in the app's central runs folder
(appdata.runs_dir()), one folder for every sorted folder.

Each run has two files:

- "<run_id>.jsonl": append-only, one JSON object per line, flushed and
  fsynced after every line. A move is recorded ("move") before it happens and
  marked ("done") after, so a crashed or cancelled run can still be undone.
- "<run_id>.json": the run's metadata, rewritten atomically.

The metadata's "root" is the sorted folder's absolute path (os.path.abspath,
symlinks not resolved); a run belongs to the folder whose path equals it.
Move paths are POSIX strings relative to root, so a moved folder only needs
its root re-pointed (relocate_root). JSON is written ASCII-escaped, so Linux
names that are not valid UTF-8 (lone surrogates in Python) round-trip
unchanged.

Runs the owner forgets are listed in "forgotten.json" (rewritten atomically)
by run_id and started time, so an old copy is never imported again.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from photosort import appdata

# Where runs were kept before, inside each sorted folder: for migration only.
RUNS_DIR = Path(".photosort") / "runs"

_FORGOTTEN = "forgotten.json"


def _runs_dir() -> Path:
    return appdata.runs_dir()


def _abs(root: Path) -> str:
    return os.path.abspath(root)


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
            "root": _abs(root),
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
        if path.name == _FORGOTTEN:
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


def forgotten_keys() -> set[tuple[str, str]]:
    """The (run_id, started) of every run the owner forgot."""
    try:
        entries = json.loads((_runs_dir() / _FORGOTTEN).read_bytes())
    except (OSError, ValueError):
        return set()
    return {(str(e["run_id"]), str(e["started"])) for e in entries
            if isinstance(e, dict) and "run_id" in e and "started" in e}


def forget_runs(run_ids: list[str]) -> None:
    """Delete the records of these runs. Each is first added to the
    forgotten list, so an old copy of it is never imported again."""
    runs = _runs_dir()
    path = runs / _FORGOTTEN
    try:
        entries = json.loads(path.read_bytes())
        if not isinstance(entries, list):
            entries = []
    except (OSError, ValueError):
        entries = []
    known = {(e.get("run_id"), e.get("started")) for e in entries if isinstance(e, dict)}
    added = False
    for run_id in run_ids:
        try:
            meta = _read_meta(runs / f"{run_id}.json")
            if not isinstance(meta, dict) or "started" not in meta:
                continue
        except (OSError, ValueError):
            continue  # no readable record: nothing to remember it by
        key = _import_key(meta)  # an imported run by the id it had before
        if key not in known:
            entries.append({"run_id": key[0], "started": key[1]})
            known.add(key)
            added = True
    if added:
        _write_meta(path, entries)
    for run_id in run_ids:
        (runs / f"{run_id}.json").unlink(missing_ok=True)
        (runs / f"{run_id}.jsonl").unlink(missing_ok=True)
    if run_ids and runs.is_dir():
        fsync_dir(runs)


def relocate_root(old: Path, new: Path) -> int:
    """Re-point every run whose root is old, or a folder inside it, to the
    same place under new. Return how many runs changed."""
    old_path, new_path = Path(_abs(old)), Path(_abs(new))
    changed = 0
    for path, meta in _metas():
        root = meta.get("root")
        if not isinstance(root, str):
            continue
        root_path = Path(root)
        if root_path != old_path and not root_path.is_relative_to(old_path):
            continue
        meta["root"] = str(new_path / root_path.relative_to(old_path))
        _write_meta(path, meta)
        changed += 1
    return changed


def _import_key(meta: dict) -> tuple[str, str]:
    return (str(meta.get("imported_run_id", meta.get("run_id"))), str(meta.get("started")))


def import_run(old_runs_dir: Path, run_id: str, root: Path) -> str | None:
    """Copy a run from an old <root>/.photosort/runs folder into the central
    one, recording root, and verify the copy. Return its central run id
    (run_id, or run_id-imported-N when that is taken), or None when the run
    was forgotten or is already imported (matched by run_id and started).

    The old files are left in place; delete them only after this returns.
    Raises OSError or ValueError when the old run can't be read or the copy
    doesn't match; no partial copy is then left behind.
    """
    old_runs_dir = Path(old_runs_dir)
    meta = _read_meta(old_runs_dir / f"{run_id}.json")
    if not isinstance(meta, dict) or "started" not in meta:
        raise ValueError(f"Unreadable run record: {run_id}")
    log = (old_runs_dir / f"{run_id}.jsonl").read_bytes()
    key = (run_id, str(meta["started"]))
    if key in forgotten_keys() or key in {_import_key(m) for _, m in _metas()}:
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
    new_meta = dict(meta, run_id=new_id, root=_abs(root), imported_run_id=run_id)
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
