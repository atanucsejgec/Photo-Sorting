"""Pick faces: once the face models are there, find every face in the
photos and group them into people; the owner picks people on the Pick faces
page and makes folders of them, and the moves are planned.

The face engine is made on the worker thread, as for Face mode, and the
folder's analysis cache is used, so a folder Face mode has looked at groups
quickly. The plan is made in a Task too: it looks at the folders in the root.
"""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.cache import AnalysisCache
from photosort.modes import ModeSpec
from photosort.modes.pick_faces import PersonGroup, PickFolder, group_people, plan_pick, scan_faces
from photosort.plan import Plan
from photosort.ui.flows.face import MODELS as FACE_MODELS
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS = FACE_MODELS
NO_FACES = "No faces found"


def _find_people(root: Path, include_subfolders: bool, engine_factory, use_gpu: bool,
                 progress=None, cancel=None) -> list[PersonGroup] | None:
    """Every face under root, grouped into people; None if cancelled. The
    face engine is made here, on the worker thread; root's analysis cache is used."""
    engine = engine_factory(use_gpu)
    with AnalysisCache(root) as cache:
        faces = scan_faces(root, include_subfolders, engine.analyse, cache,
                           progress=progress, cancel=cancel)
    if faces is None:
        return None
    return group_people(faces)


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
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


def _plan(root: Path, folders: list[PickFolder], progress=None, cancel=None) -> Plan:
    return plan_pick(root, folders, choices={})


def on_continued(window: "MainWindow", folders: list[PickFolder]) -> None:
    """The owner's folders, made on the page: plan their moves."""
    if window.busy or window.root is None:
        return
    window.run_task(Task(_plan, window.root, folders), partial(_on_planned, window),
                    partial(_on_plan_error, window))


def _on_planned(window: "MainWindow", plan: Plan) -> None:
    # Back to the page, as the owner left it.
    window.back_to = partial(window.show_page, window.pick_faces_page)
    window.show_plan(plan)


def _on_plan_error(window: "MainWindow", message: str) -> None:
    """The moves could not be planned (e.g. the drive was unplugged): home, and say so."""
    window.end_flow()
    window.pick_faces_page.clear()
    window.show_page(window.home)
    window.show_error(f"Could not plan the moves: {message}")
