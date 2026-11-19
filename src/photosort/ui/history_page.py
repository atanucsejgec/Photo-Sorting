"""The History page: every past sort of every folder, newest first, from
the central store; several can be undone or forgotten at once.

"This folder only" (the switch, or F) shows just the runs of the folder
chosen on Home and the folders inside it. Rows are picked with the arrows,
Shift/Ctrl, Space (the focused row on or off) and Ctrl+A.

Undo (the button, or Enter) asks first, with "Cancel" focused, then undoes
the picked runs newest first in one worker.Task (undo_many). That first
finishes any moved folder's re-pointing left pending (refusing to undo
while it fails: finish_moved_folders, which Done's undo runs first too),
then brings in old records (migrate.bring_in) of each of their folders, so
a newer run that blocks one is seen. It stops at the first run that is blocked or
fails: the newer ones stay undone, the older ones are not tried, and the
message says which. undo_running(True/False) says when it starts and ends,
so the window can keep the sidebar locked meanwhile (an undo can't stop
part-way, and the window waits for it before closing).

Forget (the button, or Delete) deletes the picked records, never a file. A
run that older, not undone runs depend on (journal records they placed
files that it moved: undo.dependents) is only forgotten together with them.

A run whose folder is gone shows "Folder not found"; undoing it first asks
where the folder is now. A folder holding at least one of the run's moved
files at its recorded place is taken as the same one: every run of the old
path, or of a folder inside it, is re-pointed there (journal.relocate_root).
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from photosort import journal, migrate
from photosort.journal import (
    RunInfo, forget_runs, load_all_runs, load_moves, relocate_root,
)
from photosort.modes import MODE_LABELS
from photosort.ui import dialogs, shortcuts
from photosort.ui.theme import heading, page_spacing
from photosort.ui.widgets import Switch
from photosort.ui.worker import Task
from photosort.undo import (
    BlockingRun, UndoBlocked, UndoResult, blocking_runs, dependents, related_roots, undo_run,
)

_log = logging.getLogger("photosort")
_STATUS_LABELS = {"done": "Done", "cancelled": "Cancelled", "failed": "Failed"}
_UNREADABLE = (OSError, ValueError, KeyError, TypeError)

NOT_FOUND = "Folder not found"
WHERE = "Where is this folder now?"
NOT_SAME = "This doesn't look like the same folder"
DEPEND = "Forget the older runs that depend on it too, or undo it first."
FORGET = "Your files stay where they are, but these runs can no longer be undone."
PENDING = ("Could not undo yet: some runs of a moved folder still point to its old "
           "place, and nothing is undone until they all point to the new one")
RETRIED = ("It is tried again before every undo, and nothing is undone until "
           "it works.")
INTRO = ("Past sorts of every folder, newest first. "
         "Undo newer runs before older ones that touched the same files.")


def blocked_text(blocking: list[BlockingRun]) -> str:
    """Why a run can't be undone yet: the newer runs (each says which
    folder's history it is in), in the words the Done page and this page
    both use."""
    return f"Undo the newer run(s) first: {', '.join(map(str, blocking))}"


def undo_with_message(root: Path, run_id: str, progress=None) -> UndoResult:
    """undo_run, its failure in the owner's words: the one undo of the Done
    page and this page."""
    try:
        return undo_run(root, run_id, progress=progress)
    except UndoBlocked as e:
        raise RuntimeError(blocked_text(e.blocking)) from e
    except Exception as e:
        raise RuntimeError(f"Could not undo: {str(e) or type(e).__name__}") from e


def undo_summary(result: UndoResult) -> str:
    """What an undo did, in the words the Done page and this page both use."""
    if result.failed:
        lines = [f"Undo not finished: {result.restored} files put back."]
    else:
        lines = [f"Undo complete: {result.restored} files put back."]
    lines += _after_lines(result)
    if result.failed:
        lines.append(f"{len(result.failed)} files couldn't be put back yet. Close any "
                     "program using them and press Undo again.")
    return "\n".join(lines)


def _after_lines(result: UndoResult) -> list[str]:
    lines = []
    if result.renamed:
        lines.append(f"{len(result.renamed)} files were put back under a new name "
                     "because the original name was taken.")
    if result.missing:
        lines.append(f"{len(result.missing)} files were moved or deleted after the "
                     "sort, so they were left where they are.")
    return lines


@dataclass
class ManyUndone:
    """What undo_many did: the runs undone, newest first, and the run it
    stopped at with why (its error, or its undo_summary when some of its
    files couldn't be put back), or None."""

    undone: list[tuple[str, UndoResult]] = field(default_factory=list)
    stopped: tuple[str, str, UndoResult | None] | None = None  # (run_id, why, partial)


def finish_moved_folders(cancel=None) -> None:
    """journal.finish_relocations, first thing in every undo (a worker
    Task's first function): a run still recorded under a moved folder's old
    path would not be seen to block, so while one can't be re-pointed the
    undo refuses (RuntimeError, in the owner's words)."""
    try:
        journal.finish_relocations()
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{PENDING}: {str(e) or type(e).__name__}") from e


def undo_many(run_ids: list[str], progress=None) -> ManyUndone:
    """Undo the runs, newest first, one after another (on a worker thread),
    stopping at the first one blocked or failing.

    Moved folders' pending re-pointing is finished first (else it raises,
    undoing nothing), then each run's root is read from the store, and the
    old records of each of those folders are brought in (a newer old run may
    block one)."""
    finish_moved_folders()
    roots = {r.run_id: r.root for r in load_all_runs()}
    for root in dict.fromkeys(roots[i] for i in run_ids if i in roots):
        migrate.bring_in(root)
    outcome = ManyUndone()
    for run_id in run_ids:
        root = roots.get(run_id)
        if root is None:
            outcome.stopped = (run_id, "Could not undo: its record is gone", None)
            return outcome
        try:
            result = undo_with_message(root, run_id, progress=progress)
        except RuntimeError as e:
            outcome.stopped = (run_id, str(e), None)
            return outcome
        if result.failed:
            outcome.stopped = (run_id, undo_summary(result), result)
            return outcome
        outcome.undone.append((run_id, result))
    return outcome


def _runs_word(n: int) -> str:
    return f"{n} run" if n == 1 else f"{n} runs"


def _files_put_back(n: int) -> str:
    return f"{n} file put back" if n == 1 else f"{n} files put back"


def many_summary(outcome: ManyUndone, total: int) -> str:
    """What undoing total runs (more than one) did, in the owner's words."""
    ids = ", ".join(run_id for run_id, _ in outcome.undone)
    combined = UndoResult(restored=sum(r.restored for _, r in outcome.undone))
    for _, r in outcome.undone:
        combined.missing += r.missing
        combined.renamed += r.renamed
    if outcome.stopped is None:
        lines = [f"Undo complete: {total} runs undone ({ids}), "
                 f"{_files_put_back(combined.restored)}."]
        return "\n".join(lines + _after_lines(combined))
    run_id, why, _ = outcome.stopped
    k = len(outcome.undone)
    first = f"Undo stopped: {k} of {total} runs undone"
    first += f" ({ids}), {_files_put_back(combined.restored)}." if k else "."
    lines = [first, *_after_lines(combined), f"Stopped at {run_id}: {why}"]
    if k + 1 < total:
        lines.append("The older runs were not tried.")
    return "\n".join(lines)


def choose_folder(parent: QWidget | None, title: str, start: str) -> Path | None:
    """The system's folder picker, titled title, opening at start; the
    folder picked, or None."""
    folder = QFileDialog.getExistingDirectory(parent, title, start)
    return Path(folder) if folder else None


def looks_like(root: Path, run_id: str, picked: Path) -> bool:
    """Whether picked holds at least one file the run moved, at its
    recorded place (dst, relative to the run's root)."""
    try:
        moves = load_moves(root, run_id)
    except _UNREADABLE:
        return False
    return any(m.done and os.path.lexists(Path(picked) / m.dst) for m in moves)


def _nearest_existing(path: Path) -> Path:
    for folder in (path, *path.parents):
        if folder.is_dir():
            return folder
    return path


@dataclass(frozen=True)
class _Row:
    run_id: str
    root: Path
    date: str
    mode: str
    files: str
    status: str
    undone: bool
    found: bool  # its folder is there


def _row(run: RunInfo, found: bool) -> _Row:
    files = str(run.moved)
    if run.undone:
        status = "Undone"
    elif not found:
        status = NOT_FOUND
    elif run.status == "running":
        # The run died before finishing, so its meta "moved" is stale; the
        # journal holds every move it recorded.
        status = "Interrupted"
        try:
            files = str(len(load_moves(run.root, run.run_id)))
        except _UNREADABLE:
            files = "?"
    else:
        status = _STATUS_LABELS.get(run.status, run.status.capitalize())
    return _Row(
        run_id=run.run_id,
        root=run.root,
        date=run.started.astimezone().strftime("%Y-%m-%d %H:%M"),
        mode=MODE_LABELS.get(run.mode, run.mode),
        files=files,
        status=status,
        undone=run.undone,
        found=found,
    )


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or path.is_relative_to(folder)


class _SpaceToggles(QObject):
    """Space turns the table's focused row on or off (Qt's own Space
    selects it alone); a held Space does it once."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            if not event.isAutoRepeat():
                index = self._table.currentIndex()
                if index.isValid():
                    self._table.selectionModel().select(
                        index, QItemSelectionModel.SelectionFlag.Toggle
                        | QItemSelectionModel.SelectionFlag.Rows)
            return True
        return False


class HistoryPage(QWidget):
    """Every run of every folder (see load), with Undo and Forget on the
    selected ones."""

    undo_running = Signal(bool)
    keys_changed = Signal()  # a key was switched on or off (the strip redraws)

    COLUMNS = ["When", "Mode", "Folder", "Files", "Status"]
    _FOLDER = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root: Path | None = None  # the Home folder, for "This folder only"
        self._rows: list[_Row] = []
        self._related: dict[Path, list[Path]] = {}  # related_roots, per refresh
        self._task: Task | None = None
        self._undoing = False
        self._total = 0  # runs the undo under way was asked to undo

        layout = QVBoxLayout(self)
        page_spacing(layout)
        layout.addWidget(heading("History"))
        self.intro_label = QLabel(INTRO)
        self.intro_label.setObjectName("muted")
        self.intro_label.setWordWrap(True)
        self.intro_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.intro_label)

        self.runs_table = QTableWidget(0, len(self.COLUMNS))
        self.runs_table.setHorizontalHeaderLabels(self.COLUMNS)
        header = self.runs_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self._FOLDER, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)
        self.runs_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.runs_table.setWordWrap(False)
        self.runs_table.verticalHeader().hide()
        self.runs_table.setShowGrid(False)
        self.runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.runs_table.setTabKeyNavigation(False)  # Tab leaves the table
        self.runs_table.installEventFilter(_SpaceToggles(self.runs_table))
        self.runs_table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.runs_table, 1)

        self.blocked_label = QLabel()  # why the selected run can't be undone yet
        self.blocked_label.setWordWrap(True)
        layout.addWidget(self.blocked_label)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.result_label)

        row = QHBoxLayout()
        self.filter_switch = Switch("This folder only")
        self.filter_switch.toggled.connect(lambda _: self.refresh())
        self.forget_button = QPushButton("Forget…")
        self.forget_button.setMinimumWidth(120)
        self.forget_button.clicked.connect(self._forget)
        self.undo_button = QPushButton("Undo…")
        self.undo_button.setMinimumWidth(120)
        self.undo_button.clicked.connect(self._undo)
        row.addWidget(self.filter_switch)
        row.addStretch()
        row.addWidget(self.forget_button)
        row.addWidget(self.undo_button)
        layout.addLayout(row)

        shortcuts.describe(self.runs_table, "↑↓", "choose")
        shortcuts.describe(self.runs_table, "Space", "select")
        shortcuts.describe(self.runs_table, "Ctrl+A", "all")
        shortcuts.describe(self.filter_switch, "Space", "on/off")
        self._undo_key = shortcuts.bind(self, "Return", "Enter", "undo", self._undo,
                                        repeat=False)
        self._forget_key = shortcuts.bind(self, "Delete", "Del", "forget", self._forget,
                                          repeat=False)
        self._filter_key = shortcuts.bind(self, "F", "F", "this folder",
                                          self.filter_switch.toggle, repeat=False)
        self.load(None)

    def show_error(self, text: str) -> None:
        dialogs.warn(self, "History", text)

    # ----- the list --------------------------------------------------------

    def load(self, root: Path | None) -> None:
        """List every run; root (the Home folder, or None) is the folder
        "This folder only" keeps."""
        self.root = None if root is None else Path(os.path.abspath(root))
        self.result_label.setText("")
        has_root = self.root is not None
        if not has_root:
            self.filter_switch.blockSignals(True)
            self.filter_switch.setChecked(False)
            self.filter_switch.blockSignals(False)
        self.filter_switch.setEnabled(has_root)
        self.filter_switch.setToolTip(
            f"Only the runs of {self.root} and the folders inside it" if has_root
            else "Choose a folder on Sort first")
        if self._filter_key.isEnabled() != has_root:
            self._filter_key.setEnabled(has_root)
            self.keys_changed.emit()
        self.refresh()

    def refresh(self) -> None:
        """Reload the runs from the central store, keeping the selected runs
        selected (or else selecting the first)."""
        keep = [r.run_id for r in self._selected_rows()]
        current = self._current_row()
        try:
            journal.finish_relocations()  # so each Status is right
        except (OSError, ValueError) as e:
            _log.warning("Moved folders not all re-pointed yet: %s", e)
        runs = load_all_runs()
        if self.filter_switch.isChecked() and self.root is not None:
            runs = [r for r in runs if _inside(r.root, self.root)]
        found: dict[Path, bool] = {}
        for run in runs:
            if run.root not in found:
                found[run.root] = run.root.is_dir()
        self._rows = [_row(run, found[run.root]) for run in runs]
        self._related = {}

        table = self.runs_table
        table.blockSignals(True)
        table.clearSelection()
        table.setRowCount(len(self._rows))
        for n, r in enumerate(self._rows):
            texts = (r.date, r.mode, str(r.root), r.files, r.status)
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r.run_id)
                if col == self._FOLDER:
                    item.setToolTip(text)
                table.setItem(n, col, item)
        ids = [r.run_id for r in self._rows]
        rows = [ids.index(i) for i in keep if i in ids] or ([0] if self._rows else [])
        model, flags = table.selectionModel(), (QItemSelectionModel.SelectionFlag.Select
                                                | QItemSelectionModel.SelectionFlag.Rows)
        if rows:
            to = ids.index(current) if current in ids and ids.index(current) in rows \
                else min(rows)
            model.setCurrentIndex(table.model().index(to, 0),
                                  QItemSelectionModel.SelectionFlag.NoUpdate)
            for n in rows:
                model.select(table.model().index(n, 0), flags)
        table.blockSignals(False)
        self._update_buttons()

    def _selected_rows(self) -> list[_Row]:
        """The selected runs, newest first."""
        rows = sorted(i.row() for i in self.runs_table.selectionModel().selectedRows())
        return [self._rows[n] for n in rows if n < len(self._rows)]

    def _current_row(self) -> str | None:
        n = self.runs_table.currentIndex().row()
        return self._rows[n].run_id if 0 <= n < len(self._rows) else None

    def _related_of(self, root: Path) -> list[Path]:
        if root not in self._related:
            self._related[root] = related_roots(root)
        return self._related[root]

    def _update_buttons(self) -> None:
        """Undo is on when a picked run can be undone; for one run alone it
        also says why not (several are checked as they are undone, as that
        would cost a look at every record per pick)."""
        rows = self._selected_rows()
        todo = [r for r in rows if not r.undone]
        enabled, tip = False, ""
        if not rows or self._undoing:
            pass
        elif not todo:
            tip = "Already undone"
        elif len(rows) > 1 or not todo[0].found:
            enabled = True
        else:
            row = todo[0]
            try:
                blocking = blocking_runs(row.root, row.run_id, self._related_of(row.root))
            except _UNREADABLE:
                tip = "This run's undo record can't be read"
            else:
                if blocking:
                    tip = blocked_text(blocking)
                else:
                    enabled = True
        self.undo_button.setEnabled(enabled)
        self.undo_button.setToolTip(tip)
        self.blocked_label.setText("" if rows and not todo else tip)
        self.forget_button.setEnabled(bool(rows) and not self._undoing)
        if shortcuts.follow([(self._undo_key, self.undo_button),
                             (self._forget_key, self.forget_button)]):
            self.keys_changed.emit()

    # ----- undo ------------------------------------------------------------

    def _undo(self) -> None:
        if self._undoing or not self.undo_button.isEnabled():
            return
        todo = [r for r in self._selected_rows() if not r.undone]
        roots = self._find_folders(todo) if todo else None
        if roots is None:
            return
        # Re-pointed runs keep their row (date, mode, files) but take their
        # new root; one may have left "This folder only"'s list meanwhile.
        todo = [r for r in todo if r.run_id in roots]
        if not todo:
            return
        if len(todo) == 1:
            row = todo[0]
            question = ("Undo this sort?", f"Put back the {row.files} files this {row.mode} "
                                           f"sort of {row.date} moved?")
        else:
            question = (f"Undo {len(todo)} runs?",
                        f"Put back the files of these {len(todo)} runs, newest first?")
        if not dialogs.ask(self, *question, yes="Undo", no="Cancel", default="no"):
            return
        self._undoing = True
        self._total = len(todo)
        self._update_buttons()
        self.result_label.setText("Putting files back…")
        task = Task(undo_many, [r.run_id for r in todo], cancellable=False)
        task.signals.result.connect(self._on_undone)
        task.signals.error.connect(self._on_undo_error)
        self._task = task  # keep it alive while it runs
        self.undo_running.emit(True)
        task.start()

    def _find_folders(self, todo: list[_Row]) -> dict[str, Path] | None:
        """Ask where each picked run's missing folder is now, and re-point
        its runs there. Return every run's root as the store now has it, or
        None if the owner cancels, the folder picked is not the same, or the
        records can't all be re-pointed (shown)."""
        for wanted in todo:
            roots = {r.run_id: r.root for r in load_all_runs()}
            root = roots.get(wanted.run_id)
            if root is None or root.is_dir():
                continue  # forgotten meanwhile, or re-pointed with an earlier one
            picked = choose_folder(self, WHERE, str(_nearest_existing(root.parent)))
            if picked is None:
                return None
            picked = Path(os.path.abspath(picked))
            if not looks_like(root, wanted.run_id, picked):
                self.show_error(f"{NOT_SAME}.\nNone of the files this sort moved are "
                                f"in {picked}.")
                return None
            try:
                relocate_root(root, picked)
            except (OSError, ValueError) as e:
                self.refresh()
                self.show_error(f"Couldn't point every run to the new folder: "
                                f"{str(e) or type(e).__name__}\n{RETRIED}")
                return None
            self.refresh()
        return {r.run_id: r.root for r in load_all_runs()}

    def undoing(self) -> bool:
        return self._undoing

    def _on_undone(self, outcome: ManyUndone) -> None:
        self._undoing = False
        error = None
        if self._total == 1:
            if outcome.undone:
                text = undo_summary(outcome.undone[0][1])
            elif outcome.stopped[2] is not None:  # some files are left to retry
                text = outcome.stopped[1]
            else:
                text, error = "", outcome.stopped[1]
        else:
            text = many_summary(outcome, self._total)
            if outcome.stopped is not None:
                error = text
        self.result_label.setText(text)
        self.refresh()
        self.undo_running.emit(False)
        if error is not None:
            self.show_error(error)

    def _on_undo_error(self, message: str) -> None:  # bring_in raised (it shouldn't)
        self._undoing = False
        self.result_label.setText("")
        self.refresh()
        self.undo_running.emit(False)
        self.show_error(message)

    # ----- forget ----------------------------------------------------------

    def _forget(self) -> None:
        rows = self._selected_rows()
        if not rows or self._undoing:
            return
        chosen = [r.run_id for r in rows]
        try:
            extra = self._dependents_of(rows)
        except _UNREADABLE as e:
            self.show_error("Couldn't check which runs depend on these, so nothing was "
                            f"forgotten: {str(e) or type(e).__name__}")
            return
        if extra:
            total = len(chosen) + len(extra)
            self._select_ids(chosen + extra)  # the owner sees what goes
            if not dialogs.ask(self, "Older runs depend on it",
                               f"{DEPEND}\n\n{', '.join(extra)}", yes=f"Forget all {total}",
                               no="Cancel", default="no"):
                return
        else:
            text = "" if all(r.undone for r in rows) else FORGET
            if not dialogs.ask(self, f"Forget {_runs_word(len(chosen))}?", text,
                               yes="Forget", no="Keep", default="no"):
                return
        ids = chosen + extra
        try:
            forget_runs(ids)
        except OSError as e:
            self.refresh()
            self.show_error(f"Couldn't forget the runs: {str(e) or type(e).__name__}")
            return
        self.refresh()
        self.result_label.setText(f"Forgot {_runs_word(len(ids))}.")

    def _select_ids(self, run_ids: list[str]) -> None:
        """Select the rows of run_ids (those listed) as well."""
        model = self.runs_table.selectionModel()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for n, row in enumerate(self._rows):
            if row.run_id in run_ids:
                model.select(self.runs_table.model().index(n, 0), flags)

    def _dependents_of(self, rows: list[_Row]) -> list[str]:
        """The run ids, newest first, of the older not-undone runs that the
        rows (or those found in turn) block and that are not among them.
        Raises OSError or ValueError when a record can't be read."""
        chosen = {r.run_id for r in rows}
        extra: dict[str, RunInfo] = {}
        queue = [(r.root, r.run_id) for r in rows if not r.undone]
        while queue:
            root, run_id = queue.pop()
            for run in dependents(root, run_id):
                if run.run_id not in chosen and run.run_id not in extra:
                    extra[run.run_id] = run
                    queue.append((run.root, run.run_id))
        found = sorted(extra.values(), key=lambda r: (r.started, r.run_id), reverse=True)
        return [r.run_id for r in found]
