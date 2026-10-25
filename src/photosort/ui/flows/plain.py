"""Modes whose moves are planned straight away: Date, Type, Camera, Location."""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.modes import ModeSpec
from photosort.ui.flows import check_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS: tuple[str, ...] = ()


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.begin_progress("Checking files…")
    window.run_task(Task(spec.prepare, root, include_subfolders),
                    window.show_plan, partial(check_failed, window))
