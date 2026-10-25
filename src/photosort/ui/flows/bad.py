"""Bad photos: the bad photos found are ticked by the owner, and the ticked
ones are planned to be set aside."""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.groups import Candidate
from photosort.modes import MODE_SPECS, ModeSpec
from photosort.modes.bad_photos import plan_bad
from photosort.ui.flows import check_failed, plan_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS: tuple[str, ...] = ()


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.begin_progress("Checking photos…", joiner="of")
    window.run_task(Task(spec.prepare, root, include_subfolders),
                    partial(_on_found, window), partial(check_failed, window))


def _on_found(window: "MainWindow", bad: list[Candidate] | None) -> None:
    if bad is None or window.cancel_pressed():
        window.show_page(window.home)
        return
    if not bad:
        window.show_summary([MODE_SPECS["bad"].none_found], can_go=False)
        return
    window.flow_state = bad  # the bad photos shown
    window.bad_page.load(window.root, bad)
    window.show_page(window.bad_page)


def on_ticked(window: "MainWindow", move: set[str]) -> None:
    try:
        plan = plan_bad(window.root, window.flow_state, move)
    except OSError as e:  # e.g. the drive was unplugged while ticking
        plan_failed(window, e)
        return
    window.back_to = partial(_back, window, move)
    window.show_plan(plan)


def _back(window: "MainWindow", move: set[str]) -> None:
    """Back to the bad photos, those in move ticked."""
    window.bad_page.load(window.root, window.flow_state, move)
    window.show_page(window.bad_page)
