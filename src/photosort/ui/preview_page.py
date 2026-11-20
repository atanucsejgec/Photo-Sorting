"""The Preview page: every file of a folder, one at a time, each moved at
once with one key (see modes.preview for the session that moves them).

The top line reads "12 of 340 · IMG_0001.jpg · 2.3 MB · 14 Feb 2025" (the
size and date are read on a pool thread, a moment later), the line under
it the file's folder (the root's name, then the path inside it) and, for a
PDF, "page 3 of 12". The file is shown by the viewer of its kind
(ui.preview_viewers), made the first time a file of that kind comes up and
kept; a viewer that can't show its file hands over to the info card, with
the reason. Beside it, the folder slots: their keys and names (the path
inside the root; a slot outside the root, or empty, is greyed).

Keys: ←/→ the file before or after (a fresh → past the last file ends the
Preview; a held one's repeats stop there); N moves the file to <root>/Not needed; M opens the folder
overlay, where 1…9 and 0 move it to that slot, Enter (or "Other folder…")
picks another folder inside the root, and Esc closes it; L edits the slots
(SlotEditor); Ctrl+Z puts back the last file moved, and shows it again;
Esc asks whether to leave. The viewer's own keys (Space, Shift+←/→, S,
+/−, F, ↑/↓, PgUp/PgDn, O) are offered to it first: each viewer sits in a
box that takes the focus and hands it every key press that is not one of
the page's shortcuts (a viewer never takes those), then ←/→ to the page.

Moves (and put-backs, and the finish) run one at a time, in order, on pool
threads: the next file shows at once. The viewer lets go of a file before
its move is queued. A move that fails puts the file back in the list where
it was, and says "Couldn't move <name>: <error>"; the session goes on.
What the queue comes back with is added under the message line's text, so
a failure is not wiped by a later note ("Nothing to put back"); the line
keeps the last MESSAGE_LINES of them.

The first media player of the process takes 0.5–2 s to make, on the GUI
thread (it loads Qt's media backend). So a session with a video or a sound
in its list makes the page's one player (preview_viewers.SharedPlayer) as
it opens, under "Getting the player ready…", right after the first file is
shown (or before, if that file is the one that plays), and both media
viewers use it for every file after: a file change never waits for it.

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

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from photosort.modes.preview import (
    NOT_NEEDED, SLOTS, PreviewSession, file_kind, inside_root, outside_message, slot_problem,
)
from photosort.ui import dialogs, shortcuts
from photosort.ui import preview_viewers as pv
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
EDITOR_FULL = "The list is full; remove a folder first"
NOTHING_TO_PUT_BACK = "Nothing to put back"
FILE_CHANGED = "The file changed — press M again"
FINISHING = "Finishing…"
PLAYER_READYING = "Getting the player ready…"
MESSAGE_LINES = 3  # the message line keeps the last this many notes
_PLAYS = ("video", "audio")
PICK_TITLE = "Move to which folder?"
ADD_TITLE = "Add a folder"
EDITOR_TITLE = "Preview folders"
OTHER_BUTTON = "Other folder… (Enter)"
EDIT_BUTTON = "Edit folders (L)"
EMPTY_SLOT = "—"
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
    return folder is not None and slot_problem(root, folder) is None


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
        self._queue: deque[tuple[Callable[[], object], Callable, Callable]] = deque()
        self._running: Task | None = None
        self._finished_task: Task | None = None  # kept alive until its thread lets go
        self._generation = 0  # bumped when a session is let go: its late results are dropped
        self._ending = False
        self._last = False
        self._problems: list[str] = []

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

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self.view_area, 1)
        body.addWidget(side)
        layout.addLayout(body, 1)

        # The page's keys; the viewer's are on its box. All are off while the
        # overlay is open (it has its own) and once the Preview is ending.
        # ← and → reach the page through the box (_arrow, recorded there), so
        # that a held → never runs on past the last file.
        self._keys = [
            shortcuts.bind(self, "N", "N", "not needed",
                           lambda: self._move_to(self.root / NOT_NEEDED, self.current()),
                           repeat=False),
            shortcuts.bind(self, "M", "M", "move to…", self._open_overlay, repeat=False),
            shortcuts.bind(self, "L", "L", "edit folders", self._edit_slots, repeat=False),
            shortcuts.bind(self, "Ctrl+Z", "Ctrl+Z", "put back", self._put_back,
                           repeat=False),
            shortcuts.bind(self, "Escape", "Esc", "leave", self._leave, repeat=False),
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
        if self._shown is None and self._box is None and self.files:
            self._show_current()

    def shutdown(self) -> None:
        """The window is closing: do the moves still queued and finish the
        session now, on this thread; nothing is told (ended is not emitted)."""
        session = self.session
        if session is None:
            return
        self._release()
        self.overlay.hide()
        self._generation += 1  # the running job's result, when it comes, is dropped
        if self._running is not None:
            QThreadPool.globalInstance().waitForDone()  # it ends before those after it
        while self._queue:
            job, _done, _failed = self._queue.popleft()
            _run_job(job)
        _run_job(partial(_finish, session))  # safe if it was queued already
        self.clear()

    def clear(self) -> None:
        """Let the session go (it is over) and every viewer's file."""
        self._release()
        self.overlay.hide()
        self._generation += 1
        self._queue.clear()
        self._running = None
        self.session = None
        self.files = []
        self._order = {}
        self._moved = []
        self._box = None
        self._ending = False
        self.title_label.clear()
        self.folder_label.clear()
        self._say("")
        self._sync_keys()

    def focus_first(self) -> None:
        if self.overlay.isVisible():
            self.overlay.setFocus(Qt.FocusReason.OtherFocusReason)
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

    def _sync_keys(self) -> None:
        on = self._active() and not self.overlay.isVisible()
        changed = False
        for key in self._keys:
            if key.isEnabled() != on:
                key.setEnabled(on)
                changed = True
        if changed:
            self.keys_changed.emit()

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
        there (the owner has acted since); hidden while there is none."""
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def _tell(self, text: str) -> None:
        """A problem or a note that a move, a put-back or the queue came
        back with: added under what is there, so a failure stays in view."""
        lines = [line for line in self.message_label.text().split("\n") if line]
        if text not in lines:
            lines.append(text)
        self._say("\n".join(lines[-MESSAGE_LINES:]))

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
            label.setToolTip(folder or "Empty")
            look = "" if _usable(self.root, folder) else "muted"
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
        if not self._active() or self.current() is None:
            return
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
        if self._box is not None and self.isVisible():
            self._box.setFocus(Qt.FocusReason.OtherFocusReason)

    def _slot_picked(self, n: int) -> None:
        folder = self._slots[n]
        if folder is None:
            self._say(f"Slot {slot_key(n)} is empty")
            return
        problem = slot_problem(self.root, folder)
        if problem is not None:
            self._say(problem)
            return
        self._close_overlay()
        self._move_to(Path(folder), self._overlay_rel)

    def _other_from_overlay(self) -> None:
        rel = self._overlay_rel
        self._close_overlay()
        self._other_folder(rel)

    def _other_folder(self, rel: str | None) -> None:
        """Pick a folder inside root and move rel (the file shown when M
        was pressed) there, adding the folder to the slots."""
        if not self._still_shown(rel):
            return
        picked = choose_folder(self, PICK_TITLE, self.root)
        if picked is None or not self._active():
            return
        if not inside_root(self.root, picked):
            self._say(outside_message(self.root))
            return
        if not self._still_shown(rel):  # a put-back landed while the picker was open
            return
        note = self._add_slot(picked)
        if self._move_to(picked, rel) and note:
            self._say(note)

    def _still_shown(self, rel: str | None) -> bool:
        """Whether rel is the file shown; if not, say so (nothing is moved)."""
        if not self._active() or rel is None:
            return False
        if rel != self.current():
            self._say(FILE_CHANGED)
            return False
        return True

    # ----- moving --------------------------------------------------------------------------------

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
        self._enqueue(partial(self.session.move, rel, Path(target)),
                      partial(self._moved_ok, rel), partial(self._move_failed, rel))
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
            return
        self._moved.append(rel)

    def _move_failed(self, rel: str, error: object) -> None:
        text = f"Couldn't move {rel.rpartition('/')[2]}: {_error_text(error)}"
        if self._ending:
            self._problems.append(text)
            return
        self._reinsert(rel)
        self._tell(text)

    def _reinsert(self, rel: str, order: float | None = None) -> int:
        """Put rel back in the list where it was; its place."""
        if order is None:
            order = self._order.get(rel, float(len(self._order)))
        self._order[rel] = order
        place = sum(1 for other in self.files if self._order.get(other, float("inf")) < order)
        self.files.insert(place, rel)
        if self._shown is not None and place <= self._index:
            self._index += 1  # the file shown stays shown
        self._update_top()
        return place

    def _put_back(self) -> None:
        if not self._active():
            return  # what is said stays: a failure is not wiped by Ctrl+Z
        self._enqueue(self.session.put_back, self._put_back_ok, self._put_back_failed)

    def _put_back_ok(self, back: str | None) -> None:
        if back is None:
            self._tell(NOTHING_TO_PUT_BACK)
            return
        rel = self._moved.pop() if self._moved else back
        if self._ending:
            return
        order = self._order.get(rel, float(len(self._order)))
        if back != rel:
            order += 0.5  # just after the place its name now has someone else in
        self._index = self._reinsert(back, order)
        self._show_current()

    def _put_back_failed(self, error: object) -> None:
        if isinstance(error, FileNotFoundError) and self._moved:
            self._moved.pop()  # the session let it go: the next Ctrl+Z goes on
        text = f"Couldn't put back: {_error_text(error)}"
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
                 failed: Callable[[object], None]) -> None:
        """Run job after those queued before it, on the pool; then done(its
        result) or failed(its exception), here."""
        self._queue.append((job, done, failed))
        self._pump()

    def _pump(self) -> None:
        if self._running is not None or not self._queue:
            return
        job, done, failed = self._queue.popleft()
        task = Task(_run_job, job, cancellable=False)
        generation = self._generation

        def ended(result, task=task):
            if not isValid(self) or generation != self._generation:
                return  # the session was let go meanwhile
            self._finished_task, self._running = task, None
            ok, value = result
            (done if ok else failed)(value)
            self._pump()

        task.signals.result.connect(ended)
        task.signals.error.connect(lambda message: ended((False, RuntimeError(message))))
        self._running = task
        task.start()

    # ----- ending -------------------------------------------------------------------------------

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

class SlotEditor(QDialog):
    """Edits a copy of the slots; slots() is the list as it now stands.

    Add… picks a folder inside root (the picker can make one) and puts it in
    the chosen slot if that is empty, else in the first empty one. Remove
    takes the chosen folder out, and the ones after it move up a place;
    Move up and Move down swap it with its neighbour. Either way the slots
    are numbered afresh: 1…9, then 0.

    Keys: ↑↓ choose, Insert or A add, Delete remove, Alt+↑ and Alt+↓ move,
    Enter saves (Save) and Esc cancels (Enter presses a button Tab has reached
    instead: widgets.ButtonKeys).
    """

    def __init__(self, root: Path, slots: list[str | None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self._slots = (list(slots) + [None] * SLOTS)[:SLOTS]
        self.setWindowTitle(EDITOR_TITLE)
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
                      f"folder. Folders outside {self.root.resolve().name} are greyed.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(hint)
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
        shortcuts.describe(self, "Esc", "cancel")
        shortcuts.describe_tab(self)
        self.strip = ShortcutStrip(self)
        layout.addWidget(self.strip)
        self.strip.show_for(self)
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
        if shortcuts.follow(self._keys):
            self.strip.refresh()

    def _message(self, text: str) -> None:
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def _add(self) -> None:
        if None not in self._slots:
            self._message(EDITOR_FULL)
            return
        picked = choose_folder(self, ADD_TITLE, self.root)
        if picked is None:
            return
        if not inside_root(self.root, picked):
            self._message(outside_message(self.root))
            return
        if any(f is not None and _same(Path(f), picked) for f in self._slots):
            self._message(f"{slot_name(self.root, str(picked))} is listed already")
            return
        self._message("")
        row = self.list_widget.currentRow()
        if not (0 <= row < SLOTS and self._slots[row] is None):
            row = self._slots.index(None)
        self._slots[row] = str(picked)
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
