"""The History page: the past sorts of the folder chosen on Home, newest
first, each one undoable.

Undo (the button, or Enter) asks first, with "Cancel" focused. It runs in a
worker.Task, one at a time; undo_running(True/False) says when one starts and
ends, so the window can keep the sidebar locked meanwhile (an undo can't stop
part-way, and the window waits for it before closing).
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from photosort.journal import RunInfo, load_moves, load_runs
from photosort.modes import MODE_LABELS
from photosort.ui import dialogs, shortcuts
from photosort.ui.theme import heading, page_spacing
from photosort.ui.worker import Task
from photosort.undo import (
    BlockingRun, UndoBlocked, UndoResult, blocking_runs, related_folders, undo_run,
)

_STATUS_LABELS = {"done": "Done", "cancelled": "Cancelled", "failed": "Failed"}
_UNREADABLE = (OSError, ValueError, KeyError, TypeError)


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
    if result.renamed:
        lines.append(f"{len(result.renamed)} files were put back under a new name "
                     "because the original name was taken.")
    if result.missing:
        lines.append(f"{len(result.missing)} files were moved or deleted after the "
                     "sort, so they were left where they are.")
    if result.failed:
        lines.append(f"{len(result.failed)} files couldn't be put back yet. Close any "
                     "program using them and press Undo again.")
    return "\n".join(lines)


@dataclass(frozen=True)
class _Row:
    run_id: str
    date: str
    mode: str
    files: str
    status: str
    undone: bool


def _row(root: Path, run: RunInfo) -> _Row:
    files = str(run.moved)
    if run.undone:
        status = "Undone"
    elif run.status == "running":
        # The run died before finishing, so its meta "moved" is stale; the
        # journal holds every move it recorded.
        status = "Interrupted"
        try:
            files = str(len(load_moves(root, run.run_id)))
        except _UNREADABLE:
            files = "?"
    else:
        status = _STATUS_LABELS.get(run.status, run.status.capitalize())
    return _Row(
        run_id=run.run_id,
        date=run.started.astimezone().strftime("%Y-%m-%d %H:%M"),
        mode=MODE_LABELS.get(run.mode, run.mode),
        files=files,
        status=status,
        undone=run.undone,
    )


NO_FOLDER = "Choose a folder first, on Sort: its past sorts are listed here."


class HistoryPage(QWidget):
    """The runs of one folder (see load), with Undo on the selected one."""

    undo_running = Signal(bool)
    keys_changed = Signal()  # a key was switched on or off (the strip redraws)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root: Path | None = None
        self._rows: list[_Row] = []
        self._related: list[Path] = []  # folders around/inside root with runs
        self._task: Task | None = None
        self._undoing = False

        layout = QVBoxLayout(self)
        page_spacing(layout)
        layout.addWidget(heading("History"))
        self.intro_label = QLabel()
        self.intro_label.setObjectName("muted")
        self.intro_label.setWordWrap(True)
        self.intro_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.intro_label)

        self.runs_table = QTableWidget(0, 4)
        self.runs_table.setHorizontalHeaderLabels(["Date", "Mode", "Files", "Status"])
        self.runs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.runs_table.horizontalHeader().setHighlightSections(False)
        self.runs_table.verticalHeader().hide()
        self.runs_table.setShowGrid(False)
        self.runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.runs_table.setTabKeyNavigation(False)  # Tab leaves the table
        self.runs_table.itemSelectionChanged.connect(self._update_undo_button)
        layout.addWidget(self.runs_table, 1)

        self.blocked_label = QLabel()  # why the selected run can't be undone yet
        self.blocked_label.setWordWrap(True)
        layout.addWidget(self.blocked_label)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        row = QHBoxLayout()
        self.undo_button = QPushButton("Undo…")
        self.undo_button.setMinimumWidth(120)
        self.undo_button.clicked.connect(self._undo)
        row.addStretch()
        row.addWidget(self.undo_button)
        layout.addLayout(row)

        shortcuts.describe(self.runs_table, "↑↓", "choose")
        self._undo_key = shortcuts.bind(self, "Return", "Enter", "undo", self._undo,
                                        repeat=False)
        self.load(None)

    def show_error(self, text: str) -> None:
        dialogs.warn(self, "History", text)

    def load(self, root: Path | None) -> None:
        """Show the runs of root, or, with None, say to choose a folder first."""
        self.root = None if root is None else Path(root)
        self.result_label.setText("")
        if self.root is None:
            self.intro_label.setText(NO_FOLDER)
        else:
            self.intro_label.setText(
                f"Past sorts in {self.root}, newest first. "
                "Undo newer runs before older ones that touched the same files.")
        self.refresh()

    def refresh(self) -> None:
        """Reload the runs from disk, keeping the selected run selected."""
        keep = self._selected()
        if self.root is None:
            self._rows, self._related = [], []
        else:
            self._rows = [_row(self.root, run) for run in load_runs(self.root)]
            # Walked once here, not on every selection (the tree can be big);
            # undo_run itself always checks afresh.
            self._related = related_folders(self.root)
        self.runs_table.blockSignals(True)
        self.runs_table.clearSelection()
        self.runs_table.setRowCount(len(self._rows))
        for n, r in enumerate(self._rows):
            for col, text in enumerate((r.date, r.mode, r.files, r.status)):
                self.runs_table.setItem(n, col, QTableWidgetItem(text))
        self.runs_table.blockSignals(False)
        ids = [r.run_id for r in self._rows]
        if keep is not None and keep.run_id in ids:
            self.runs_table.selectRow(ids.index(keep.run_id))
        elif self._rows:
            self.runs_table.selectRow(0)
        self._update_undo_button()

    def _selected(self) -> _Row | None:
        rows = self.runs_table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._rows):
            return None
        return self._rows[rows[0].row()]

    def _update_undo_button(self) -> None:
        row = self._selected()
        enabled, tip = False, ""
        if row is None or self._undoing or self.root is None:
            pass
        elif row.undone:
            tip = "Already undone"
        else:
            try:
                blocking = blocking_runs(self.root, row.run_id, self._related)
            except _UNREADABLE:
                tip = "This run's undo record can't be read"
            else:
                if blocking:
                    tip = blocked_text(blocking)
                else:
                    enabled = True
        self.undo_button.setEnabled(enabled)
        self.undo_button.setToolTip(tip)
        self.blocked_label.setText("" if row is not None and row.undone else tip)
        if self._undo_key.isEnabled() != enabled:
            self._undo_key.setEnabled(enabled)
            self.keys_changed.emit()

    def _undo(self) -> None:
        row = self._selected()
        if row is None or self._undoing or self.root is None or not self.undo_button.isEnabled():
            return
        if not dialogs.ask(self, "Undo this sort?",
                           f"Put back the {row.files} files this {row.mode} sort of "
                           f"{row.date} moved?", yes="Undo", no="Cancel", default="no"):
            return
        self._undoing = True
        self._update_undo_button()
        self.result_label.setText("Putting files back…")
        task = Task(undo_with_message, self.root, row.run_id, cancellable=False)
        task.signals.result.connect(self._on_undone)
        task.signals.error.connect(self._on_undo_error)
        self._task = task  # keep it alive while it runs
        self.undo_running.emit(True)
        task.start()

    def undoing(self) -> bool:
        return self._undoing

    def _on_undone(self, result: UndoResult) -> None:
        self._undoing = False
        self.result_label.setText(undo_summary(result))
        self.refresh()
        self.undo_running.emit(False)

    def _on_undo_error(self, message: str) -> None:
        self._undoing = False
        self.result_label.setText("")
        self.refresh()
        self.undo_running.emit(False)
        self.show_error(message)
