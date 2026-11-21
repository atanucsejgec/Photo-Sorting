"""The Preview page: every file of a folder, one at a time, each moved at
once with one key (see modes.preview for the session that moves them).

The top line reads "12 of 340 · IMG_0001.jpg · 2.3 MB · 14 Feb 2025" (the
size and date are read on a pool thread, a moment later), the line under
it the file's folder (the root's name, then the path inside it) and, for a
PDF, "page 3 of 12". The file is shown by the viewer of its kind
(ui.preview_viewers), made the first time a file of that kind comes up and
kept; a viewer that can't show its file hands over to the info card, with
the reason. Beside it, the folder slots: their keys and names (the path
inside the root, the folder's name outside it); a slot that is empty, or
whose folder is not there now, is greyed.

Keys: ←/→ the file before or after (a fresh → past the last file ends the
Preview; a held one's repeats stop there); N moves the file to <root>/Not needed; M opens the folder
overlay, where 1…9 and 0 move it to that slot, Enter (or "Other folder…")
picks another folder (anywhere: see modes.preview.target_problem for the
ones refused), and Esc closes it; L edits the slots
(SlotEditor); Ctrl+Z puts back the last group of files moved (a single
move is a group of one), and shows them again; E (or "Show in folder")
shows the file selected in the file manager (ui.show_folder), and the
Preview stays on it; G opens the day view (below); Esc asks whether to leave.
The viewer's own keys (Space, Shift+←/→, S,
+/−, F, ↑/↓, PgUp/PgDn, O) are offered to it first: each viewer sits in a
box that takes the focus and hands it every key press that is not one of
the page's shortcuts (a viewer never takes those), then ←/→ to the page.

Moves (and put-backs, and the finish) run one at a time, in order, on pool
threads: the next file shows at once. The viewer lets go of a file before
its move is queued. A move (or put-back) that takes over MOVING_AFTER_MS
(a copy to another drive) says "Moving <name> to <folder>…" on the
message line until it is done (an owner's later note keeps it); closing
the window says it while it waits. A move that fails puts the file back in
the list where it was, and says "Couldn't move <name>: <error>"; the
session goes on. What the queue comes back with is added under the message line's text, so
a failure is not wiped by a later note ("Nothing to put back"); the line
keeps the last MESSAGE_LINES of them.

The first media player of the process takes 0.5–2 s to make, on the GUI
thread (it loads Qt's media backend). So a session with a video or a sound
in its list makes the page's one player (preview_viewers.SharedPlayer) as
it opens, under "Getting the player ready…", right after the first file is
shown (or before, if that file is the one that plays), and both media
viewers use it for every file after: a file change never waits for it.

The day view (G) shows every file of the shown file's day in a grid
(preview_day.DayGrid), all ticked: the day is the local date of the file's
modified time, read for every file on a pool thread the first time G is
pressed ("Finding the days…") and kept (a file put back is read again);
files whose day can't be read are "Unknown day", the last day. The top line
reads "Fri 28 Feb 2025 · day 3 of 41 · 9 files · 7 ticked". Keys there: the
arrows between the cards, Space ticks, A ticks all (or none), Enter or a
double-click opens the file alone, PgUp/PgDn (or "◀ Day before" and "Day
after ▶") the day before or after, N and M move every ticked file as one
group (none ticked: "Nothing ticked"), L, E (the focused file) and Ctrl+Z as
in the one view, and G or Esc go back to one at a time, on the focused file.
The ticked files leave the grid at once; the unticked ones stay, unticked.
A file whose move fails comes back into the grid, ticked. Once the grid is
empty and the queue is idle (a failed move or a queued put-back may fill it
again), the next day opens (else the day before; with
no files left, the Preview ends as after the last file). A file opened
alone is the one view with all its keys, but G or Esc goes back to the grid
of its file's day (with the ticks it had, for the day it was opened from).
Ctrl+Z puts back the whole last group, in either view; in the grid, a group
of another day opens that day.

M remembers the file shown; a slot or Other folder… moves that file only
if it is still the one shown (a put-back may land meanwhile, while the
picker is open, say), else "The file changed — press M again". Any change
of the file shown closes the overlay, and the focus goes to the file.

The Preview ends when the owner leaves (Esc, then Leave), moves on past the
last file, or closes the window (shutdown(), which does the queued moves
and the finish there and then). Once the moves queued before it are done,
the session is finished on the queue, and ended(PreviewEnd) says how it
went.
"""

import bisect
import itertools
import logging
import math
import os
import urllib.parse
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from photosort.modes.preview import (
    NOT_NEEDED, SLOTS, PreviewSession, PutBackError, day_text, file_day, file_kind,
    inside_root, target_problem,
)
from photosort.ui import dialogs, shortcuts, show_folder
from photosort.ui import preview_viewers as pv
from photosort.ui.preview_day import DayGrid
from photosort.ui.labels import literal
from photosort.ui.shortcuts import ShortcutStrip
from photosort.ui.theme import COLORS, heading, page_spacing, scaled_font, styled
from photosort.ui.widgets import ButtonBar, ButtonKeys
from photosort.ui.worker import Task

_log = logging.getLogger("photosort")

LEAVE_TITLE = "Leave Preview?"
LEAVE_TEXT = "The files moved so far stay where they are now; Undo can put them back."
LAST_FILE = "That was the last file"
LIST_FULL = "The list is full; edit it with L"
NOTHING_TO_PUT_BACK = "Nothing to put back"
FILE_CHANGED = "The file changed — press M again"
DAY_CHANGED = "The day changed — press M again"
NOTHING_TICKED = "Nothing ticked"
FINDING_DAYS = "Finding the days…"
UNKNOWN_DAY = "Unknown day"
DAY_BEFORE_BUTTON = "◀ Day before (PgUp)"
DAY_AFTER_BUTTON = "Day after (PgDn) ▶"
FINISHING = "Finishing…"
PLAYER_READYING = "Getting the player ready…"
MESSAGE_LINES = 3  # the message line keeps the last this many notes
_PLAYS = ("video", "audio")
PICK_TITLE = "Move to which folder?"
ADD_TITLE = "Add a folder"
EDITOR_TITLE = "Preview folders"
OTHER_BUTTON = "Other folder… (Enter)"
EDIT_BUTTON = "Edit folders (L)"
SHOW_BUTTON = "Show in folder (E)"
EMPTY_SLOT = "—"
PATH_PLACEHOLDER = "Paste or type a folder path"
FOLDER_NOT_FOUND = "Folder not found"
PATH_LIST_FULL = "The list is full"
MOVING_AFTER_MS = 500  # a move taking longer says "Moving <name> to <folder>…"
SIDE_WIDTH = 220
OVERLAY_MAX_WIDTH = 720


# ----- slots ---------------------------------------------------------------------------------

def slot_key(n: int) -> str:
    """The key of slot n (0-based): 1…9, then 0 for the tenth."""
    return "0" if n == SLOTS - 1 else str(n + 1)


def slot_name(root: Path, folder: str | None) -> str:
    """How a slot is shown: its path inside root (root's own name for root),
    the folder's name if it is outside root, or "—" if it is empty."""
    if folder is None:
        return EMPTY_SLOT
    if inside_root(root, Path(folder)):
        inner = Path(folder).resolve().relative_to(Path(root).resolve()).as_posix()
        return Path(root).resolve().name if inner == "." else inner
    return Path(folder).name or folder


def _usable(root: Path, folder: str | None) -> bool:
    """Whether a slot is shown plain: it can take files now (greyed: empty,
    an app folder, or outside root and not there; one missing inside root
    is made again by the move)."""
    return folder is not None and target_problem(root, folder) is None


def moving_text(name: str, root: Path, folder: Path) -> str:
    return f"Moving {name} to {slot_name(root, str(folder))}…"


def _first_line(text: str) -> str:
    """The first line of text with something in it, stripped ("" if none)."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def local_path_of_url(url: str) -> str:
    """The path of a file: URL, its %-escapes read as the bytes they are (a
    folder name need not be UTF-8); "" if it is not a local file URL."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.casefold() != "file" or parts.netloc.casefold() not in ("", "localhost"):
        return ""
    return os.fsdecode(urllib.parse.unquote_to_bytes(parts.path))


