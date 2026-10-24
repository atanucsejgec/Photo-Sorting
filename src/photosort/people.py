"""The people the owner has named, remembered by their faces' embeddings.

They live in user_data_dir/people/, never inside a sorted folder:
people.json lists each person's id, name and thumbnail file, and each person
has <id>.npy (their sample embeddings, one row each) and <id>.jpg (a face
picture). Every file is written atomically, people.json last, so a crash
never leaves a half-written file and people.json only names complete people.

Names are cleaned with naming.safe_name (they become folder names) and are
unique case-insensitively.

An entry of people.json that can't be used (its samples can't be read, its
name isn't a clean one or repeats an earlier one, its id is not ours) is
neither offered nor dropped: it is written back unchanged on every save, and
its name stays taken, so a passing fault (say, a file another program holds)
never makes the app forget anyone.
"""

import io
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import platformdirs
from PIL import Image

from photosort.faces import similarity
from photosort.naming import safe_name
from photosort.thresholds import PERSON_THUMB_PX

_log = logging.getLogger("photosort")

LIST_NAME = "people.json"
_ID = re.compile(r"[0-9a-f]{8,64}")  # ids are used in file names: nothing else is


class PeopleError(Exception):
    """A change to the remembered people that can't be made; str() is for the owner."""


@dataclass(frozen=True, eq=False)
class Person:
    id: str
    name: str
    embeddings: np.ndarray  # (k, 512) float32, unit rows
    thumb: Path


# One of people.json's entries, in order: a Person, or an entry it can't use,
# kept as it was read.
_Entry = object


def default_folder() -> Path:
    return Path(platformdirs.user_data_dir("photo-sorter")) / "people"


