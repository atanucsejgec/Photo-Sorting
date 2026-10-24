"""The undo record of a run, kept in <root>/.photosort/runs/.

Each run has two files:

- "<run_id>.jsonl": append-only, one JSON object per line, flushed and
  fsynced after every line. A move is recorded ("move") before it happens and
  marked ("done") after, so a crashed or cancelled run can still be undone.
- "<run_id>.json": the run's metadata, rewritten atomically.

Paths are POSIX strings relative to root, so undo still works when the drive
is mounted elsewhere. JSON is written ASCII-escaped, so Linux names that are
not valid UTF-8 (lone surrogates in Python) round-trip unchanged.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

RUNS_DIR = Path(".photosort") / "runs"


def _runs_dir(root: Path) -> Path:
    return Path(root) / RUNS_DIR


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


def _write_meta(path: Path, meta: dict) -> None:
    """Replace the metadata file atomically: temp file, fsync, os.replace."""
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
        self._meta_path = _runs_dir(root) / f"{run_id}.json"
        self._next_i = 0
        self._done: set[int] = set()

    @classmethod
    def create(
        cls, root: Path, mode: str, include_subfolders: bool, planned: int
    ) -> "RunJournal":
        runs = _runs_dir(root)
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


@dataclass(frozen=True)
class JournalMove:
    src: str
    dst: str
    done: bool


def load_runs(root: Path) -> list[RunInfo]:
    """Return every recorded run, newest first. Unreadable records are skipped."""
    runs = _runs_dir(root)
    if not runs.is_dir():
        return []
    result = []
    for path in runs.glob("*.json"):
        try:
            meta = _read_meta(path)
            started = datetime.fromisoformat(meta["started"])
            if started.tzinfo is None:
                continue
            result.append(RunInfo(
                run_id=str(meta["run_id"]),
                mode=str(meta["mode"]),
                started=started,
                status=str(meta["status"]),
                moved=int(meta["moved"]),
                undone=bool(meta["undone"]),
            ))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    result.sort(key=lambda r: (r.started, r.run_id), reverse=True)
    return result


def _entries(root: Path, run_id: str) -> list[dict]:
    """Parse the run's .jsonl. A broken last line (a crash mid-write) is dropped;
    a broken line anywhere else raises ValueError."""
    lines = (_runs_dir(root) / f"{run_id}.jsonl").read_bytes().splitlines()
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
    """Return the run's recorded moves in order, each marked done or not."""
    entries = _entries(root, run_id)
    done = {e["i"] for e in entries if e["op"] == "done"}
    return [
        JournalMove(e["src"], e["dst"], e["i"] in done)
        for e in entries if e["op"] == "move"
    ]


def load_dirs(root: Path, run_id: str) -> tuple[list[str], list[str]]:
    """Return (created, removed): the folders the run made and removed, in order."""
    entries = _entries(root, run_id)
    created = [e["path"] for e in entries if e["op"] == "mkdir"]
    removed = [e["path"] for e in entries if e["op"] == "rmdir"]
    return created, removed


def mark_undone(root: Path, run_id: str) -> None:
    path = _runs_dir(root) / f"{run_id}.json"
    meta = _read_meta(path)
    meta["undone"] = True
    _write_meta(path, meta)