def paste_text(text: str) -> str:
    """What a paste puts in the path box: its first line, a file: URL as
    its path. A path that is not UTF-8 can't be shown (Qt's text would
    lose its bytes): the URL stays, read at Enter (folder_from_text)."""
    text = _first_line(text)
    if text.casefold().startswith("file:"):
        path = local_path_of_url(text)
        try:
            path.encode("utf-8")
        except UnicodeEncodeError:
            return text
        return path or text
    return text


def folder_from_text(root: Path, text: str) -> Path | None:
    """The folder a pasted or typed path names: the first line, spaces and
    quotes around it dropped, a file:// URL made a path (local_path_of_url),
    ~ expanded, and a relative path taken inside root. None if nothing is
    left. Raises ValueError (FOLDER_NOT_FOUND) for a file: URL that names
    no local path (one of another host)."""
    text = _first_line(text)
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    if text.casefold().startswith("file:"):
        text = local_path_of_url(text)
        if not text:
            raise ValueError(FOLDER_NOT_FOUND)
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    return Path(os.path.normpath(path))


def _same(a: Path, b: Path) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def choose_folder(parent: QWidget | None, title: str, start: Path) -> Path | None:
    """The system's folder picker (it can make a new folder), opening at
    start; the folder picked, or None."""
    folder = QFileDialog.getExistingDirectory(parent, title, str(start))
    return Path(folder) if folder else None


# ----- work on the pool ------------------------------------------------------------------------

def _run_job(job: Callable[[], object], progress=None) -> tuple[bool, object]:
    """(True, what job returned), or (False, the exception it raised). An
    expected failure (a file gone, a folder that can't be written) is the
    owner's to see, said on the page; it is logged as a warning only."""
    try:
        return True, job()
    except Exception as e:
        _log.warning("Preview: %s", e, exc_info=True)
        return False, e


def _finish(session: PreviewSession) -> tuple[str | None, str | None]:
    """(run_id, why the finish failed or None). A finish that fails part-way
    (an empty folder that can't be removed) still ends the run as done."""
    try:
        return session.finish(), None
    except Exception as e:
        _log.warning("Preview could not finish cleanly", exc_info=True)
        return session.finish(), str(e) or type(e).__name__


def _facts(generation: int, path: Path, progress=None, cancel=None):
    """(generation, size, modified) of path, or Nones if it can't be read."""
    try:
        st = path.stat()
    except OSError:
        return generation, None, None
    return generation, st.st_size, st.st_mtime


def _error_text(error: object) -> str:
    return str(error) or type(error).__name__


DayFacts = tuple[date | None, float]  # (the day, the modified time; inf if unread)


def _day_facts(path: Path) -> DayFacts:
    """path's day (file_day) and its modified time (a broken link's own),
    to order a day's files; inf if it can't be read."""
    for stat in (os.stat, os.lstat):
        try:
            mtime = stat(path).st_mtime
            break
        except (OSError, ValueError):
            continue
    else:
        mtime = math.inf
    return file_day(path), mtime


def _read_days(root: Path, rels: list[str], progress=None, cancel=None) -> dict[str, DayFacts]:
    """The day facts of each of rels (relative to root)."""
    return {rel: _day_facts(Path(root) / rel) for rel in rels}


def _put_back_group(session: PreviewSession, root: Path
                    ) -> tuple[list[tuple[str, date | None, float]], BaseException | None]:
    """Put back the last group: (where each file is now, with its day facts,
    in put-back order; why the rest could not be put back, or None)."""
    try:
        back, error = session.put_back_group(), None
    except PutBackError as e:
        back, error = e.back, e.error
    return [(rel, *_day_facts(Path(root) / rel)) for rel in back], error


def _day_order(day: date | None) -> tuple[bool, date]:
    """The days in order, Unknown day (None) last."""
    return day is None, day or date.min


@dataclass(frozen=True)
class PreviewEnd:
    """How a Preview ended: its run (None if nothing was ever moved), the
    live moves (to Not needed, to other folders), whether it ended past the
    last file, and the problems to tell on Done."""

    run_id: str | None
    counts: tuple[int, int]
    last: bool
    problems: list[str] = field(default_factory=list)


# ----- the widgets -------------------------------------------------------------------------------

