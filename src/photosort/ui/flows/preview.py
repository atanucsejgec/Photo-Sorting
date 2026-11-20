"""Preview: every file of the folder, one at a time, each moved at once by
the owner on the Preview page (see ui.preview_page and modes.preview).

As in the modes that take every file (Extension, Date (all files)), with
subfolders included the owner is first asked "Include subfolders?" (Cancel,
the default, goes Home). The files are then listed in a Task, the window's,
which brings the folder's old records in first (not strictly, as before a
sort). With none to show, the summary says "No files to preview".

When the Preview ends, the Done page says "Moved N files: X to Not needed,
Y to folders", counting the moves still standing (a file put back is not
moved), with Undo for the run. If none is standing, it goes Home: the run,
if one was made, is in History and undoing it changes nothing.
"""

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from photosort.modes import ModeSpec
from photosort.modes.preview import preview_files
from photosort.ui import dialogs
from photosort.ui.flows import check_failed
from photosort.ui.flows.files import SUBFOLDERS_TEXT, SUBFOLDERS_TITLE
from photosort.ui.preview_page import LAST_FILE, PreviewEnd
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS: tuple[str, ...] = ()
NO_FILES = "No files to preview"
LISTING = "Looking for files…"


def _list(root: Path, include_subfolders: bool, slots: list[str | None],
          progress=None, cancel=None) -> list[str]:
    return preview_files(root, include_subfolders, slots)


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    if include_subfolders and not dialogs.ask(
            window, SUBFOLDERS_TITLE, SUBFOLDERS_TEXT, yes="Continue", no="Cancel",
            default="no"):
        window.show_page(window.home)
        return
    slots = list(window.settings.preview_folders)
    window.begin_progress(LISTING)
    window.run_task(Task(_list, root, include_subfolders, slots),
                    partial(_on_listed, window, root, include_subfolders, slots),
                    partial(check_failed, window))


def _on_listed(window: "MainWindow", root: Path, include_subfolders: bool,
               slots: list[str | None], files: list[str]) -> None:
    if window.cancel_pressed():
        window.end_flow()
        window.show_page(window.home)
        return
    if not files:
        window.show_summary([NO_FILES], can_go=False)
        return
    window.preview_page.load(root, include_subfolders, files, slots)
    window.show_page(window.preview_page)


def done_lines(end: PreviewEnd) -> list[str]:
    """What the Done page says of a Preview that moved files."""
    to_not_needed, to_folders = end.counts
    lines = [f"{LAST_FILE}."] if end.last else []
    lines.append(f"Moved {to_not_needed + to_folders} files: {to_not_needed} to Not needed, "
                 f"{to_folders} to folders")
    return lines + end.problems


def on_ended(window: "MainWindow", end: PreviewEnd) -> None:
    """The Preview is over: Done with Undo if files stay moved, else Home."""
    window.end_flow()
    if end.run_id is None or sum(end.counts) == 0:
        window.show_page(window.home)
        if end.problems:
            window.show_error("\n".join(end.problems))
        return
    window.show_run_done(end.run_id, done_lines(end))


def on_slots_changed(window: "MainWindow", slots: list[str | None]) -> None:
    """The owner changed the folder slots: keep them in Settings."""
    window.settings.preview_folders = list(slots)
    window.save_settings()
