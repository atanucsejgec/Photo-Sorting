"""Modes that sort every file, not only photos and videos: Extension.

With subfolders included, the owner is first warned that files inside
folders may belong to programs or projects; Cancel (the default) goes Home
with nothing scanned. The moves are then planned straight away, as in the
plain flow.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from photosort.modes import ModeSpec
from photosort.ui import dialogs
from photosort.ui.flows import plain

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS: tuple[str, ...] = ()

SUBFOLDERS_TITLE = "Include subfolders?"
SUBFOLDERS_TEXT = ("Files inside folders may belong to programs or projects. "
                   "Moving them can break them. Continue?")


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    if spec.warn_subfolders and include_subfolders and not dialogs.ask(
            window, SUBFOLDERS_TITLE, SUBFOLDERS_TEXT, yes="Continue", no="Cancel",
            default="no"):
        window.show_page(window.home)
        return
    plain.start(window, root, include_subfolders, spec)