class _ViewerBox(QWidget):
    """A viewer, in a box that takes the focus for it: each key press that
    reaches the box is offered to the viewer first, then to page_key (← and
    →). Its keys are recorded on the box, so the strip lists them while it
    has the focus."""

    def __init__(self, viewer: pv.Viewer, page_key: Callable[[QKeyEvent], bool],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewer = viewer
        self._page_key = page_key
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(viewer)
        for label, word in viewer.keys():
            shortcuts.describe(self, label, word)
        shortcuts.describe(self, "←→", "file")  # the page's (page_key)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt's name)
        if self.viewer.handle_key(event) or self._page_key(event):
            event.accept()
            return
        super().keyPressEvent(event)


class FolderOverlay(QWidget):
    """M's overlay: the slots, "1 Family · 2 Trips · … · 0 —", and Other
    folder…. 1…9 and 0 pick a slot (picked(n), 0-based), Enter or the
    button asks for another folder (other), Esc closes it (closed). The
    focus leaving it (a click elsewhere, Tab) closes it too."""

    picked = Signal(int)
    other = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        title = QLabel("Move to…")
        title.setFont(scaled_font(title, 1.0, QFont.Weight.Bold))
        self.slots_label = QLabel()
        self.slots_label.setTextFormat(Qt.TextFormat.PlainText)
        self.slots_label.setWordWrap(True)
        self.other_button = QPushButton(literal(OTHER_BUTTON))
        self.other_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.other_button.clicked.connect(self.other)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.other_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(self.slots_label)
        layout.addLayout(row)
        for n in range(SLOTS):
            shortcuts.bind(self, slot_key(n), "1…9, 0", "move to folder",
                           partial(self.picked.emit, n), repeat=False)
        shortcuts.bind(self, "Return", "Enter", "other folder…", self.other.emit, repeat=False)
        shortcuts.bind(self, "Escape", "Esc", "close", self.closed.emit, repeat=False)
        self.hide()

    def text(self) -> str:
        return self.slots_label.text()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if self.isVisible() and event.reason() in (
                Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                Qt.FocusReason.BacktabFocusReason):
            self.closed.emit()


class PreviewPage(QWidget):
    """One Preview session at a time (load); ended(PreviewEnd) once it is over."""

    ended = Signal(object)  # PreviewEnd
    slots_changed = Signal(object)  # list[str | None]: save them
    keys_changed = Signal()  # keys switched on or off: the strip redraws

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path()
        self.session: PreviewSession | None = None
        self.files: list[str] = []  # the files not moved, in order
        self._order: dict[str, float] = {}  # each file's place in the first list
        self._index = 0
        self._slots: list[str | None] = [None] * SLOTS
        self._boxes: dict[str, _ViewerBox] = {}
        self._player: pv.SharedPlayer | None = None  # made once, as a session needs it
        self._box: _ViewerBox | None = None  # the box shown
        self._shown: Path | None = None  # the file shown
        self._overlay_rel: str | None = None  # the file shown when M opened the overlay
        self._shows = 0  # bumped at each file shown: a late stat for another is dropped
        self._facts: tuple[int | None, float | None] = (None, None)
        self._stat_tasks: set[Task] = set()
        self._moved: list[str] = []  # each live move's file (as it was), last last
        # (job, done, failed, status): see _enqueue
        self._queue: deque[tuple[Callable[[], object], Callable, Callable,
                                 Callable[[], str | None] | None]] = deque()
        self._running: Task | None = None
        self._running_status: Callable[[], str | None] | None = None  # what it says if slow
        self._status: str | None = None  # "Moving …", said while a slow move runs
        self._finished_task: Task | None = None  # kept alive until its thread lets go
        self._generation = 0  # bumped when a session is let go: its late results are dropped
        self._ending = False
        self._last = False
        self._problems: list[str] = []
        self._group_ids = itertools.count(1)  # each N or M press: a fresh group
        # The day view: see the module doc.
        self._view = "one"  # "one" (one at a time) or "day" (the grid)
        self._alone = False  # the one view shows a file opened from the grid
        self._days: dict[str, DayFacts] = {}  # each file's day, once read
        self._days_read = False  # G has read them (until then none are kept)
        self._day_tasks: set[Task] = set()  # the days being read: kept alive until they end
        self._returns: list[str] = []  # failed moves, back into the grid once the queue idles
        self._root_name = ""  # the grid's folder line
        self._finding: Task | None = None  # reading the days
        self._day: date | None = None  # the grid's day
        self._day_line = ""
        # (day, its files, those ticked) when a file was opened alone from the grid
        self._saved_ticks: tuple[date | None, set[str], set[str]] | None = None
        self._overlay_view = "one"  # the view M opened the overlay in
        self._overlay_rels: list[str] = []  # the files ticked then (the grid)
        self._overlay_day: date | None = None

        layout = QVBoxLayout(self)
        page_spacing(layout)
        self.title_label = heading("")
        self.folder_label = QLabel()
        self.folder_label.setObjectName("muted")
        self.message_label = QLabel()
        self.message_label.setObjectName("error")
        self.message_label.setWordWrap(True)
        self.message_label.hide()
        for label in (self.title_label, self.folder_label, self.message_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
        for label in (self.title_label, self.folder_label):
            # A long name never widens the page: it is cut (see _elide).
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setMinimumWidth(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.folder_label)
        layout.addWidget(self.message_label)

        self.view_area = QWidget()
        self.viewers = QStackedWidget(self.view_area)
        area_layout = QVBoxLayout(self.view_area)
        area_layout.setContentsMargins(0, 0, 0, 0)
        area_layout.addWidget(self.viewers)
        self.overlay = FolderOverlay(self.view_area)
        self.overlay.picked.connect(self._slot_picked)
        self.overlay.other.connect(self._other_from_overlay)
        self.overlay.closed.connect(self._close_overlay)
        self.view_area.installEventFilter(self)  # the overlay follows its size
        self.day_grid = DayGrid(sort_key=self._day_sort_key)
        self.day_grid.changed.connect(self._update_top)
        self.day_grid.open_file.connect(self._open_alone)
        self.before_button = QPushButton(literal(DAY_BEFORE_BUTTON))
        self.after_button = QPushButton(literal(DAY_AFTER_BUTTON))
        day_row = QHBoxLayout()
        day_row.setContentsMargins(0, 0, 0, 0)
        day_row.addWidget(self.before_button)
        day_row.addStretch(1)
        day_row.addWidget(self.after_button)
        for button, step in ((self.before_button, -1), (self.after_button, 1)):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(partial(self._day_step, step))
        self.day_view = QWidget()  # the grid, under the day buttons
        day_layout = QVBoxLayout(self.day_view)
        day_layout.setContentsMargins(0, 0, 0, 0)
        day_layout.setSpacing(6)
        day_layout.addLayout(day_row)
        day_layout.addWidget(self.day_grid, 1)
        self.viewers.addWidget(self.day_view)

        side = QWidget()
        side.setFixedWidth(SIDE_WIDTH)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(4)
        folders = QLabel("Folders")
        folders.setFont(scaled_font(folders, 1.0, QFont.Weight.Bold))
        side_layout.addWidget(folders)
        self.slot_labels: list[QLabel] = []
        for _ in range(SLOTS):
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setMinimumWidth(1)
            self.slot_labels.append(label)
            side_layout.addWidget(label)
        side_layout.addStretch(1)
        self.edit_button = QPushButton(literal(EDIT_BUTTON))
        self.edit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.edit_button.clicked.connect(self._edit_slots)
        side_layout.addWidget(self.edit_button)
        self.show_button = QPushButton(literal(SHOW_BUTTON))
        self.show_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.show_button.clicked.connect(self._show_in_folder)
        side_layout.addWidget(self.show_button)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self.view_area, 1)
        body.addWidget(side)
        layout.addLayout(body, 1)

        # The page's keys; the viewer's are on its box, the grid's on it. All
        # are off while the overlay is open (it has its own) and once the
        # Preview is ending; G and Esc are the view's (one set on at a time).
        # ← and → reach the page through the box (_arrow, recorded there), so
        # that a held → never runs on past the last file.
        self._keys = [
            shortcuts.bind(self, "N", "N", "not needed", self._not_needed, repeat=False),
            shortcuts.bind(self, "M", "M", "move to…", self._open_overlay, repeat=False),
            shortcuts.bind(self, "L", "L", "edit folders", self._edit_slots, repeat=False),
            shortcuts.bind(self, "E", "E", "show in folder", self._show_in_folder,
                           repeat=False),
            shortcuts.bind(self, "Ctrl+Z", "Ctrl+Z", "put back", self._put_back,
                           repeat=False),
        ]
        self._one_keys = [  # one at a time
            shortcuts.bind(self, "G", "G", "day", self._open_day, repeat=False),
            shortcuts.bind(self, "Escape", "Esc", "leave", self._leave, repeat=False),
        ]
        self._alone_keys = [  # a file opened alone from the grid
            shortcuts.bind(self, ["G", "Escape"], "G/Esc", "back to the day",
                           self._back_to_day, repeat=False),
        ]
        self._day_keys = [  # the grid
            *self.day_grid.keys,
            shortcuts.bind(self.day_grid, "PgUp", "PgUp/PgDn", "day before/after",
                           partial(self._day_step, -1), repeat=False),
            shortcuts.bind(self.day_grid, "PgDown", "PgUp/PgDn", "day before/after",
                           partial(self._day_step, 1), repeat=False),
            shortcuts.bind(self, ["G", "Escape"], "G/Esc", "one at a time",
                           self._one_at_a_time, repeat=False),
        ]
        self.set_slots(self._slots)
        self._sync_keys()

    # ----- the session ---------------------------------------------------------------------

    def load(self, root: Path, include_subfolders: bool, files: list[str],
             slots: list[str | None]) -> None:
        """Start a Preview of files (relative to root, in order; at least one)."""
        self.shutdown()  # an earlier session, if any, is finished first
        self.root = Path(root)
        self.session = PreviewSession(self.root, include_subfolders)
        self.files = list(files)
        self._order = {rel: float(n) for n, rel in enumerate(self.files)}
        self._index = 0
        self._moved = []
        self._ending = False
        self._last = False
        self._problems = []
        self._reset_day_view()
        self._say("")
        self.set_slots(slots)
        self._sync_keys()
        if self.files:
            plays = {kind for kind in map(file_kind, (self.root / rel for rel in self.files))
                     if kind in _PLAYS}
            if plays and self._player is None:
                # Made once the page shows, under the status line (see the docstring).
                self._say(PLAYER_READYING)
                QTimer.singleShot(0, partial(self._get_player_ready, self.session, plays))
                if file_kind(self.root / self.files[0]) in _PLAYS:
                    return  # shown once the player is ready
            self._show_current()
        else:
            self._end(last=True)

    def _get_player_ready(self, session: PreviewSession, plays: set[str]) -> None:
        """Make the page's player, and the viewers for the kinds that play
        (plays), on the GUI thread while the status line says so; then show
        the first file if it was waiting."""
        if not isValid(self) or session is not self.session or self._ending:
            return  # that session is over
        if self._player is None:
            if self.isVisible():
                self.repaint()  # the status line shows while the GUI thread waits
            self._player = pv.SharedPlayer(self)
            for kind in sorted(plays):
                self._box_for(kind)
        if self.message() == PLAYER_READYING:
            self._say("")
        if self._shown is None and self._box is None and self.files and self._view == "one":
            self._show_current()

    def shutdown(self) -> None:
        """The window is closing: do the moves still queued and finish the
        session now, on this thread; nothing is told (ended is not emitted)."""
        session = self.session
        if session is None:
            return
        self._release()
        self.day_grid.stop_thumbnails()
        self.overlay.hide()
        self._generation += 1  # the running job's result, when it comes, is dropped
        if self._running is not None:
            self._show_status(self._running_status)
            QThreadPool.globalInstance().waitForDone()  # it ends before those after it
        while self._queue:
            job, _done, _failed, status = self._queue.popleft()
            if status != self._putting_back_text:  # self._moved is behind now
                self._show_status(status)
            _run_job(job)
        _run_job(partial(_finish, session))  # safe if it was queued already
        self.clear()

    def _show_status(self, status: Callable[[], str | None] | None) -> None:
        """Say status() now, painted at once: the window is closing, and this
        thread waits for the move (shutdown)."""
        text = status() if status is not None else None
        if text is not None and isValid(self) and self.isVisible():
            self._say(text)
            self.repaint()

    def clear(self) -> None:
        """Let the session go (it is over) and every viewer's file."""
        self._release()
        self.overlay.hide()
        self._generation += 1
        self._queue.clear()
        self._running = None
        self._running_status = None
        self._status = None
        self.session = None
        self.files = []
        self._order = {}
        self._moved = []
        self._box = None
        self._ending = False
        self._reset_day_view()
        self.title_label.clear()
        self.folder_label.clear()
        self._say("")
        self._sync_keys()

    def _reset_day_view(self) -> None:
        """Back to one at a time, the days forgotten (a session starts or ends)."""
        self._view = "one"
        self._alone = False
        self._days = {}
        self._days_read = False
        self._returns = []
        self._finding = None
        self._day = None
        self._day_line = ""
        self._saved_ticks = None
        self.day_grid.clear()
        self._sync_day_buttons()

    def focus_first(self) -> None:
        if self.overlay.isVisible():
            self.overlay.setFocus(Qt.FocusReason.OtherFocusReason)
        elif self._view == "day":
            self.day_grid.focus()
        elif self._box is not None:
            self._box.setFocus(Qt.FocusReason.OtherFocusReason)

    def viewer(self) -> pv.Viewer | None:
        """The viewer shown."""
        return None if self._box is None else self._box.viewer

    def current(self) -> str | None:
        """The file shown, relative to root."""
        if self._shown is None or not 0 <= self._index < len(self.files):
            return None
        return self.files[self._index]

    def _active(self) -> bool:
        return self.session is not None and not self._ending

    def view(self) -> str:
        """"one" (one at a time, a file opened alone included) or "day" (the grid)."""
        return self._view

    def _sync_keys(self) -> None:
        on = self._active() and not self.overlay.isVisible()
        one, day = self._view == "one", self._view == "day"
        wanted = ([(key, on) for key in self._keys]
                  + [(key, on and one and not self._alone) for key in self._one_keys]
                  + [(key, on and one and self._alone) for key in self._alone_keys]
                  + [(key, on and day) for key in self._day_keys])
        changed = False
        # Off first, then on: two keys of one view never clash on the same key.
        for key, want in sorted(wanted, key=lambda pair: pair[1]):
            if key.isEnabled() != want:
                key.setEnabled(want)
                changed = True
        self._sync_day_buttons()
        if changed:
            self.keys_changed.emit()

    def _sync_day_buttons(self) -> None:
        on = self._view == "day" and self._active() and not self.overlay.isVisible()
        for button, step in ((self.before_button, -1), (self.after_button, 1)):
            button.setEnabled(on and bool(self._neighbour_day(step)))

    # ----- showing a file --------------------------------------------------------------------

    def _box_for(self, kind: str) -> _ViewerBox:
        kind = "other" if kind == "info" else kind
        box = self._boxes.get(kind)
        if box is None:
            if kind in _PLAYS and self._player is None:
                self._player = pv.SharedPlayer(self)  # not got ready ahead (never so far)
            makers = {"image": pv.ImageViewer, "pdf": pv.PdfViewer,
                      "video": partial(pv.MediaViewer, True, shared=self._player),
                      "audio": partial(pv.MediaViewer, False, shared=self._player),
                      "text": pv.TextViewer, "other": pv.InfoCard}
            viewer = makers[kind]()
            viewer.failed.connect(partial(self._viewer_failed, viewer))  # before any load
            if isinstance(viewer, pv.PdfViewer):
                viewer.page_changed.connect(lambda _text: self._update_top())
            box = _ViewerBox(viewer, self._arrow)
            self.viewers.addWidget(box)
            self._boxes[kind] = box
        return box

    def _show_current(self) -> None:
        path = self.root / self.files[self._index]
        self._show_in(self._box_for(file_kind(path)), path)

    def _show_in(self, box: _ViewerBox, path: Path, message: str = "") -> None:
        """Show path in box; the file shown before is let go first. The
        overlay, if open, closes: it was for the file shown before (or, for
        a viewer that failed, its box is gone), and the focus goes to box."""
        if self.overlay.isVisible():
            self.overlay.hide()
            self._sync_keys()
        if self._box is not None and self._box is not box:
            self._box.viewer.release()
        if self._shown != path:
            self._shows += 1
            self._facts = (None, None)
            self._stat(path)
        self._box, self._shown = box, path
        self.viewers.setCurrentWidget(box)
        box.viewer.load(path)  # it lets go of its file before
        if self._box is not box:  # it failed at once: the info card shows it
            return
        if message and isinstance(box.viewer, pv.InfoCard):
            box.viewer.set_message(message)
        if self.isVisible():
            box.setFocus(Qt.FocusReason.OtherFocusReason)
        self._update_top()

    def _viewer_failed(self, viewer: pv.Viewer, message: str) -> None:
        if self._box is None or self._box.viewer is not viewer or self._shown is None:
            return  # not the file shown now
        viewer.release()
        self._show_in(self._box_for("info"), self._shown, message)

    def _release(self) -> None:
        """Let go of the file shown (before it moves, or the page ends)."""
        if self._box is not None:
            self._box.viewer.release()
        self._shown = None

    def _stat(self, path: Path) -> None:
        task = Task(_facts, self._shows, path)

        def got(result, task=task):
            self._stat_tasks.discard(task)
            if isValid(self):
                generation, size, modified = result
                if generation == self._shows:
                    self._facts = (size, modified)
                    self._update_top()

        task.signals.result.connect(got)
        task.signals.error.connect(lambda _m, task=task: self._stat_tasks.discard(task))
        self._stat_tasks.add(task)
        task.start()

    def _update_top(self) -> None:
        if self._view == "day":
            if not self._ending:  # "Finishing…" stays
                self._update_day_top()
            return
        rel = self.current()
        if rel is None:
            return
        name = rel.rpartition("/")[2]
        parts = [f"{self._index + 1} of {len(self.files)}", name]
        size, modified = self._facts
        if size is not None:
            parts.append(pv.size_text(size))
        if modified is not None:
            parts.append(pv.date_text(modified))
        self._set_line(self.title_label, " · ".join(parts))
        folder = rel.rpartition("/")[0]
        root_name = self.root.resolve().name or str(self.root)
        line = f"{root_name}/{folder}" if folder else root_name
        viewer = self.viewer()
        if isinstance(viewer, pv.PdfViewer) and viewer.page_text():
            line += f" · {viewer.page_text()}"
        self._set_line(self.folder_label, line)

    def _set_line(self, label: QLabel, text: str) -> None:
        label.setToolTip(text)
        label.setProperty("full_text", text)
        self._elide(label)

    @staticmethod
    def _elide(label: QLabel) -> None:
        text = label.property("full_text") or ""
        width = max(label.width(), 50)
        label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, width))

    def _update_day_top(self) -> None:
        """The grid's top line: "Fri 28 Feb 2025 · day 3 of 41 · 9 files · 7 ticked"."""
        days = self._day_list()
        order = _day_order(self._day)
        k = 1 + sum(1 for day in days if _day_order(day) < order)
        n = len(days) + (0 if self._day in days else 1)  # an empty grid's day still counts
        files, ticked = len(self.day_grid.rels()), len(self.day_grid.ticked())
        name = UNKNOWN_DAY if self._day is None else day_text(self._day)
        self._day_line = (f"{name} · day {k} of {n} · {files} file{'' if files == 1 else 's'}"
                          f" · {ticked} ticked")
        self._set_line(self.title_label, self._day_line)
        self._set_line(self.folder_label, self._root_name)
        self._sync_day_buttons()

    def day_text(self) -> str:
        """The grid's top line, uncut ("" in the one view)."""
        return self._day_line if self._view == "day" else ""

    def text(self) -> str:
        """The top line, uncut."""
        return self.title_label.property("full_text") or ""

    def folder_text(self) -> str:
        """The folder line, uncut."""
        return self.folder_label.property("full_text") or ""

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        for label in (self.title_label, self.folder_label):
            self._elide(label)

    def _say(self, text: str) -> None:
        """A problem or a note, under the top lines, in place of what was
        there (the owner has acted since), but for a slow move's status,
        kept until the move is done; hidden while there is none."""
        if self._status is not None and self._status not in text.split("\n"):
            text = f"{text}\n{self._status}" if text else self._status
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def _tell(self, text: str) -> None:
        """A problem or a note that a move, a put-back or the queue came
        back with: added under what is there, so a failure stays in view."""
        lines = [line for line in self.message_label.text().split("\n") if line]
        if text not in lines:
            lines.append(text)
        self._say("\n".join(lines[-MESSAGE_LINES:]))

    def _untell(self, text: str) -> None:
        """Take text (a slow move's status, over now) off the message line."""
        lines = [line for line in self.message_label.text().split("\n") if line]
        if text in lines:
            lines.remove(text)
            self._say("\n".join(lines))

    def message(self) -> str:
        return self.message_label.text()

    # ----- the slots ---------------------------------------------------------------------------

    def set_slots(self, slots: list[str | None]) -> None:
        """Show slots (SLOTS entries) beside the file and in the overlay."""
        self._slots = list(slots) + [None] * (SLOTS - len(slots))
        self._slots = self._slots[:SLOTS]
        names = []
        for n, (label, folder) in enumerate(zip(self.slot_labels, self._slots)):
            name = slot_name(self.root, folder)
            names.append(f"{slot_key(n)} {name}")
            label.setText(label.fontMetrics().elidedText(
                f"{slot_key(n)}  {name}", Qt.TextElideMode.ElideMiddle, SIDE_WIDTH - 8))
            problem = None if folder is None else target_problem(self.root, folder)
            # A missing folder says why, as a move to it would (the full path).
            label.setToolTip(problem or folder or "Empty")
            look = "" if folder is not None and problem is None else "muted"
            if label.objectName() != look:
                label.setObjectName(look)
                label.style().unpolish(label)
                label.style().polish(label)
        self.overlay.slots_label.setText(" · ".join(names))

    def slots(self) -> list[str | None]:
        return list(self._slots)

    def _edit_slots(self) -> None:
        if not self._active() or self.overlay.isVisible():
            return
        editor = SlotEditor(self.root, self._slots, self)
        accepted = editor.exec() == QDialog.DialogCode.Accepted
        slots = editor.slots()
        editor.deleteLater()
        if accepted and slots != self._slots:
            self.set_slots(slots)
            self.slots_changed.emit(self.slots())

    def _add_slot(self, folder: Path) -> str:
        """Put folder in the first empty slot, unless it is listed; the note
        to show ("" or LIST_FULL)."""
        if any(f is not None and _same(Path(f), folder) for f in self._slots):
            return ""
        if None not in self._slots:
            return LIST_FULL
        slots = list(self._slots)
        slots[slots.index(None)] = str(folder)
        self.set_slots(slots)
        self.slots_changed.emit(self.slots())
        return ""

    # ----- the day view --------------------------------------------------------------------------

    def _day_sort_key(self, rel: str) -> tuple[float, float]:
        """A day's files in order: by modified time, then as first listed."""
        return (self._days.get(rel, (None, math.inf))[1],
                self._order.get(rel, float(len(self._order))))

    def _day_of(self, rel: str) -> date | None:
        return self._days.get(rel, (None, math.inf))[0]

    def _day_list(self) -> list[date | None]:
        """The days of the files listed, in order (Unknown day last)."""
        return sorted({self._day_of(rel) for rel in self.files if rel in self._days},
                      key=_day_order)

    def _rels_of(self, day: date | None) -> list[str]:
        """The listed files of day, in order."""
        return sorted((rel for rel in self.files if rel in self._days and self._day_of(rel) == day),
                      key=self._day_sort_key)

    def _neighbour_day(self, step: int) -> list[date | None]:
        """[the day before (step -1) or after (+1) the grid's], or [] if none."""
        if self._view != "day":
            return []
        order = _day_order(self._day)
        days = self._day_list()
        if step < 0:
            return [day for day in days if _day_order(day) < order][-1:]
        return [day for day in days if _day_order(day) > order][:1]

    def _open_day(self) -> None:
        """G in the one view: the grid of the shown file's day; the days are
        read first, the first time ("Finding the days…")."""
        rel = self.current()
        if not self._active() or rel is None or self._finding is not None:
            return
        missing = [other for other in self.files if other not in self._days]
        if missing:
            self._find_days(missing, rel)
            return
        self._say("")
        self._show_day(self._day_of(rel), focus=rel)

    def _find_days(self, rels: list[str], shown: str, quiet: bool = False) -> None:
        """Read the days of rels on the pool (then of any file that came back
        into the list meanwhile, a failed move); then open the grid of shown,
        the file G was pressed on, if it is still shown in the one view with
        nothing over it (a dialog, the overlay). Else nothing is said: the
        days are kept, and the next G opens at once. quiet: a follow-up read
        after which the grid won't open says nothing either."""
        if not quiet:
            self._say(FINDING_DAYS)
        task = Task(_read_days, self.root, rels)
        generation = self._generation

        def done(task: Task) -> bool:
            self._day_tasks.discard(task)
            if not isValid(self) or self._finding is not task:
                return False
            self._finding = None
            self._untell(FINDING_DAYS)
            return generation == self._generation

        def got(result: dict[str, DayFacts], task=task) -> None:
            if not done(task):
                return
            for rel, facts in result.items():
                self._days.setdefault(rel, facts)  # a put-back's is newer
            self._days_read = True
            opens = (self._active() and self._view == "one" and self.current() == shown
                     and not self.overlay.isVisible()
                     and QApplication.activeModalWidget() is None)
            missing = [other for other in self.files if other not in self._days]
            if missing:
                self._find_days(missing, shown, quiet=not opens)
            elif opens:
                self._open_day()

        def failed(message: str, task=task) -> None:
            if done(task):
                self._tell(f"Couldn't find the days: {message}")

        task.signals.result.connect(got)
        task.signals.error.connect(failed)
        self._finding = task
        self._day_tasks.add(task)
        task.start()

    def _show_day(self, day: date | None, ticked: set[str] | None = None,
                  focus: str | None = None) -> None:
        """The grid of day: its files, those in ticked ticked (all if None),
        the focus on focus (else the first)."""
        self._release()  # the file shown is let go
        self.overlay.hide()
        self._view, self._alone, self._day = "day", False, day
        self._saved_ticks = None
        self._root_name = self.root.resolve().name or str(self.root)
        self.viewers.setCurrentWidget(self.day_view)
        self.day_grid.show_day(self.root, self._rels_of(day), ticked, focus)
        self._sync_keys()
        self._update_top()
        if self.isVisible():
            self.day_grid.focus()

    def _day_step(self, step: int) -> None:
        """PgUp/PgDn: the day before (-1) or after (+1), all ticked."""
        if not self._active() or self.overlay.isVisible():
            return
        for day in self._neighbour_day(step):
            self._say("")
            self._show_day(day)

    def _open_alone(self, rel: str) -> None:
        """Enter or a double-click in the grid: rel alone, in the one view."""
        if not self._active() or self._view != "day" or self.overlay.isVisible() \
                or rel not in self.files:
            return
        saved = (self._day, set(self.day_grid.rels()), set(self.day_grid.ticked()))
        self._to_one(rel, alone=True)
        self._saved_ticks = saved

    def _one_at_a_time(self) -> None:
        """G or Esc in the grid: one at a time, on the focused file (an empty
        grid's: the first file of the next day, else the last file)."""
        if not self._active():
            return
        rel = self.day_grid.focused()
        if rel is None:
            order = _day_order(self._day)
            later = [other for other in self.files if other in self._days
                     and _day_order(self._day_of(other)) > order]
            if later:
                rel = min(later, key=lambda other: (_day_order(self._day_of(other)),
                                                    self._day_sort_key(other)))
            else:
                rel = self.files[-1] if self.files else None
        if rel is not None:
            self._say("")
            self._to_one(rel, alone=False)

    def _to_one(self, rel: str, alone: bool) -> None:
        self._view, self._alone = "one", alone
        self._saved_ticks = None
        self.day_grid.clear()  # its thumbnails, and those still to come, go
        self._index = self.files.index(rel)
        self._sync_keys()
        self._show_current()

    def _back_to_day(self) -> None:
        """G or Esc on a file opened alone: the grid of its day, with the
        ticks it had if that is the day it was opened from."""
        rel = self.current()
        if not self._active() or rel is None:
            return
        if rel not in self._days:
            self._open_day()  # not known yet (it can't be, but read it then)
            return
        day = self._day_of(rel)
        ticked = None
        if self._saved_ticks is not None and self._saved_ticks[0] == day:
            _, then, ticked_then = self._saved_ticks
            ticked = {other for other in self._rels_of(day)
                      if other not in then or other in ticked_then}
        self._say("")
        self._show_day(day, ticked, focus=rel)

    def _open_next_day(self) -> None:
        """The grid is empty and its moves are done: the next day, else the
        day before; with no file left, the Preview ends."""
        days = self._day_list()
        if not days:
            self._end(last=True)
            return
        order = _day_order(self._day)
        later = [day for day in days if _day_order(day) > order]
        self._show_day(later[0] if later else days[-1])

    def _check_empty_grid(self) -> None:
        """The queue is idle: an empty grid opens the next day (or ends the
        Preview). Not before: a move that fails, or a put-back queued after
        the moves, brings files back into it."""
        if (self._view == "day" and self._active() and self._running is None
                and not self._queue and not self.day_grid.rels()):
            self._open_next_day()

    # ----- the overlay ---------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.view_area and event.type() == QEvent.Type.Resize:
            self._place_overlay()
        return False

    def _place_overlay(self) -> None:
        area = self.view_area.rect()
        width = max(1, min(OVERLAY_MAX_WIDTH, area.width() - 24))
        height = self.overlay.heightForWidth(width)
        if height <= 0:
            height = self.overlay.sizeHint().height()
        self.overlay.setGeometry((area.width() - width) // 2,
                                 max(0, area.height() - height - 12), width, height)

    def _open_overlay(self) -> None:
        if not self._active():
            return
        if self._view == "day":
            ticked = self.day_grid.ticked()
            if not ticked:
                self._say(NOTHING_TICKED)
                return
            self._overlay_rels, self._overlay_day = ticked, self._day
        elif self.current() is None:
            return
        self._overlay_view = self._view
        self._say("")
        self._overlay_rel = self.current()
        self.overlay.show()
        self._place_overlay()
        self.overlay.raise_()
        self.overlay.setFocus(Qt.FocusReason.OtherFocusReason)
        self._sync_keys()

    def _close_overlay(self) -> None:
        if not self.overlay.isVisible():
            return
        self.overlay.hide()
        self._sync_keys()
        if not self.isVisible():
            return
        if self._view == "day":
            self.day_grid.focus()
        elif self._box is not None:
            self._box.setFocus(Qt.FocusReason.OtherFocusReason)

    def _slot_picked(self, n: int) -> None:
        folder = self._slots[n]
        if folder is None:
            self._say(f"Slot {slot_key(n)} is empty")
            return
        problem = target_problem(self.root, folder)
        if problem is not None:
            self._say(problem)
            return
        self._close_overlay()
        self._move_from_overlay(Path(folder))

    def _other_from_overlay(self) -> None:
        self._close_overlay()
        self._other_folder()

    def _other_folder(self) -> None:
        """Pick a folder (the picker starts at root) and move what M was
        pressed on (the file shown, or the files ticked) there, adding the
        folder to the slots."""
        if not self._overlay_still_there():
            return
        picked = choose_folder(self, PICK_TITLE, self.root)
        if picked is None or not self._active():
            return
        problem = target_problem(self.root, picked)
        if problem is not None:
            self._say(problem)
            return
        if not self._overlay_still_there():  # a put-back landed while the picker was open
            return
        note = self._add_slot(picked)
        if self._move_from_overlay(picked) and note:
            self._say(note)

    def _overlay_still_there(self) -> bool:
        """Whether what M was pressed on is still shown: the file, or the
        grid's day; if not, say so (nothing is moved)."""
        if not self._active():
            return False
        if self._overlay_view == "day":
            if self._view != "day" or self._day != self._overlay_day:
                self._say(DAY_CHANGED)
                return False
            return True
        if self._view != "one":
            self._say(FILE_CHANGED)
            return False
        return self._still_shown(self._overlay_rel)

    def _move_from_overlay(self, target: Path) -> bool:
        """Move what M was pressed on into target (see _move_to, _move_ticked)."""
        if self._overlay_view == "day":
            if not self._overlay_still_there():
                return False
            return self._move_ticked(target, self._overlay_rels)
        return self._move_to(target, self._overlay_rel)

    def _still_shown(self, rel: str | None) -> bool:
        """Whether rel is the file shown; if not, say so (nothing is moved)."""
        if not self._active() or rel is None:
            return False
        if rel != self.current():
            self._say(FILE_CHANGED)
            return False
        return True

    # ----- moving --------------------------------------------------------------------------------

    def _not_needed(self) -> None:
        """N: the file shown, or the grid's ticked files, to <root>/Not needed."""
        target = self.root / NOT_NEEDED
        if self._view == "day":
            self._move_ticked(target, self.day_grid.ticked())
        else:
            self._move_to(target, self.current())

    def _move_ticked(self, target: Path, rels: list[str]) -> bool:
        """Queue the moves of rels (the grid's ticked files, those still in
        it) into target, as one group; they leave the grid at once. Those in
        target already stay, and that is said. False if nothing is moved
        ("Nothing ticked" if none of rels is in the grid)."""
        if not self._active():
            return False
        shown = set(self.day_grid.rels())
        rels = [rel for rel in rels if rel in shown]
        if not rels:
            self._say(NOTHING_TICKED)
            return False
        there = [rel for rel in rels if _same((self.root / rel).parent, target)]
        moving = [rel for rel in rels if rel not in there]
        where = slot_name(self.root, str(target))
        if len(there) == 1:
            self._say(f"{there[0].rpartition('/')[2]} is in {where} already")
        elif there:
            self._say(f"{len(there)} files are in {where} already")
        else:
            self._say("")
        if not moving:
            return False
        group = next(self._group_ids)
        gone = set(moving)
        self.files = [rel for rel in self.files if rel not in gone]
        self.day_grid.remove(moving)
        for rel in moving:
            self._enqueue(partial(self.session.move, rel, Path(target), group),
                          partial(self._moved_ok, rel), partial(self._move_failed, rel),
                          partial(moving_text, rel.rpartition("/")[2], self.root, Path(target)))
        return True

    def _move_to(self, target: Path, rel: str | None) -> bool:
        """Queue the move of rel, the file shown, into target, and show the
        next one. False, with nothing moved, if rel is not the file shown
        any more, or it is in target already."""
        if not self._still_shown(rel):
            return False
        name = rel.rpartition("/")[2]
        if _same((self.root / rel).parent, target):
            self._say(f"{name} is in {slot_name(self.root, str(target))} already")
            return False
        self._say("")
        self._release()  # before the move: nothing holds the file
        del self.files[self._index]
        self._enqueue(partial(self.session.move, rel, Path(target), next(self._group_ids)),
                      partial(self._moved_ok, rel), partial(self._move_failed, rel),
                      partial(moving_text, name, self.root, Path(target)))
        self._go_on()
        return True

    def _go_on(self) -> None:
        """Show the file now at the current place; past the last, end."""
        if self._index >= len(self.files):
            self._end(last=True)
        else:
            self._show_current()

    def _moved_ok(self, rel: str, dst: str) -> None:
        if dst == rel:  # it was there already: nothing moved
            self._reinsert(rel)
        else:
            self._moved.append(rel)

    def _move_failed(self, rel: str, error: object) -> None:
        """rel is back in the list; in the grid it comes back (ticked) once
        the queue is idle, with the others that failed (_flush_returns)."""
        text = f"Couldn't move {rel.rpartition('/')[2]}: {_error_text(error)}"
        if self._ending:
            self._problems.append(text)
            return
        if self._view == "day":
            self._reinsert(rel, show=False)
            self._returns.append(rel)
        else:
            self._reinsert(rel)
        self._tell(text)

    def _flush_returns(self) -> None:
        """The failed moves come back into the grid, laid out once."""
        returns, self._returns = self._returns, []
        if returns and self._view == "day" and not self._ending:
            back = [rel for rel in returns if rel in self.files and self._in_grid_day(rel)]
            self.day_grid.insert_many(back, True)
            self._update_top()
            self._focus_returned(back)

    def _reinsert(self, rel: str, order: float | None = None, show: bool = True) -> int:
        """Put rel back in the list where it was; its place. In the grid, a
        file of its day comes back into it, ticked. With show False neither
        the grid nor the top line is touched (the caller does it once)."""
        if order is None:
            order = self._order.get(rel, float(len(self._order)))
        self._order[rel] = order
        place = bisect.bisect_left(self.files, order,
                                   key=lambda other: self._order.get(other, math.inf))
        self.files.insert(place, rel)
        if self._shown is not None and place <= self._index:
            self._index += 1  # the file shown stays shown
        if self._days_read and rel not in self._days:
            # Moved before the days were read, and back now: one stat, here.
            self._days[rel] = _day_facts(self.root / rel)
        if show:
            if self._in_grid_day(rel):
                self.day_grid.insert(rel, True)
            self._update_top()
        return place

    def _in_grid_day(self, rel: str) -> bool:
        """Whether rel belongs in the grid shown."""
        return self._view == "day" and rel in self._days and self._day_of(rel) == self._day

    def _focus_returned(self, back: list[str]) -> None:
        """Files came back into the grid: unless the focus is on a card still
        there, it goes to the first of them (the page shown, no dialog open)."""
        if not back or not self.isVisible() or QApplication.activeModalWidget() is not None:
            return
        focus = QApplication.focusWidget()
        if focus is not None and focus in self.day_grid.cards():
            return
        first = next((rel for rel in self.day_grid.rels() if rel in back), None)
        self.day_grid.focus(first)

    def _put_back(self) -> None:
        if not self._active():
            return  # what is said stays: a failure is not wiped by Ctrl+Z
        self._enqueue(partial(_put_back_group, self.session, self.root), self._put_back_ok,
                      self._put_back_failed, self._putting_back_text)

    def _putting_back_text(self) -> str | None:
        """What a slow put-back says, asked while it runs: every job before it
        is done then, so the last live move is the one going back."""
        if not self._moved:
            return None
        rel = self._moved[-1]
        return moving_text(rel.rpartition("/")[2], self.root, (self.root / rel).parent)

    def _put_back_ok(self, result) -> None:
        """The last group is back (_put_back_group's result): in the one view
        the file put back last (the group's first) is shown; in the grid the
        files come back into it, ticked, or their day opens."""
        back, error = result
        if not back and error is None:
            self._tell(NOTHING_TO_PUT_BACK)
            return
        placed: list[tuple[str, str]] = []  # (where it was, where it is now)
        keep = self._days_read or self._finding is not None  # else nothing is kept
        for now, day, mtime in back:
            placed.append((self._moved.pop() if self._moved else now, now))
            if keep:
                self._days[now] = (day, mtime)  # read again
        if error is not None:
            self._put_back_failed(error)
        if self._ending or not placed:
            return
        for was, now in placed:
            order = self._order.get(was, float(len(self._order)))
            if now != was:
                order += 0.5  # just after the place its name now has someone else in
            self._reinsert(now, order, show=False)
        last = placed[-1][1]
        if self._view == "day":
            if self._day_of(last) != self._day:
                self._show_day(self._day_of(last), focus=last)
            else:  # into the grid at once, laid out once
                back = [now for _, now in placed if self._in_grid_day(now)]
                self.day_grid.insert_many(back, True)
                self._update_top()
                self._focus_returned(back)
            return
        self._index = self.files.index(last)
        self._show_current()

    def _put_back_failed(self, error: object) -> None:
        name = self._moved[-1].rpartition("/")[2] if self._moved else ""  # the one that failed
        if isinstance(error, FileNotFoundError) and self._moved:
            self._moved.pop()  # the session let it go: the next Ctrl+Z goes on
        text = (f"Couldn't put back {name}: {_error_text(error)}" if name
                else f"Couldn't put back: {_error_text(error)}")
        if self._ending:
            self._problems.append(text)
        else:
            self._tell(text)

    def _arrow(self, event: QKeyEvent) -> bool:
        """← or → (no modifier): the file before or after. Past the last
        file the Preview ends, but not on a held key's repeats."""
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        key = event.key()
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right) \
                or modifiers != Qt.KeyboardModifier.NoModifier:
            return False
        if not self._active() or self.current() is None or self.overlay.isVisible():
            return True
        if key == Qt.Key.Key_Left:
            if self._index > 0:
                self._say("")
                self._index -= 1
                self._show_current()
        elif self._index + 1 < len(self.files):
            self._say("")
            self._index += 1
            self._show_current()
        elif not event.isAutoRepeat():
            self._index += 1
            self._go_on()
        return True

    # ----- the queue ----------------------------------------------------------------------------

    def _enqueue(self, job: Callable[[], object], done: Callable[[object], None],
                 failed: Callable[[object], None],
                 status: Callable[[], str | None] | None = None) -> None:
        """Run job after those queued before it, on the pool; then done(its
        result) or failed(its exception), here. If it takes over
        MOVING_AFTER_MS, status() (asked then) is said until it ends."""
        self._queue.append((job, done, failed, status))
        self._pump()

    def _pump(self) -> None:
        if self._running is not None or not self._queue:
            return
        job, done, failed, status = self._queue.popleft()
        task = Task(_run_job, job, cancellable=False)
        generation = self._generation

        def slow(task=task):
            if (isValid(self) and generation == self._generation and self._running is task
                    and (text := status()) is not None):
                self._status = text
                self._tell(text)

        def ended(result, task=task):
            if not isValid(self) or generation != self._generation:
                return  # the session was let go meanwhile
            self._finished_task, self._running = task, None
            if self._status is not None:
                text, self._status = self._status, None
                self._untell(text)
            ok, value = result
            (done if ok else failed)(value)
            if not self._queue:
                self._flush_returns()
                self._check_empty_grid()
            self._pump()

        task.signals.result.connect(ended)
        task.signals.error.connect(lambda message: ended((False, RuntimeError(message))))
        self._running = task
        self._running_status = status
        task.start()
        if status is not None:
            QTimer.singleShot(MOVING_AFTER_MS, slow)

    # ----- ending -------------------------------------------------------------------------------

    def _show_in_folder(self) -> None:
        """The file shown (the grid's focused one), selected in the file
        manager (nothing moves)."""
        rel = self.day_grid.focused() if self._view == "day" else self.current()
        if rel is not None and self._active() and not self.overlay.isVisible():
            show_folder.show_in_folder(self.root / rel)

    def _leave(self) -> None:
        if self._active() and dialogs.ask(self, LEAVE_TITLE, LEAVE_TEXT, yes="Leave",
                                          no="Stay", default="yes"):
            self._end(last=False)

    def _end(self, last: bool) -> None:
        """End the session once the moves queued are done (see ended)."""
        if not self._active():
            return
        self._ending = True
        self._last = last
        self.overlay.hide()
        self._release()
        self.day_grid.clear()
        self._sync_keys()
        self._set_line(self.title_label, FINISHING)
        self._set_line(self.folder_label, "")
        self._enqueue(partial(_finish, self.session), self._finished,
                      lambda error: self._finished((None, _error_text(error))))

    def _finished(self, result: tuple[str | None, str | None]) -> None:
        run_id, problem = result
        session = self.session
        counts = session.counts() if session is not None else (0, 0)
        problems = list(self._problems)
        if problem is not None:
            problems.append(f"Couldn't finish Preview: {problem}")
        end = PreviewEnd(run_id, counts, self._last, problems)
        self.clear()
        self.ended.emit(end)


