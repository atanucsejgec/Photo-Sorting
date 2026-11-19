"""After the central store, no mode writes a .photosort folder into the folder
it sorts: every mode, with fake engines where it needs one, is run from Home
through its review pages to Done and then undone, and nothing named
.photosort may appear anywhere under the folder. The run's record is in the
central store instead.
"""

import math
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from keys import (
    errors,  # noqa: F401  (fixtures, used by name)
    make_window,  # noqa: F401
    models_present,  # noqa: F401
    settings_path,  # noqa: F401
)
from PIL import Image
from PySide6.QtCore import Qt

from photosort.faces import Face
from photosort.journal import load_runs
from photosort.modes import MODE_SPECS, content, notes
from photosort.settings import DEFAULT_CONTENT_CATEGORIES
from photosort.ui.flows import files as files_flow

DIM = 512


def _unit(axis: int) -> np.ndarray:
    v = np.zeros(DIM, np.float32)
    v[axis] = 1.0
    return v


def _face(embedding: np.ndarray) -> Face:
    return Face((20.0, 20.0, 80.0, 80.0), np.zeros((5, 2), np.float32), 0.9,
                embedding.astype(np.float32), (200, 200))


class FaceEngine:
    """Faces by file name (none if not named)."""

    def __init__(self, faces: dict[str, list[Face]]) -> None:
        self.faces = faces

    def analyse(self, path: Path) -> list[Face]:
        return list(self.faces.get(Path(path).name, []))


class ClipEngine:
    """Each prompt one axis; each photo, by name, its similarity to some prompts."""

    def __init__(self, prompts: list[str], photos: dict[str, dict[str, float]]) -> None:
        self.axis = {text: n for n, text in enumerate(prompts)}
        self.photos = photos

    def image_embedding(self, path: Path) -> np.ndarray:
        v = np.zeros(DIM, np.float32)
        for text, similarity in self.photos.get(Path(path).name, {}).items():
            v[self.axis[text]] = similarity
        v[-1] = math.sqrt(1 - float(v @ v))
        return v

    def text_embeddings(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), DIM), np.float32)
        for row, text in enumerate(texts):
            out[row, self.axis[text]] = 1
        return out


def _exif_jpeg(path: Path, tags: dict, size=(16, 16)) -> None:
    exif = Image.Exif()
    for tag, value in tags.items():
        if tag == 0x8769:
            exif.get_ifd(0x8769).update(value)
        else:
            exif[tag] = value
    Image.new("RGB", size, "red").save(path, "JPEG", exif=exif)


def _taken(when: datetime) -> dict:
    return {0x8769: {36867: when.strftime("%Y:%m:%d %H:%M:%S")}}


DAY = datetime(2025, 2, 20, 10, 0)


# ----- each mode's folder and engines ------------------------------------------------------

def _date(w, root, make_jpeg, make_pattern_jpeg, store):
    for n in range(11):  # a busy day: its page is shown, left unnamed
        make_jpeg(root / f"d{n:02d}.jpg", (DAY + timedelta(minutes=n)).strftime(
            "%Y:%m:%d %H:%M:%S"))


def _all_dates(w, root, make_jpeg, make_pattern_jpeg, store):
    make_jpeg(root / "a.jpg", "2025:02:20 10:00:00")
    (root / "notes.txt").write_text("hello")


def _face_mode(w, root, make_jpeg, make_pattern_jpeg, store):
    store.add("Zoe", _unit(1), Image.new("RGB", (40, 40), "blue"))
    make_jpeg(root / "zoe.jpg", size=(200, 200))
    make_jpeg(root / "sea.jpg", size=(200, 200))
    w.engine_factory = lambda use_gpu: FaceEngine({"zoe.jpg": [_face(_unit(1))]})


def _pick_faces(w, root, make_jpeg, make_pattern_jpeg, store):
    for name in ("a1.jpg", "a2.jpg", "sea.jpg"):
        make_jpeg(root / name, size=(200, 200))
    w.engine_factory = lambda use_gpu: FaceEngine(
        {"a1.jpg": [_face(_unit(0))], "a2.jpg": [_face(_unit(0))]})


def _no_people(w, root, make_jpeg, make_pattern_jpeg, store):
    make_jpeg(root / "me.jpg")
    make_jpeg(root / "sea.jpg")
    w.engine_factory = lambda use_gpu: FaceEngine({"me.jpg": [_face(_unit(0))]})


def _duplicates(w, root, make_jpeg, make_pattern_jpeg, store):
    make_pattern_jpeg(root / "x.jpg", 1)
    shutil.copyfile(root / "x.jpg", root / "x copy.jpg")


def _repeated(w, root, make_jpeg, make_pattern_jpeg, store):
    make_pattern_jpeg(root / "a.jpg", 5, exif_dt="2025:02:20 18:13:25")
    make_pattern_jpeg(root / "b.jpg", 5, shift=2, blur=2, exif_dt="2025:02:20 18:13:26")


def _bad(w, root, make_jpeg, make_pattern_jpeg, store):
    Image.new("RGB", (64, 48), "black").save(root / "pocket.jpg", "JPEG")
    make_pattern_jpeg(root / "sharp.jpg", 2)


