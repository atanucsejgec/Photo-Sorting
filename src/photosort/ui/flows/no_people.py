"""No people: once the face models are there, look for faces in the photos
and plan the moves of those without any into a No people folder.

There is nothing to ask: the summary comes straight after the scan. The
face engine is made on the worker thread, as for Face mode, and the
folder's analysis cache is used, so a folder Face mode has looked at is
quick.
"""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.cache import AnalysisCache
from photosort.faces import FaceEngine
from photosort.modes import ModeSpec
from photosort.modes.no_people import find_no_people
from photosort.plan import Plan
from photosort.ui.flows import check_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS = ("face_detection", "face_recognition")


def _scan(root: Path, include_subfolders: bool,
          engine_factory: Callable[[bool], FaceEngine], use_gpu: bool,
          progress=None, cancel=None) -> Plan | None:
    """find_no_people with a face engine made here, on the worker thread,
    and root's analysis cache."""
    engine = engine_factory(use_gpu)
    with AnalysisCache(root) as cache:
        return find_no_people(root, include_subfolders, engine.analyse,
                              progress=progress, cancel=cancel, cache=cache)


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.begin_progress("Looking for faces…", joiner="of")
    window.run_task(
        Task(_scan, root, include_subfolders, window.engine_factory, window.settings.use_gpu),
        window.show_plan, partial(check_failed, window))