# ----- the slot editor ---------------------------------------------------------------------------

class _PathEdit(QLineEdit):
    """The editor's path box. Ctrl+V puts in the first line pasted, and a
    folder copied in a file manager (a file:// URL) as its path."""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste) and not self.isReadOnly():
            mime = QGuiApplication.clipboard().mimeData()
            if mime is None:
                text = ""
            else:  # the first local URL, kept as bytes (see local_path_of_url)
                urls = [url for url in mime.urls() if url.isLocalFile()]
                text = (urls[0].toString(QUrl.ComponentFormattingOption.FullyEncoded)
                        if urls else mime.text())
            text = paste_text(text)
            if text:
                self.insert(text)
            event.accept()
            return
        super().keyPressEvent(event)


class SlotEditor(QDialog):
    """Edits a copy of the slots; slots() is the list as it now stands.

    The path box at the top takes a pasted or typed folder path (see
    folder_from_text): Enter puts it in the first empty slot, if it is an
    existing folder ("Folder not found"), not listed ("Already in the list
    (slot N)"), and there is room ("The list is full"). Add… says the same.

    Add… picks a folder (the picker starts at root, can go anywhere and can
    make one) and puts it in the chosen slot if that is empty, else in the
    first empty one. Remove
    takes the chosen folder out, and the ones after it move up a place;
    Move up and Move down swap it with its neighbour. Either way the slots
    are numbered afresh: 1…9, then 0.

    Keys: ↑↓ choose, Insert or A add, Delete remove, Alt+↑ and Alt+↓ move
    (off while the path box has the focus, even after an add: its Enter
    adds its path, and its Esc empties it, or cancels if it is empty),
    Enter saves (Save) and Esc cancels (Enter presses a button Tab has reached
    instead: widgets.ButtonKeys).
    """

    def __init__(self, root: Path, slots: list[str | None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self._slots = (list(slots) + [None] * SLOTS)[:SLOTS]
        self.setWindowTitle(EDITOR_TITLE)
        self.path_edit = _PathEdit()
        self.path_edit.setPlaceholderText(PATH_PLACEHOLDER)
        self.path_edit.setClearButtonEnabled(True)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("rowList")  # see theme
        self.list_widget.setMinimumWidth(260)
        self.list_widget.ensurePolished()
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName("error")
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.hide()

        self.add_button = QPushButton("Add…")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        side = QVBoxLayout()
        side.setSpacing(6)
        for button in (self.add_button, self.remove_button, self.up_button, self.down_button):
            side.addWidget(button)
        side.addStretch()
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.list_widget, 1)
        row.addLayout(side)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Save")  # the strip says "Enter save"
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(12)
        layout.addWidget(heading(EDITOR_TITLE))
        hint = QLabel("N sends a file to Not needed; M then a number sends it to that "
                      "folder. Folders that aren't there now are greyed.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(hint)
        layout.addWidget(self.path_edit)
        layout.addLayout(row, 1)
        layout.addWidget(self.message_label)
        layout.addWidget(ButtonBar([], [buttons]))
        self.setMinimumWidth(560)

        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.list_widget.currentRowChanged.connect(self._update_buttons)
        shortcuts.describe(self.list_widget, "↑↓", "choose")
        shortcuts.bind(self.list_widget, "Return", "Enter", "save", self.accept, repeat=False)
        words = ((self.add_button, "add"), (self.remove_button, "remove"),
                 (self.up_button, "move up"), (self.down_button, "move down"),
                 (self.ok_button, "save"), (self.cancel_button, "cancel"))
        ButtonKeys([button for button, _ in words])
        for button, word in words:
            shortcuts.describe(button, "Enter", word)
        self._keys = [
            (shortcuts.bind(self, keys, label, word, button.click, repeat=repeat), button)
            for keys, label, word, button, repeat in (
                (["Insert", "A"], "Insert/A", "add", self.add_button, False),
                ("Delete", "Del", "remove", self.remove_button, False),
                ("Alt+Up", "Alt+↑", "move up", self.up_button, True),
                ("Alt+Down", "Alt+↓", "move down", self.down_button, True))]
        shortcuts.bind(self.path_edit, "Return", "Enter", "add", self._add_typed, repeat=False)
        # Esc empties a box with something in it; in an empty one it cancels (the dialog's).
        self._clear_key = shortcuts.bind(self.path_edit, "Escape", "Esc", "clear",
                                         self.path_edit.clear, repeat=False)
        self._clear_key.setEnabled(False)
        self.path_edit.textChanged.connect(self._box_changed)
        shortcuts.pause_while_focused(self.path_edit, [key for key, _ in self._keys],
                                      resume=self._update_buttons)
        shortcuts.describe(self, "Esc", "cancel")
        shortcuts.describe_tab(self)
        self.strip = ShortcutStrip(self)
        layout.addWidget(self.strip)
        self.strip.show_for(self)
        self.setTabOrder(self.path_edit, self.list_widget)
        self._fill(0)
        self.list_widget.setFocus()

    def slots(self) -> list[str | None]:
        return list(self._slots)

    def _fill(self, row: int) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for n, folder in enumerate(self._slots):
            item = QListWidgetItem(f"{slot_key(n)}  {slot_name(self.root, folder)}")
            item.setToolTip(folder or "Empty")
            if not _usable(self.root, folder):
                item.setForeground(QColor(COLORS["muted"]))
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        self.list_widget.setCurrentRow(row)
        self._update_buttons()

    def _update_buttons(self, *_) -> None:
        row = self.list_widget.currentRow()
        filled = 0 <= row < SLOTS and self._slots[row] is not None
        self.add_button.setEnabled(None in self._slots)
        self.remove_button.setEnabled(filled)
        self.up_button.setEnabled(filled and row > 0)
        self.down_button.setEnabled(filled and row < SLOTS - 1)
        if self.path_edit.hasFocus():  # off while typing (pause_while_focused)
            changed = False
            for key, _ in self._keys:
                changed |= key.isEnabled()
                key.setEnabled(False)
        else:
            changed = shortcuts.follow(self._keys)
        if changed:
            self.strip.refresh()

    def _box_changed(self, text: str) -> None:
        if self._clear_key.isEnabled() != bool(text):
            self._clear_key.setEnabled(bool(text))
            self.strip.refresh()

    def _message(self, text: str) -> None:
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def _refusal(self, folder: Path) -> str | None:
        """Why folder can't be listed (the path box's and Add…'s words); None
        if it can."""
        if not folder.is_dir():
            return FOLDER_NOT_FOUND
        problem = target_problem(self.root, folder)
        if problem is not None:
            return problem
        for n, listed in enumerate(self._slots):
            if listed is not None and _same(Path(listed), folder):
                return f"Already in the list (slot {slot_key(n)})"
        if None not in self._slots:
            return PATH_LIST_FULL
        return None

    def _add(self) -> None:
        if None not in self._slots:
            self._message(PATH_LIST_FULL)
            return
        picked = choose_folder(self, ADD_TITLE, self.root)
        if picked is None:
            return
        problem = self._refusal(picked)
        if problem is not None:
            self._message(problem)
            return
        self._message("")
        row = self.list_widget.currentRow()
        if not (0 <= row < SLOTS and self._slots[row] is None):
            row = self._slots.index(None)
        self._slots[row] = str(picked)
        self._fill(row)

    def _add_typed(self) -> None:
        """Put the folder in the path box in the first empty slot, and empty
        the box; or say why not (the box keeps it, to be put right)."""
        try:
            folder = folder_from_text(self.root, self.path_edit.text())
        except ValueError as e:
            self._message(str(e))
            return
        if folder is None:
            self._message("")
            return
        problem = self._refusal(folder)
        if problem is not None:
            self._message(problem)
            return
        self._message("")
        row = self._slots.index(None)
        self._slots[row] = str(folder)
        self.path_edit.clear()
        self._fill(row)

    def _remove(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < SLOTS) or self._slots[row] is None:
            return
        self._message("")
        del self._slots[row]
        self._slots.append(None)
        self._fill(row)

    def _move(self, delta: int) -> None:
        row = self.list_widget.currentRow()
        to = row + delta
        if not (0 <= row < SLOTS and 0 <= to < SLOTS) or self._slots[row] is None:
            return
        self._slots[row], self._slots[to] = self._slots[to], self._slots[row]
        self._fill(to)
