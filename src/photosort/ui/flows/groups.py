"""Duplicates and Repeated shots: the groups found are reviewed, and the
files not kept are planned to be set aside."""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.groups import Group, plan_set_aside
from photosort.modes import MODE_SPECS, ModeSpec
from photosort.ui.flows import check_failed, plan_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS: tuple[str, ...] = ()


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.begin_progress("Checking files…")
    window.run_task(Task(spec.prepare, root, include_subfolders),
                    partial(_on_found, window), partial(check_failed, window))


def _on_found(window: "MainWindow", groups: list[Group] | None) -> None:
    if groups is None or window.cancel_pressed():
        window.show_page(window.home)
        return
    if not groups:
        window.show_summary([MODE_SPECS[window.mode].none_found], can_go=False)
        return
    window.flow_state = groups  # being reviewed
    window.review_page.load(window.root, window.mode, groups)
    window.show_page(window.review_page)


def on_reviewed(window: "MainWindow", keep: dict[int, set[str]]) -> None:
    folder = MODE_SPECS[window.mode].set_aside_folder
    try:
        plan = plan_set_aside(window.root, window.mode, folder, window.flow_state, keep)
    except OSError as e:  # e.g. the drive was unplugged during the review
        plan_failed(window, e)
        return
    window.back_to = partial(_resume, window)
    window.show_plan(plan)


def _resume(window: "MainWindow") -> None:
    """Back to the review, every answer intact."""
    window.review_page.resume()
    window.show_page(window.review_page)
