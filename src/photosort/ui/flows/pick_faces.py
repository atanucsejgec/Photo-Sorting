"""Pick faces: once the face models are there, find every face in the
photos and group them into people; the owner picks people on the Pick faces
page and makes folders of them. The photos picked for two or more folders
are asked about on the Which folder? page (skipped when there are none),
then the moves are planned.

The face engine is made on the worker thread, as for Face mode, and the
folder's analysis cache is used, so a folder Face mode has looked at groups
quickly. The plan is made in a Task too: it looks at the folders in the root.

Remember on the People page: the plan's Task also gathers, for each folder
ticked so, its faces' embeddings and a picture of its clearest face (read
now, before the photo moves). The window's after_run then remembers them,
only once the run has moved files and was not cancelled (see
MainWindow._on_executed): a new person named after the folder (its mean
face first, then every face chosen as a sample), or every face chosen as
more samples for the person of that name. Undo leaves them remembered.

Names: the scan's Task also looks up which groups are someone on the People
page (modes.pick_faces.known_names; an unreadable People page means no
names), and the page shows them. A tile named on the page
(name_requested) is saved at once by save_name on a pool thread, without
leaving the page; the answer goes back to the page (set_tile_name, or
name_failed with "Couldn't save <name>: <error>"), unless the page has been
loaded since.
"""

import logging
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject

from photosort.cache import AnalysisCache
from photosort.executor import ExecResult
from photosort.faces import Face, face_crop
from photosort.modes import ModeSpec
from photosort.modes.pick_faces import (
    PersonGroup, PickFolder, group_people, known_names, photo_folders, plan_pick, scan_faces,
)
from photosort.naming import fold
from photosort.people import PeopleStore, Person, name_problem
from photosort.plan import Plan
from photosort.thresholds import PERSON_THUMB_PX
from photosort.ui.flows.face import MODELS as FACE_MODELS
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

_log = logging.getLogger("photosort")

MODELS = FACE_MODELS
NO_FACES = "No faces found"
_CROP_TRIES = 5  # faces tried, clearest first, for a remembered person's picture


GROUPING = "Grouping faces…"


def _find_people(root: Path, include_subfolders: bool, engine_factory, use_gpu: bool,
                 store: PeopleStore | None = None, stage=None, progress=None,
                 cancel=None) -> tuple[list[PersonGroup], dict[int, str]] | None:
    """Every face under root, grouped into people, and the known names of
    the groups (by their place; see _known_names); None if cancelled. The
    face engine is made here, on the worker thread; the central analysis
    cache is used. stage(GROUPING) is called as the grouping starts (it has
    no count: the progress page shows a busy bar); Stop is still looked at
    before each of its rounds."""
    engine = engine_factory(use_gpu)
    with AnalysisCache() as cache:
        faces = scan_faces(root, include_subfolders, engine.analyse, cache,
                           progress=progress, cancel=cancel)
    if faces is None:
        return None
    if stage is not None:
        stage(GROUPING)
    groups = group_people(faces, cancel=cancel)
    if groups is None:
        return None
    return groups, _known_names(store, groups)


def _known_names(store: PeopleStore | None, groups: list[PersonGroup]) -> dict[int, str]:
    """known_names against the People page; none if it can't be read (the
    page loads all the same). Slow with many samples: a worker's job."""
    if store is None or not groups:
        return {}
    try:
        people = store.people()
    except Exception:
        _log.warning("The People page could not be read for Pick faces' names",
                     exc_info=True)
        return {}
    return known_names(groups, people)


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.which_folder_page.clear()
    window.begin_progress("Looking for faces…", joiner="of")
    window.run_task(
        Task(_find_people, root, include_subfolders, window.engine_factory,
             window.settings.use_gpu, window.people_store,
             window.progress_page.stage_requested.emit),
        partial(_on_found, window), partial(_on_scan_error, window))


def _on_found(window: "MainWindow",
              found: tuple[list[PersonGroup], dict[int, str]] | None) -> None:
    if found is None or window.cancel_pressed():
        window.end_flow()
        window.show_page(window.home)
        return
    groups, names = found
    if not groups:
        window.show_summary([NO_FACES], can_go=False)
        return
    window.pick_faces_page.load(window.root, groups)
    window.pick_faces_page.set_known_names(names)
    window.show_page(window.pick_faces_page)


