"""Face: make sure the face models are there, then show the remembered
people to pick from (or to add to); the photos are then searched for those
people, the owner is asked about the unsure matches, and the moves planned.

The Face setup page and the naming dialog use the window's one PeopleStore.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog

from photosort.cache import AnalysisCache
from photosort.faces import FaceEngine
from photosort.modes import ModeSpec
from photosort.modes.face_mode import Answer, FaceScan, find_face_matches, plan_faces
from photosort.people import PeopleError, Person
from photosort.ui.face_naming_dialog import FaceNamingDialog, pick_photo
from photosort.ui.flows import plan_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS = ("face_detection", "face_recognition")


def _scan_faces(root: Path, include_subfolders: bool, people: list[Person],
                engine_factory: Callable[[bool], FaceEngine], use_gpu: bool,
                progress=None, cancel=None) -> FaceScan | None:
    """find_face_matches with a face engine made here, on the worker thread,
    and root's analysis cache."""
    engine = engine_factory(use_gpu)
    with AnalysisCache(root) as cache:
        return find_face_matches(root, include_subfolders, people, engine.analyse,
                                 progress=progress, cancel=cancel, cache=cache)


@dataclass
class _FaceSort:
    """A face sort, searched: the people looked for and what was found."""

    people: list[Person]
    scan: FaceScan


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    """Open the Face setup page, everyone unticked."""
    window.face_setup_page.load(window.people_store)
    window.show_page(window.face_setup_page)


def back_to_setup(window: "MainWindow") -> None:
    """Back to the Face setup page from a sort, with the people ticked for it."""
    window.face_setup_page.load(window.people_store, window.face_setup_page.ticked_ids())
    window.show_page(window.face_setup_page)


def _name_faces(window: "MainWindow", path: Path, name: str = "") -> tuple[list[Person], bool]:
    """Let the owner name the faces in path. Return the people saved and
    whether the owner finished with Save. The people new to the store come
    back ticked; the ticks of the others stay as they were.

    The dialog has a face engine of its own, let go when it closes, so
    the models are not held in memory twice for the life of the app.
    """
    try:
        known = {p.id for p in window.people_store.people()}
    except PeopleError:
        known = set()
    engine = window.engine_factory(window.settings.use_gpu)
    dialog = FaceNamingDialog(path, engine, window.people_store, window, name=name)
    finished = dialog.exec() == QDialog.DialogCode.Accepted
    saved = dialog.added()
    dialog.deleteLater()
    ticked = [*window.face_setup_page.ticked_ids(),
              *(p.id for p in saved if p.id not in known)]
    window.face_setup_page.load(window.people_store, ticked)
    return saved, finished


def add_person(window: "MainWindow", path: Path | None = None) -> None:
    """Name the faces in path, or in a photo the owner picks."""
    if window.busy:
        return
    if path is None:
        path = pick_photo(window, "Choose a photo of the person")
    if path is None:
        return
    nobody_yet = not window.face_setup_page.has_people()
    saved, finished = _name_faces(window, path)
    # §5.2: sorting starts after naming. Not for two or more people: that
    # would be a sort of group photos only, which the owner should choose.
    if len(saved) == 1 and finished and nobody_yet:
        start_sort(window, window.face_setup_page.ticked_ids())


def add_photos(window: "MainWindow", person_id: str) -> None:
    if window.busy:
        return
    try:
        person = next((p for p in window.people_store.people() if p.id == person_id), None)
    except PeopleError as e:
        window.show_error(str(e))
        return
    if person is None:
        window.face_setup_page.load(window.people_store)
        return
    path = pick_photo(window, f"Choose a photo of {person.name}")
    if path is not None:
        _name_faces(window, path, person.name)


def start_sort(window: "MainWindow", person_ids: list[str]) -> None:
    if window.busy or window.root is None:
        return
    try:
        people = [p for p in window.people_store.people() if p.id in set(person_ids)]
    except PeopleError as e:
        window.show_error(str(e))
        return
    if not people:
        window.face_setup_page.load(window.people_store)
        return
    window.begin_sort()
    window.begin_progress("Looking for faces…", joiner="of")
    window.run_task(
        Task(_scan_faces, window.root, window.include_subfolders, people,
             window.engine_factory, window.settings.use_gpu),
        partial(_on_scan, window, people), partial(_on_scan_error, window),
    )


def _on_scan(window: "MainWindow", people: list[Person], scan: FaceScan | None) -> None:
    if scan is None or window.cancel_pressed():
        back_to_setup(window)
        return
    window.flow_state = _FaceSort(people, scan)
    if scan.questions:
        window.face_question_page.load(window.root, scan, people)
        window.show_page(window.face_question_page)
    else:
        plan_moves(window, {})


def plan_moves(window: "MainWindow", answers: dict[tuple[str, str], Answer]) -> None:
    """Plan the moves, with the owner's answers to the unsure matches."""
    sort: _FaceSort = window.flow_state
    try:
        plan = plan_faces(window.root, sort.scan, sort.people, answers)
    except OSError as e:  # e.g. the drive was unplugged
        plan_failed(window, e)
        return
    window.back_to = (partial(_resume_questions, window) if sort.scan.questions
                      else partial(back_to_setup, window))
    window.show_plan(plan)


def _resume_questions(window: "MainWindow") -> None:
    """Back to the face questions, every answer intact."""
    window.face_question_page.resume()
    window.show_page(window.face_question_page)


def _on_scan_error(window: "MainWindow", message: str) -> None:
    window.end_flow()
    window.show_page(window.home)
    window.show_error(f"Could not look for faces: {message}")
