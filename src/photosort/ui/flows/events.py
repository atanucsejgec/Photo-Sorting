"""Events: the events found (with the gap and minimum from Settings) are
named, merged, split or skipped by the owner before they are planned."""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.modes import MODE_SPECS, ModeSpec
from photosort.modes.events import Event, plan_events
from photosort.ui.flows import check_failed, plan_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS: tuple[str, ...] = ()


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.begin_progress("Dating files…", joiner="of")
    prepare = partial(spec.prepare, gap_hours=window.settings.event_gap_hours,
                      min_files=window.settings.event_min_files)
    window.run_task(Task(prepare, root, include_subfolders),
                    partial(_on_found, window), partial(check_failed, window))


def _on_found(window: "MainWindow", found: tuple[list[Event], int] | None) -> None:
    if found is None or window.cancel_pressed():
        window.show_page(window.home)
        return
    events, left_out = found
    window.flow_state = left_out  # files in no event
    if not events:
        window.show_summary([MODE_SPECS["events"].none_found], can_go=False)
        return
    window.events_page.load(window.root, events, left_out)
    window.show_page(window.events_page)


def on_named(window: "MainWindow", events: list[Event], names: list[str],
             skipped: set[int]) -> None:
    try:
        plan = plan_events(window.root, events, names, skipped, window.flow_state)
    except OSError as e:  # e.g. the drive was unplugged while naming
        plan_failed(window, e)
        return
    # Back to the events, as the owner left the page.
    window.back_to = partial(window.show_page, window.events_page)
    window.show_plan(plan)
