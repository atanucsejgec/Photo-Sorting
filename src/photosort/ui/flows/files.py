"""Modes that sort every file, not only photos and videos: Extension and
Date (all files).

With subfolders included, the owner is first warned that files inside
folders may belong to programs or projects; Cancel (the default) goes Home
with nothing scanned. Date (all files) then asks which date to sort by,
Modified or Created, with the last choice focused (saved in Settings); Esc
goes Home with nothing scanned. The moves are then planned straight away,
as in the plain flow.
"""

from dataclasses import replace
from functools import partial
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

DATE_TITLE = "Which date?"
DATE_TEXT = ("Sort files by the date they were last modified, or the date they were "
             "created? Photos and videos use the date they were taken.")
DATE_SOURCES = ("modified", "created")  # the choices, in the order offered
DATE_CHOICES = ("Modified", "Created")


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    if spec.warn_subfolders and include_subfolders and not dialogs.ask(
            window, SUBFOLDERS_TITLE, SUBFOLDERS_TEXT, yes="Continue", no="Cancel",
            default="no"):
        window.show_page(window.home)
        return
    if spec.asks_date_source:
        last = 0 if window.settings.file_date == "modified" else 1
        picked = dialogs.choose(window, DATE_TITLE, DATE_TEXT, DATE_CHOICES, default=last)
        if picked is None:
            window.show_page(window.home)
            return
        window.settings.file_date = DATE_SOURCES[picked]
        window.save_settings()
        spec = replace(spec, prepare=partial(spec.prepare, date_source=DATE_SOURCES[picked]))
    plain.start(window, root, include_subfolders, spec)
