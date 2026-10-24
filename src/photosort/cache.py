"""Results of slow photo analysis, remembered between runs in <root>/.photosort/cache.db.

An entry is found by its kind (what was worked out, and in which format,
e.g. "photo-v1" or "faces-v1"), the file's casefolded name, its byte size
and its modified time. Moving a file within the folder (say, by a Date sort)
keeps its entries; changing its content loses them.

The cache never stops a run. A database found corrupt (on opening, or later)
is renamed to cache.db.broken-<n>; on opening, a fresh one is then started.
Any other trouble (a read-only folder, a database locked by another program)
turns the cache off for the run: get() finds nothing and put() stores
nothing. Each is logged once, as a warning.

Data is bytes the caller packs (with struct or numpy), never pickles.
"""

import logging
import os
import sqlite3
from pathlib import Path

from photosort.executor import CACHE_NAME, PHOTOSORT_DIR

_log = logging.getLogger("photosort")

_COMMIT_EVERY = 200  # puts between commits, so a crash loses little work
_SCHEMA = """CREATE TABLE IF NOT EXISTS entries (
    kind TEXT, name TEXT, size INT, mtime_ns INT, data BLOB,
    PRIMARY KEY (kind, name, size, mtime_ns))"""


class AnalysisCache:
    """The analysis cache of the folder root, as a context manager.

    Open it (enter it) on the thread that uses it, and use it from that one
    thread only: the SQLite connection is tied to it. Changes are committed
    every _COMMIT_EVERY puts and on exit.
    """

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / PHOTOSORT_DIR / CACHE_NAME
        self._conn: sqlite3.Connection | None = None
        self._puts = 0

    def __enter__(self) -> "AnalysisCache":
        self._conn = self._open()
        return self

    def __exit__(self, *exc) -> None:
        if self._conn is None:
            return
        try:
            self._conn.commit()
        except sqlite3.Error as e:
            self._give_up(e)
        finally:
            self._close()

    def get(self, kind: str, path: Path) -> bytes | None:
        """The data stored for path's current content, or None."""
        key = self._key(kind, path)
        if key is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT data FROM entries WHERE kind=? AND name=? AND size=? AND mtime_ns=?",
                key,
            ).fetchone()
        except sqlite3.Error as e:
            self._give_up(e)
            return None
        return None if row is None else bytes(row[0])

    def put(self, kind: str, path: Path, data: bytes) -> None:
        """Store data for path's current content, replacing what was there."""
        key = self._key(kind, path)
        if key is None:
            return
        try:
            self._conn.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?)",
                               (*key, data))
            self._puts += 1
            if self._puts % _COMMIT_EVERY == 0:
                self._conn.commit()
        except sqlite3.Error as e:
            self._give_up(e)

    def _key(self, kind: str, path: Path) -> tuple[str, str, int, int] | None:
        """(kind, name, size, mtime_ns), or None when off or path can't be stat'ed."""
        if self._conn is None:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        return kind, Path(path).name.casefold(), st.st_size, st.st_mtime_ns

    def _open(self) -> sqlite3.Connection | None:
        """A connection to a working database, or None (logged) to do nothing."""
        try:
            self.path.parent.mkdir(exist_ok=True)
        except OSError as e:
            _log.warning("Analysis cache off: can't create %s: %s", self.path.parent, e)
            return None
        try:
            return _connect(self.path)
        except sqlite3.Error as first:
            if not _is_corrupt(first):
                _log.warning("Analysis cache off for this run: can't open %s: %s",
                             self.path, first)
                return None
            try:
                broken = _set_aside(self.path)
                conn = _connect(self.path)
            except (OSError, sqlite3.Error) as e:
                _log.warning("Analysis cache off: %s is unusable (%s) and can't be "
                             "replaced: %s", self.path, first, e)
                return None
            _log.warning("Analysis cache %s was unusable (%s); moved to %s, starting afresh",
                         self.path, first, broken.name)
            return conn

    def _give_up(self, error: sqlite3.Error) -> None:
        """Stop using the cache for the rest of this run (logged once).

        A database found corrupt is also set aside, so the next run starts afresh.
        """
        self._close()
        if not _is_corrupt(error):
            _log.warning("Analysis cache off for this run: %s: %s", self.path, error)
            return
        try:
            broken = _set_aside(self.path)
        except OSError as e:
            _log.warning("Analysis cache off for this run: %s is corrupt (%s) and "
                         "can't be moved aside: %s", self.path, error, e)
            return
        _log.warning("Analysis cache off for this run: %s was corrupt (%s); moved to %s",
                     self.path, error, broken.name)

    def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _is_corrupt(error: sqlite3.Error) -> bool:
    """Whether error says the file is damaged or not a database at all.

    SQLite reports those (SQLITE_CORRUPT, SQLITE_NOTADB) as a plain
    DatabaseError; its subclasses, such as OperationalError ("database is
    locked", "unable to open database file"), are passing trouble.
    """
    return type(error) is sqlite3.DatabaseError


def _connect(path: Path) -> sqlite3.Connection:
    """Open path and make sure the table exists (a file that isn't a database
    fails here; damage further in shows up when it is read)."""
    conn = sqlite3.connect(path)  # check_same_thread: one thread only
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def _set_aside(path: Path) -> Path:
    """Rename path to <name>.broken-<n>, the smallest n free, and return the new path."""
    n = 1
    while True:
        broken = path.with_name(f"{path.name}.broken-{n}")
        if not broken.exists():
            break
        n += 1
    os.rename(path, broken)
    for suffix in ("-journal", "-wal", "-shm"):  # SQLite's side files go with it
        side = path.with_name(path.name + suffix)
        if side.exists():
            os.rename(side, broken.with_name(broken.name + suffix))
    return broken
