"""The people the owner has named, remembered by their faces' embeddings.

They live in user_data_dir/people/, never inside a sorted folder:
people.json lists each person's id, name and thumbnail file, and each person
has <id>.npy (their sample embeddings, one row each) and <id>.jpg (a face
picture). Every file is written atomically, people.json last, so a crash
never leaves a half-written file and people.json only names complete people.

Names are cleaned with naming.safe_name (they become folder names) and are
unique case-insensitively.
"""

import io
import json
import logging
import os
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


class PeopleError(Exception):
    """A change to the remembered people that can't be made; str() is for the owner."""


@dataclass(frozen=True, eq=False)
class Person:
    id: str
    name: str
    embeddings: np.ndarray  # (k, 512) float32, unit rows
    thumb: Path


def default_folder() -> Path:
    return Path(platformdirs.user_data_dir("photo-sorter")) / "people"


class PeopleStore:
    """The remembered people in folder (default: user_data_dir/people/).

    Reads come from disk, and are kept until people.json changes, so another
    store on the same folder sees this one's changes.
    """

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = default_folder() if folder is None else Path(folder)
        self._loaded: tuple[tuple[int, int, int], list[Person]] | None = None

    def people(self) -> list[Person]:
        """Everyone remembered, sorted by name (case-insensitively)."""
        return sorted(self._load(), key=lambda p: (p.name.casefold(), p.name, p.id))

    def add(self, name: str, embedding: np.ndarray, thumb: Image.Image) -> Person:
        """Remember a new person with one sample embedding and a face picture."""
        if not name.replace(".", "").strip():
            raise PeopleError("Type a name.")
        name = safe_name(name.strip())
        everyone = self._load()
        if any(p.name.casefold() == name.casefold() for p in everyone):
            raise PeopleError(f"A person called {name} already exists.")
        person_id = uuid.uuid4().hex
        embeddings = _as_rows(embedding)
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(self._npy(person_id), _npy_bytes(embeddings))
        _write_atomic(self._jpg(person_id), _thumb_bytes(thumb))
        person = Person(person_id, name, embeddings, self._jpg(person_id))
        self._save([*everyone, person])
        return person

    def add_samples(self, person_id: str, embeddings: np.ndarray) -> None:
        """Add sample embeddings ((m, 512), or one (512,)) to a remembered person."""
        everyone = self._load()
        person = self._find(everyone, person_id)
        rows = np.concatenate([person.embeddings, _as_rows(embeddings)])
        _write_atomic(self._npy(person_id), _npy_bytes(rows))
        updated = Person(person.id, person.name, rows, person.thumb)
        self._save([updated if p.id == person_id else p for p in everyone])

    def delete(self, person_id: str) -> None:
        """Forget a person: take them off the list, then remove their files."""
        everyone = self._load()
        person = self._find(everyone, person_id)
        self._save([p for p in everyone if p.id != person_id])
        for path in (self._npy(person_id), person.thumb):
            path.unlink(missing_ok=True)

    def best_match(self, embedding: np.ndarray,
                   person_ids: list[str]) -> tuple[str, float] | None:
        """(id, score) of the person in person_ids most like embedding, or None.

        A person's score is their most similar sample's similarity. Ids of
        people not remembered are ignored; ties go to the earlier id.
        """
        by_id = {p.id: p for p in self._load()}
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

    def _find(self, everyone: list[Person], person_id: str) -> Person:
        for p in everyone:
            if p.id == person_id:
                return p
        raise PeopleError("That person is no longer remembered.")

    def _load(self) -> list[Person]:
        """Everyone on the list whose samples can be read; raises PeopleError
        if people.json exists but can't be read (so it is never overwritten)."""
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
            entries = json.loads(path.read_bytes())["people"]
            listed = [(str(e["id"]), str(e["name"]), str(e["thumb"])) for e in entries]
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise PeopleError("The remembered people can't be read.") from e
        everyone = []
        for person_id, name, thumb in listed:
            try:
                embeddings = np.load(self._npy(person_id), allow_pickle=False)
            except (OSError, ValueError) as e:
                _log.warning("Remembered person %r skipped: %s", name, e)
                continue
            everyone.append(Person(person_id, name, embeddings.astype(np.float32),
                                   self.folder / Path(thumb).name))
        self._loaded = (stamp, everyone)
        return list(everyone)

    def _save(self, everyone: list[Person]) -> None:
        listing = {"people": [
            {"id": p.id, "name": p.name, "thumb": p.thumb.name, "samples": len(p.embeddings)}
            for p in everyone
        ]}
        self.folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.folder / LIST_NAME,
                      json.dumps(listing, indent=1, ensure_ascii=False).encode("utf-8"))
        self._loaded = None  # read back (and stamped) on next use


def _as_rows(embeddings: np.ndarray) -> np.ndarray:
    rows = np.atleast_2d(np.asarray(embeddings, np.float32))
    if rows.ndim != 2 or rows.shape[1] != 512:
        raise ValueError(f"embeddings must be (512,) or (m, 512), not {rows.shape}")
    return rows


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
