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
MainWindow._on_executed): a new person named after the folder, or more
samples for the person of that name. Undo leaves them remembered.
"""

import logging
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from photosort.cache import AnalysisCache
from photosort.executor import ExecResult
from photosort.faces import Face, face_crop
from photosort.modes import ModeSpec
from photosort.modes.pick_faces import (
    PersonGroup, PickFolder, group_people, photo_folders, plan_pick, scan_faces,
)
from photosort.naming import fold
from photosort.people import PeopleStore
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


def _find_people(root: Path, include_subfolders: bool, engine_factory, use_gpu: bool,
                 progress=None, cancel=None) -> list[PersonGroup] | None:
    """Every face under root, grouped into people; None if cancelled. The
    face engine is made here, on the worker thread; the central analysis cache is used."""
    engine = engine_factory(use_gpu)
    with AnalysisCache() as cache:
        faces = scan_faces(root, include_subfolders, engine.analyse, cache,
                           progress=progress, cancel=cancel)
    if faces is None:
        return None
    return group_people(faces, cancel=cancel)


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.which_folder_page.clear()
    window.begin_progress("Looking for faces…", joiner="of")
    window.run_task(
        Task(_find_people, root, include_subfolders, window.engine_factory,
             window.settings.use_gpu),
        partial(_on_found, window), partial(_on_scan_error, window))


def _on_found(window: "MainWindow", groups: list[PersonGroup] | None) -> None:
    if groups is None or window.cancel_pressed():
        window.end_flow()
        window.show_page(window.home)
        return
    if not groups:
        window.show_summary([NO_FACES], can_go=False)
        return
    window.pick_faces_page.load(window.root, groups)
    window.show_page(window.pick_faces_page)


def _on_scan_error(window: "MainWindow", message: str) -> None:
    window.end_flow()
    window.show_page(window.home)
    window.show_error(f"Could not look for faces: {message}")


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


def _remember(store: PeopleStore, remember: list[_Remember], result: ExecResult) -> list[str]:
    """Remember each folder as a person (after a run that moved files); the
    Done page's lines. A problem is a line too: it never fails the run."""
    lines = []
    for item in remember:
        try:
            person = next((p for p in store.people() if fold(p.name) == fold(item.name)), None)
            if person is not None:
                store.add_samples(person.id, item.embeddings)
            elif item.picture is None:
                raise ValueError(item.problem)
            else:
                mean = item.embeddings.mean(axis=0)
                store.add(item.name, (mean / np.linalg.norm(mean)).astype(np.float32),
                          item.picture)
        except Exception as e:  # the store's or the disk's: said on Done
            _log.warning("Remembering %s failed", item.name, exc_info=True)
            lines.append(f"Couldn't remember {item.name}: {e}")
        else:
            lines.append(f"{item.name} remembered on the People page")
    return lines