class PeopleStore:
    """The remembered people in folder (default: user_data_dir/people/).

    Reads come from disk, and are kept until people.json changes, so another
    store on the same folder sees this one's changes.
    """

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = default_folder() if folder is None else Path(folder)
        self._loaded: tuple[tuple[int, int, int], list[_Entry]] | None = None

    def people(self) -> list[Person]:
        """Everyone remembered, sorted by name (case-insensitively)."""
        return sorted(_people(self._load()), key=lambda p: (p.name.casefold(), p.name, p.id))

    def add(self, name: str, embedding: np.ndarray, thumb: Image.Image) -> Person:
        """Remember a new person with one sample embedding and a face picture."""
        if not name.replace(".", "").strip():
            raise PeopleError("Type a name.")
        name = safe_name(name.strip())
        entries = self._load()
        if name.casefold() in {n.casefold() for n in map(_name_of, entries) if n}:
            raise PeopleError(f"A person called {name} already exists.")
        embeddings = _as_rows(embedding)
        person_id = uuid.uuid4().hex
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(self._npy(person_id), _npy_bytes(embeddings))
        _write_atomic(self._jpg(person_id), _thumb_bytes(thumb))
        person = Person(person_id, name, embeddings, self._jpg(person_id))
        self._save([*entries, person])
        return person

    def add_samples(self, person_id: str, embeddings: np.ndarray) -> None:
        """Add sample embeddings ((m, 512), or one (512,)) to a remembered person."""
        entries = self._load()
        person = self._find(entries, person_id)
        rows = np.concatenate([person.embeddings, _as_rows(embeddings)])
        _write_atomic(self._npy(person_id), _npy_bytes(rows))
        updated = Person(person.id, person.name, rows, person.thumb)
        self._save([updated if e is person else e for e in entries])

    def delete(self, person_id: str) -> None:
        """Forget a person: take them off the list, then remove their files."""
        entries = self._load()
        person = self._find(entries, person_id)
        self._save([e for e in entries if e is not person])
        for path in (self._npy(person.id), self._jpg(person.id)):
            path.unlink(missing_ok=True)

    def best_match(self, embedding: np.ndarray,
                   person_ids: list[str]) -> tuple[str, float] | None:
        """(id, score) of the person in person_ids most like embedding, or None.

        A person's score is their most similar sample's similarity. Ids of
        people not remembered are ignored; ties go to the earlier id.
        """
        by_id = {p.id: p for p in _people(self._load())}
        best: tuple[str, float] | None = None
        for person_id in person_ids:
            person = by_id.get(person_id)
            if person is None:
                continue
            score = max(similarity(row, embedding) for row in person.embeddings)
            if best is None or score > best[1]:
                best = (person_id, score)
        return best

    def _npy(self, person_id: str) -> Path:
        return self.folder / f"{person_id}.npy"

    def _jpg(self, person_id: str) -> Path:
        return self.folder / f"{person_id}.jpg"

    def _find(self, entries: list[_Entry], person_id: str) -> Person:
        for p in _people(entries):
            if p.id == person_id:
                return p
        raise PeopleError("That person is no longer remembered.")

    def _load(self) -> list[_Entry]:
        """people.json's entries; raises PeopleError if it exists but can't be
        read (so it is never overwritten)."""
        path = self.folder / LIST_NAME
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return []
        except OSError as e:
            raise PeopleError("The remembered people can't be read.") from e
        stamp = (st.st_ino, st.st_size, st.st_mtime_ns)  # os.replace: a new inode
        if self._loaded is not None and self._loaded[0] == stamp:
            return list(self._loaded[1])
        try:
            listed = json.loads(path.read_bytes())["people"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise PeopleError("The remembered people can't be read.") from e
        if not isinstance(listed, list):
            raise PeopleError("The remembered people can't be read.")
        entries: list[_Entry] = []
        taken: set[str] = set()  # casefolded names of earlier entries
        for raw in listed:
            person = self._person(raw, taken)
            entries.append(raw if person is None else person)
            name = _name_of(raw)
            if name:
                taken.add(name.casefold())
        self._loaded = (stamp, entries)
        return list(entries)

    def _person(self, raw: object, taken: set[str]) -> Person | None:
        """The Person an entry of people.json describes, or None (logged) if unusable."""
        if not isinstance(raw, dict):
            return _unusable(raw, "not an entry")
        person_id, name = raw.get("id"), raw.get("name")
        if not isinstance(person_id, str) or not _ID.fullmatch(person_id):
            return _unusable(raw, "bad id")
        if raw.get("thumb") != f"{person_id}.jpg":
            return _unusable(raw, "bad thumbnail file")
        if (not isinstance(name, str) or not name.replace(".", "").strip()
                or safe_name(name) != name):
            return _unusable(raw, "bad name")
        if name.casefold() in taken:
            return _unusable(raw, "name repeats an earlier one")
        try:
            embeddings = _as_rows(np.load(self._npy(person_id), allow_pickle=False))
        except (OSError, ValueError, PeopleError) as e:
            return _unusable(raw, f"samples unreadable: {e}")
        return Person(person_id, name, embeddings, self._jpg(person_id))

    def _save(self, entries: list[_Entry]) -> None:
        listing = {"people": [
            {"id": e.id, "name": e.name, "thumb": e.thumb.name, "samples": len(e.embeddings)}
            if isinstance(e, Person) else e
            for e in entries
        ]}
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.folder / LIST_NAME,
                      json.dumps(listing, indent=1, ensure_ascii=False).encode("utf-8"))
        self._loaded = None  # read back (and stamped) on next use


def _people(entries: list[_Entry]) -> list[Person]:
    return [e for e in entries if isinstance(e, Person)]


def _name_of(entry: _Entry) -> str | None:
    """The entry's name, also for an unusable entry that has one."""
    if isinstance(entry, Person):
        return entry.name
    if isinstance(entry, dict) and isinstance(entry.get("name"), str):
        return entry["name"]
    return None


def _unusable(raw: object, why: str) -> None:
    _log.warning("Remembered person %.80r kept but not used: %s", raw, why)
    return None


def _as_rows(embeddings: np.ndarray) -> np.ndarray:
    """embeddings ((512,) or (k, 512), k >= 1, floats) as (k, 512) float32 rows."""
    rows = np.asarray(embeddings)
    if rows.ndim == 1:
        rows = rows[None]
    if (rows.ndim != 2 or rows.shape[0] < 1 or rows.shape[1] != 512
            or not np.issubdtype(rows.dtype, np.floating)):
        raise PeopleError("These face samples can't be used.")
    return rows.astype(np.float32)


def _npy_bytes(rows: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, rows, allow_pickle=False)
    return buf.getvalue()


def _thumb_bytes(thumb: Image.Image) -> bytes:
    img = thumb.convert("RGB")  # a copy, so the caller's image is left alone
    img.thumbnail((PERSON_THUMB_PX, PERSON_THUMB_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _write_atomic(path: Path, data: bytes) -> None:
    """Write data to path: temp file in the same folder, fsync, os.replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