def _events(w, root, make_jpeg, make_pattern_jpeg, store):
    for n in range(10):
        _exif_jpeg(root / f"goa{n}.jpg", _taken(DAY + timedelta(minutes=20 * n)))


def _notes(w, root, make_jpeg, make_pattern_jpeg, store):
    make_jpeg(root / "receipt.jpg", size=(64, 48))
    w.clip_engine_factory = lambda use_gpu: ClipEngine(
        notes.PAPER_PROMPTS + notes.OTHER_PROMPTS, {"receipt.jpg": {
            "a photo of a shop receipt": 0.3}})


def _content(w, root, make_jpeg, make_pattern_jpeg, store):
    make_jpeg(root / "food.jpg", size=(64, 48))
    prompts = [text for parts in content.prompt_sets(list(DEFAULT_CONTENT_CATEGORIES))
               for text in parts]
    w.clip_engine_factory = lambda use_gpu: ClipEngine(
        prompts + content.background_prompts(), {"food.jpg": {"a photo of food": 0.3}})


def _type(w, root, make_jpeg, make_pattern_jpeg, store):
    make_jpeg(root / "IMG-20250315-WA0036.jpg")


def _location(w, root, make_jpeg, make_pattern_jpeg, store):
    _exif_jpeg(root / "a.jpg", {0x8825: {1: "N", 2: (22.0, 34.0, 21.4), 3: "E",
                                         4: (88.0, 21.0, 50.0)}})  # Kolkata


def _camera(w, root, make_jpeg, make_pattern_jpeg, store):
    _exif_jpeg(root / "a.jpg", {271: "OPPO", 272: "CPH2581"})


def _extension(w, root, make_jpeg, make_pattern_jpeg, store):
    (root / "notes.txt").write_text("hello")
    make_jpeg(root / "a.jpg")


SETUPS = {
    "date": _date, "face": _face_mode, "pick_faces": _pick_faces,
    "duplicates": _duplicates, "repeated": _repeated, "notes": _notes, "type": _type,
    "content": _content, "bad": _bad, "events": _events, "location": _location,
    "camera": _camera, "no_people": _no_people, "extension": _extension,
    "all_dates": _all_dates,
}


# The review page each mode's folder above leads through, by attribute name.
REVIEWS = {"date": "busy_days_page", "face": "face_setup_page",
           "pick_faces": "pick_faces_page", "duplicates": "review_page",
           "repeated": "review_page", "bad": "bad_page", "events": "events_page"}


def test_every_mode_is_covered():
    assert set(SETUPS) == set(MODE_SPECS)


# ----- driving a mode to Done --------------------------------------------------------------

def _settled(w) -> bool:
    return not w.busy and w.current_page() is not w.progress_page


def _step(qtbot, w) -> None:
    """Answer the review page shown the plain way, on towards the summary."""
    page = w.current_page()
    if page is w.review_page:
        page.confirm_button.click()
    elif page in (w.bad_page, w.events_page, w.busy_days_page, w.category_review_page,
                  w.which_folder_page):
        page.continue_button.click()
    elif page is w.face_setup_page:
        people = page.people_list
        for n in range(people.count()):
            people.item(n).setCheckState(Qt.CheckState.Checked)
        page.start_button.click()
    elif page is w.face_question_page:
        page.yes_button.click()
    elif page is w.pick_faces_page:
        page.tiles[0].clicked.emit()
        page.name_edit.setText("Friend")
        page.add_button.click()
        page.continue_button.click()
    else:
        pytest.fail(f"no way on from {type(page).__name__}")


def _photosort_dirs(root: Path) -> list[str]:
    return [os.path.join(top, name) for top, dirs, files in os.walk(root)
            for name in dirs + files if name.casefold() == ".photosort"]


@pytest.mark.parametrize("mode", list(SETUPS))
def test_no_photosort_folder_after_any_mode(qtbot, make_window, tmp_path, make_jpeg,
                                            make_pattern_jpeg, errors, monkeypatch, mode):
    monkeypatch.setattr(files_flow.dialogs, "choose", lambda *a, **k: 0)
    root = tmp_path / "photos"
    root.mkdir()
    w = make_window()
    SETUPS[mode](w, root, make_jpeg, make_pattern_jpeg, w.people_store)
    w.subfolders_check.setChecked(False)
    w.folder_edit.setText(str(root))
    shown = []
    w.stack.currentChanged.connect(lambda _: shown.append(w.current_page()))
    w.mode_buttons[mode].click()
    for _ in range(10):
        qtbot.waitUntil(lambda: _settled(w), timeout=15000)
        if w.current_page() is w.summary_page:
            break
        _step(qtbot, w)
    assert w.current_page() is w.summary_page, mode
    assert w.go_button.isEnabled(), w.summary_label.text()
    if mode in REVIEWS:
        assert getattr(w, REVIEWS[mode]) in shown

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page and not w.busy, timeout=15000)
    assert "Moved " in w.done_label.text() and "Moved 0 " not in w.done_label.text()
    assert [run.mode for run in load_runs(root)] == [mode]  # in the central store
    assert _photosort_dirs(root) == []

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text(), timeout=15000)
    assert _photosort_dirs(root) == []
    assert _photosort_dirs(tmp_path) == []
    assert errors == []
