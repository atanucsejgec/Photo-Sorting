"""Undo history: the folder's past runs, newest first, each one undoable.

Undo runs in a worker.Task. Only one undo runs at a time, and closing the
dialog waits for a running undo to finish (an undo can't stop part-way).
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from photosort.journal import RunInfo, load_moves, load_runs
from photosort.modes import MODES
from photosort.ui.worker import Task
from photosort.undo import UndoResult, blocking_runs, undo_run

_MODE_LABELS = {key: label for key, label, _ in MODES}
_STATUS_LABELS = {"done": "Done", "cancelled": "Cancelled", "failed": "Failed"}
_UNREADABLE = (OSError, ValueError, KeyError, TypeError)


def undo_summary(result: UndoResult) -> str:
    """What an undo did, in the words the Done page and this dialog both use."""
    lines = [f"Undo complete: {result.restored} files put back."]
    if result.renamed:
        lines.append(f"{len(result.renamed)} files were put back under a new name "
                     "because the original name was taken.")
    if result.missing:
        lines.append(f"{len(result.missing)} files could not be found.")
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
        mode=_MODE_LABELS.get(run.mode, run.mode),
        files=files,
        status=status,
        undone=run.undone,
    )


class HistoryDialog(QDialog):
    def __init__(self, root: Path, parent=None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self._rows: list[_Row] = []
        self._task: Task | None = None
        self._undoing = False

        self.setWindowTitle("Undo history")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(f"Past sorts in {self.root}, newest first. "
                       "Undo newer runs before older ones that touched the same files.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.runs_table = QTableWidget(0, 4)
        self.runs_table.setHorizontalHeaderLabels(["Date", "Mode", "Files", "Status"])
        self.runs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.runs_table.verticalHeader().hide()
        self.runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.runs_table.itemSelectionChanged.connect(self._update_undo_button)
        layout.addWidget(self.runs_table, 1)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        row = QHBoxLayout()
        self.undo_button = QPushButton("Undo")
        self.undo_button.setMinimumWidth(120)
        self.undo_button.clicked.connect(self._undo)
        row.addWidget(self.undo_button)
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)

        self.refresh()

    def show_error(self, text: str) -> None:
        QMessageBox.warning(self, "Undo history", text)

    def refresh(self) -> None:
        """Reload the runs from disk, keeping the selected run selected."""
        keep = self._selected()
        self._rows = [_row(self.root, run) for run in load_runs(self.root)]
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
        if row is None or self._undoing:
            pass
        elif row.undone:
            tip = "Already undone"
        else:
            try:
                blocked = bool(blocking_runs(self.root, row.run_id))
            except _UNREADABLE:
                tip = "This run's undo record can't be read"
            else:
                if blocked:
                    tip = "Undo newer runs first"
                else:
                    enabled = True
        self.undo_button.setEnabled(enabled)
        self.undo_button.setToolTip(tip)

    def _undo(self) -> None:
        row = self._selected()
        if row is None or self._undoing:
            return
        self._undoing = True
        self._update_undo_button()
        self.result_label.setText("Putting files back…")
        task = Task(undo_run, self.root, row.run_id, cancellable=False)
        task.signals.result.connect(self._on_undone)
        task.signals.error.connect(self._on_undo_error)
        self._task = task  # keep it alive while it runs
        task.start()

    def _on_undone(self, result: UndoResult) -> None:
        self._undoing = False
        self.result_label.setText(undo_summary(result))
        self.refresh()

    def _on_undo_error(self, message: str) -> None:
        self._undoing = False
        self.result_label.setText("")
        self.refresh()
        self.show_error(f"Could not undo: {message}")

    def done(self, r: int) -> None:
        if self._undoing:
            # An undo can't stop part-way; let it finish before closing.
            QThreadPool.globalInstance().waitForDone()
        super().done(r)