def _on_scan_error(window: "MainWindow", message: str) -> None:
    window.end_flow()
    window.show_page(window.home)
    window.show_error(f"Could not look for faces: {message}")


# ----- naming a tile ---------------------------------------------------------------------


def _save_name(store: PeopleStore, root: Path, name: str, faces: list[tuple[str, Face]],
               progress=None, cancel=None) -> str:
    """save_name, as a Task runs it (it can't be stopped part-way)."""
    return save_name(store, root, name, faces)


class _Saving(QObject):
    """A tile's name being saved: keeps its Task alive (as the page's child)
    and gives the answer to the page, if the page was not loaded since."""

    def __init__(self, page, tile_id: int, name: str, task: Task) -> None:
        super().__init__(page)
        self._page = page
        self._generation = page.generation
        self._tile_id = tile_id
        self._name = name
        self._task = task
        task.signals.result.connect(self._saved)
        task.signals.error.connect(self._failed)

    def _saved(self, name: str) -> None:
        if self._page.generation == self._generation:
            self._page.set_tile_name(self._tile_id, name)
        self.deleteLater()

    def _failed(self, message: str) -> None:
        if self._page.generation == self._generation:
            self._page.name_failed(self._tile_id, f"Couldn't save {self._name.strip()}: "
                                                  f"{message}")
        self.deleteLater()


def on_name_requested(window: "MainWindow", tile_id: int, name: str) -> None:
    """Save the tile's ticked faces under name on the People page, on a pool
    thread; the owner stays on the page meanwhile."""
    page = window.pick_faces_page
    found = _faces(window)
    faces = [(rel, found[(rel, i)]) for rel, i in page.ticked_faces(tile_id)
             if (rel, i) in found]
    task = Task(_save_name, window.people_store, window.root, name, faces, cancellable=False)
    _Saving(page, tile_id, name, task)
    task.start()


# ----- which folder ----------------------------------------------------------------------


def _faces(window: "MainWindow") -> dict[tuple[str, int], Face]:
    """Every face found, by (photo, its number in the photo)."""
    return {(f.rel, f.index): f.face for g in window.pick_faces_page.groups for f in g.faces}


def on_continued(window: "MainWindow", folders: list[PickFolder]) -> None:
    """The owner's folders, made on the page: ask about the photos of two
    or more of them, if any; else plan the moves."""
    if window.busy or window.root is None:
        return
    window.flow_state = folders
    conflicts = {rel: names for rel, names in photo_folders(folders).items() if len(names) > 1}
    if not conflicts:
        window.which_folder_page.clear()
        _plan_moves(window, folders, {}, window.pick_faces_page)
        return
    found = _faces(window)
    picked = {key for f in folders for key in f.faces}
    boxed: dict[str, list[Face]] = {}
    for rel, index in sorted(picked):
        if rel in conflicts and (rel, index) in found:
            boxed.setdefault(rel, []).append(found[(rel, index)])
    window.which_folder_page.load(window.root, conflicts, boxed)
    window.show_page(window.which_folder_page)


def on_chosen(window: "MainWindow", choices: dict[str, str | None]) -> None:
    """The Which folder? answers: plan the moves."""
    folders = window.flow_state
    if window.busy or window.root is None or not isinstance(folders, list):
        return
    _plan_moves(window, folders, choices, window.which_folder_page)


def back_to_people(window: "MainWindow") -> None:
    """Esc on Which folder?: the people, as the owner left them."""
    window.show_page(window.pick_faces_page)


# ----- planning --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Remember:
    """A folder to remember as a person: its faces' embeddings ((m, d), unit
    rows) and its clearest face's picture, or why there is none."""

    name: str
    embeddings: np.ndarray
    picture: Image.Image | None
    problem: str


def _area(face: Face) -> float:
    x1, y1, x2, y2 = face.box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _remember_data(root: Path, name: str, faces: list[tuple[str, Face]]) -> _Remember:
    embeddings = np.stack([np.asarray(face.embedding, np.float32).ravel() for _, face in faces])
    clearest = sorted(faces, key=lambda rf: (-rf[1].score, -_area(rf[1]), rf[0]))
    problem = "no face could be read"
    for rel, face in clearest[:_CROP_TRIES]:
        try:
            return _Remember(name, embeddings, face_crop(root / rel, face.box, PERSON_THUMB_PX),
                             "")
        except Exception as e:  # an unreadable photo: the next clearest face
            problem = str(e) or type(e).__name__
    return _Remember(name, embeddings, None, problem)


