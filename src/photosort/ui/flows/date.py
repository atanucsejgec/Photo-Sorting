"""Date: the files are scanned and dated; when some days are busy (at least
BUSY_DAY_MIN_PHOTOS photos, and no day folder yet), the owner may name them
on the busy days page first. The moves are then planned, named days going
to "DD-MM-YYYY <name>"; with no busy day they are planned straight away.

Back on the summary goes back to the busy days page, the names typed kept.
"""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.modes import ModeSpec
from photosort.modes.date_mode import DateScan, busy_days, plan_date
from photosort.plan import Plan
from photosort.ui.flows import check_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from datetime import date

    from photosort.ui.main_window import MainWindow

MODELS: tuple[str, ...] = ()


def _scan(prepare, root: Path, include_subfolders: bool, progress=None, cancel=None):
    """(the scan, its busy days), or None if cancelled. On a pool thread:
    looking for the busy days' folders reads the disk."""
    found = prepare(root, include_subfolders, progress=progress, cancel=cancel)
    if found is None:
        return None
    return found, busy_days(found)


def _plan(scan: DateScan, names: "dict[date, str]", progress=None, cancel=None) -> Plan:
    return plan_date(scan.root, scan.files, scan.dates, names=names)


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    window.begin_progress("Checking files…")
    window.run_task(Task(_scan, spec.prepare, root, include_subfolders),
                    partial(_on_scanned, window), partial(check_failed, window))


def _on_scanned(window: "MainWindow",
                found: "tuple[DateScan, dict[date, list[str]]] | None") -> None:
    if found is None or window.cancel_pressed():
        window.show_page(window.home)
        return
    scan, days = found
    window.flow_state = scan
    if not days:
        window.run_task(Task(_plan, scan, {}), window.show_plan,
                        partial(_plan_failed, window))
        return
    window.busy_days_page.load(scan.root, days)
    window.show_page(window.busy_days_page)


def on_named(window: "MainWindow", names: "dict[date, str]") -> None:
    """The owner named the busy days (a blank name left out): plan, then the summary."""
    scan = window.flow_state
    if not isinstance(scan, DateScan):
        return
    window.run_task(Task(_plan, scan, names), partial(_on_planned, window, scan),
                    partial(_plan_failed, window))


def _on_planned(window: "MainWindow", scan: DateScan, plan: Plan) -> None:
    if window.flow_state is not scan:
        return  # the owner left the page while it was planned
    # Back to the busy days, as the owner left them.
    window.back_to = partial(window.show_page, window.busy_days_page)
    window.show_plan(plan)


def _plan_failed(window: "MainWindow", message: str) -> None:
    """The moves could not be planned (e.g. the drive was unplugged): home, and say so."""
    window.busy_days_page.stop_thumbnails()
    window.end_flow()
    window.show_page(window.home)
    window.show_error(f"Could not plan the moves: {message}")