def _plan(root: Path, folders: list[PickFolder], choices: dict[str, str | None],
          remember: list[tuple[str, list[tuple[str, Face]]]],
          progress=None, cancel=None) -> tuple[Plan, list[_Remember]]:
    """The plan, and what to remember of the folders ticked so (read before
    anything moves)."""
    plan = plan_pick(root, folders, choices)
    return plan, [_remember_data(root, name, faces) for name, faces in remember if faces]


def _plan_moves(window: "MainWindow", folders: list[PickFolder],
                choices: dict[str, str | None], back_page) -> None:
    found = _faces(window) if any(f.remember for f in folders) else {}
    remember = [(f.name, [(rel, found[(rel, i)]) for rel, i in sorted(f.faces)
                          if (rel, i) in found])
                for f in folders if f.remember]
    window.run_task(Task(_plan, window.root, folders, choices, remember),
                    partial(_on_planned, window, back_page), partial(_on_plan_error, window))


def _on_planned(window: "MainWindow", back_page,
                result: tuple[Plan, list[_Remember]]) -> None:
    plan, remember = result
    # Back to the page before, as the owner left it; what was to be remembered is let go.
    window.back_to = partial(_back, window, back_page)
    window.after_run = partial(_remember, window.people_store, remember) if remember else None
    window.show_plan(plan)


def _back(window: "MainWindow", page) -> None:
    window.after_run = None
    window.show_page(page)


def _on_plan_error(window: "MainWindow", message: str) -> None:
    """The moves could not be planned (e.g. the drive was unplugged): home, and say so."""
    window.end_flow()
    window.pick_faces_page.clear()
    window.which_folder_page.clear()
    window.show_page(window.home)
    window.show_error(f"Could not plan the moves: {message}")


# ----- remember --------------------------------------------------------------------------


def _save_person(store: PeopleStore, item: _Remember) -> Person:
    """Save item's faces as the person of its name (compared folded, as the
    People page does): more samples for that person if there is one, else a
    new person (the normalised mean face first, then every face chosen).
    Raises the store's or the disk's error, or ValueError if a new person
    has no picture."""
    with store.lock():  # the look-up and the write as one: saves may run at once
        person = next((p for p in store.people() if fold(p.name) == fold(item.name)), None)
        if person is not None:
            store.add_samples(person.id, item.embeddings)
            return person
        if item.picture is None:
            raise ValueError(item.problem)
        mean = item.embeddings.mean(axis=0)
        person = store.add(item.name, (mean / np.linalg.norm(mean)).astype(np.float32),
                           item.picture)
        store.add_samples(person.id, item.embeddings)  # every face chosen
        return person


def save_name(store: PeopleStore, root: Path, name: str, faces: list[tuple[str, Face]]) -> str:
    """Name a Pick faces tile: save its faces ((rel, Face), the ticked ones)
    on the People page under name, as Remember does (_save_person). Returns
    the name saved: the person's name as the People page has it.

    The name is cleaned and checked first (people.name_problem); a refused
    name raises ValueError with the reason, and the store is not touched.
    The store's or the disk's errors are raised too. It reads a photo for
    the picture, so it runs on a worker thread.
    """
    name, problem = name_problem(name)
    if problem is not None:
        raise ValueError(problem)
    if not faces:
        raise ValueError("No face is ticked.")
    return _save_person(store, _remember_data(root, name, faces)).name


def _remember(store: PeopleStore, remember: list[_Remember], result: ExecResult) -> list[str]:
    """Remember each folder as a person (after a run that moved files); the
    Done page's lines. A problem is a line too: it never fails the run."""
    lines = []
    for item in remember:
        try:
            _save_person(store, item)
        except Exception as e:  # the store's or the disk's: said on Done
            _log.warning("Remembering %s failed", item.name, exc_info=True)
            lines.append(f"Couldn't remember {item.name}: {e}")
        else:
            lines.append(f"{item.name} remembered on the People page")
    return lines
