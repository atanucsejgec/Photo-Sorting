"""The main window: Home, Progress, Review, Bad photos, Events, Face setup,
Face questions, Summary and Done pages for a sort.

A sort runs as one of the flows in photosort.ui.flows, chosen by the mode's
kind (see photosort.modes.MODE_SPECS). The window is their shell: it checks
the folder and the mode, then hands over to the flow, which plans the moves
(after the owner's review, for some) and shows them on the Summary; Go then
moves them, and Undo puts them back. Back on the Summary calls the flow's
back_to, if it set one, else goes Home.

The window owns the one PeopleStore of the app: the Face setup page, the
naming dialog and Settings all use it.

Slow work runs in a worker.Task. Only one Task runs at a time (self.busy
guards every way of starting one); the window keeps a reference to the last
Task (self._task) so it is not garbage-collected while its thread may still be
using it, and closing the window cancels it and waits for the pool to finish.
"""

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThreadPool, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from photosort.clip import ClipEngine
from photosort.executor import ExecResult, execute
from photosort.faces import FaceEngine
from photosort.journal import RunJournal
from photosort.modes import MODE_SPECS, MODES
from photosort.people import PeopleStore
from photosort.plan import Plan
from photosort.settings import Settings, default_path, save_settings
from photosort.ui.bad_photos_page import BadPhotosPage
from photosort.ui.category_review_page import CategoryReviewPage
from photosort.ui.events_page import EventsPage
from photosort.ui.face_question_page import FaceQuestionPage
from photosort.ui.face_setup_page import FaceSetupPage
from photosort.ui.flows import bad, clip, events, face, groups, plain
from photosort.ui.history_dialog import HistoryDialog, undo_summary
from photosort.ui.model_download import ensure_models
from photosort.ui.review_page import ReviewPage
from photosort.ui.settings_dialog import SettingsDialog
from photosort.ui.worker import Task
from photosort.undo import UndoBlocked, UndoResult, undo_run

TITLE = "Photo Sorter"

# The flow that runs each kind of mode.
_FLOWS = {"plan": plain, "groups": groups, "bad": bad, "events": events, "face": face,
          "content": clip, "notes": clip}


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 1.6)
    font.setWeight(QFont.Weight.Bold)
    label.setFont(font)
    return label


def _text(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _page(*heading: str) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(32, 24, 32, 24)
    layout.setSpacing(16)
    for text in heading:
        layout.addWidget(_heading(text))
    return page, layout


def _buttons(layout: QVBoxLayout, *buttons: QPushButton) -> None:
    row = QHBoxLayout()
    row.addStretch()
    for button in buttons:
        button.setMinimumWidth(120)
        row.addWidget(button)
    layout.addLayout(row)


def _undo_with_message(root: Path, run_id: str, progress=None) -> UndoResult:
    """undo_run, with the owner's wording for a blocked undo."""
    try:
        return undo_run(root, run_id, progress=progress)
    except UndoBlocked as e:
        runs = ", ".join(map(str, e.blocking))  # each says which folder's history
        raise RuntimeError(f"Undo the newer run(s) first: {runs}") from e


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, settings_path: Path | None = None,
                 people_store: PeopleStore | None = None) -> None:
        super().__init__()
        self.settings = settings
        self._settings_path = default_path() if settings_path is None else Path(settings_path)
        # The app's one store of remembered people (they have no lock between stores).
        self.people_store = PeopleStore() if people_store is None else people_store
        # Makes the face engine, given Settings' use_gpu; the models open on first use.
        self.engine_factory: Callable[[bool], FaceEngine] = FaceEngine
        # Makes the CLIP engine, given Settings' use_gpu; the models open on first use.
        self.clip_engine_factory: Callable[[bool], ClipEngine] = ClipEngine
        self._task: Task | None = None
        self._on_result: Callable[[object], None] | None = None  # the Task's handlers
        self._on_error: Callable[[str], None] | None = None
        self.busy = False  # a Task is running
        # The sort being set up or run.
        self.root: Path | None = None
        self.include_subfolders = False
        self.mode: str | None = None
        self.flow_state: object = None  # the flow's working data (see photosort.ui.flows)
        self.back_to: Callable[[], None] | None = None  # where Back on the summary goes
        self._plan: Plan | None = None
        self._run_id: str | None = None
        self._progress_prefix = ""
        self._progress_joiner = "/"

        self.setWindowTitle(TITLE)
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home = self._build_home()
        self.progress_page = self._build_progress()
        self.review_page = self._build_review()
        self.bad_page = self._build_bad()
        self.events_page = self._build_events()
        self.face_setup_page = self._build_face_setup()
        self.face_question_page = self._build_face_questions()
        self.category_review_page = self._build_category_review()
        self.summary_page = self._build_summary()
        self.done_page = self._build_done()
        for page in (self.home, self.progress_page, self.review_page, self.bad_page,
                     self.events_page, self.face_setup_page, self.face_question_page,
                     self.category_review_page, self.summary_page, self.done_page):
            self.stack.addWidget(page)
        self.show_page(self.home)

    # ----- pages -----------------------------------------------------------

    def _build_home(self) -> QWidget:
        page, layout = _page(TITLE)

        layout.addWidget(_text("Choose a folder (or drop one onto this window):"))
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Folder with your photos and videos")
        self.folder_edit.setAcceptDrops(False)  # a dropped folder goes to the window
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(self.browse_button)
        layout.addLayout(row)

        layout.addWidget(_text("Sort by:"))
        self.mode_group = QButtonGroup(page)
        self.mode_buttons: dict[str, QRadioButton] = {}
        grid = QHBoxLayout()
        columns = [QVBoxLayout(), QVBoxLayout()]
        for n, (key, label, available) in enumerate(MODES):
            button = QRadioButton(label if available else f"{label} (coming soon)")
            button.setEnabled(available)
            self.mode_group.addButton(button)
            self.mode_buttons[key] = button
            columns[n * 2 // len(MODES)].addWidget(button)
        for column in columns:
            column.addStretch()
            grid.addLayout(column)
        grid.addStretch()
        layout.addLayout(grid)
        first = next(key for key, _, available in MODES if available)
        self.mode_buttons[first].setChecked(True)

        self.subfolders_check = QCheckBox("Include subfolders")
        self.subfolders_check.setChecked(self.settings.include_subfolders)
        self.subfolders_check.toggled.connect(self._subfolders_toggled)
        layout.addWidget(self.subfolders_check)

        layout.addStretch()
        self.home_status_label = _text()
        layout.addWidget(self.home_status_label)
        self.undo_history_button = QPushButton("Undo history…")
        self.settings_button = QPushButton("Settings…")
        self.start_button = QPushButton("Start")
        row = QHBoxLayout()
        row.addWidget(self.undo_history_button)
        row.addWidget(self.settings_button)
        row.addStretch()
        self.start_button.setMinimumWidth(120)
        row.addWidget(self.start_button)
        layout.addLayout(row)

        self.folder_edit.textChanged.connect(self._update_start)
        self.mode_group.buttonToggled.connect(self._update_start)
        self.start_button.clicked.connect(self._start)
        self.undo_history_button.clicked.connect(self._open_history)
        self.settings_button.clicked.connect(self._open_settings)
        last = self.settings.last_folder
        if last and Path(last).is_dir():
            self.folder_edit.setText(last)
        self._update_start()
        return page

    def _build_progress(self) -> QWidget:
        page, layout = _page("Working…")
        layout.addStretch()
        self.progress_label = _text()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        layout.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel)
        _buttons(layout, self.cancel_button)
        return page

    def _build_review(self) -> ReviewPage:
        page = ReviewPage()
        page.finished.connect(lambda keep: groups.on_reviewed(self, keep))
        page.cancelled.connect(lambda: self.show_page(self.home))
        return page

    def _build_bad(self) -> BadPhotosPage:
        page = BadPhotosPage()
        page.finished.connect(lambda move: bad.on_ticked(self, move))
        page.cancelled.connect(lambda: self.show_page(self.home))
        return page

    def _build_events(self) -> EventsPage:
        page = EventsPage()
        page.finished.connect(
            lambda named, names, skipped: events.on_named(self, named, names, skipped))
        page.cancelled.connect(lambda: self.show_page(self.home))
        return page

    def _build_face_setup(self) -> FaceSetupPage:
        page = FaceSetupPage()
        page.start.connect(lambda person_ids: face.start_sort(self, person_ids))
        page.add_requested.connect(lambda: face.add_person(self))
        page.photo_dropped.connect(lambda path: face.add_person(self, path))
        page.more_requested.connect(lambda person_id: face.add_photos(self, person_id))
        page.back_button.clicked.connect(lambda: self.show_page(self.home))
        return page

    def _build_face_questions(self) -> FaceQuestionPage:
        page = FaceQuestionPage()
        page.finished.connect(lambda answers: face.plan_moves(self, answers))
        page.cancelled.connect(lambda: face.back_to_setup(self))
        return page

    def _build_category_review(self) -> CategoryReviewPage:
        page = CategoryReviewPage()
        page.finished.connect(lambda answers: clip.plan_moves(self, answers))
        page.cancelled.connect(lambda: self.show_page(self.home))
        return page

    def _build_summary(self) -> QWidget:
        page, layout = _page("Ready to sort")
        self.summary_label = _text()
        layout.addWidget(self.summary_label)
        self.show_moves_button = QPushButton("Show all moves")
        self.show_moves_button.setCheckable(True)
        self.show_moves_button.toggled.connect(self._toggle_moves)
        row = QHBoxLayout()
        row.addWidget(self.show_moves_button)
        row.addStretch()
        layout.addLayout(row)
        self.moves_table = QTableWidget(0, 2)
        self.moves_table.setHorizontalHeaderLabels(["From", "To"])
        self.moves_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.moves_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.moves_table.hide()
        layout.addWidget(self.moves_table, 1)
        layout.addStretch()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._back_from_summary)
        self.go_button = QPushButton("Go")
        self.go_button.clicked.connect(self._go)
        _buttons(layout, self.back_button, self.go_button)
        return page

    def _build_done(self) -> QWidget:
        page, layout = _page("Done")
        self.done_label = _text()
        self.undo_label = _text()
        layout.addWidget(self.done_label)
        layout.addWidget(self.undo_label)
        layout.addStretch()
        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(self._undo)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.home_button = QPushButton("Home")
        self.home_button.clicked.connect(lambda: self.show_page(self.home))
        _buttons(layout, self.undo_button, self.open_folder_button, self.home_button)
        return page

    # ----- helpers ---------------------------------------------------------

    def current_page(self) -> QWidget:
        return self.stack.currentWidget()

    def show_page(self, page: QWidget) -> None:
        self.stack.setCurrentWidget(page)

    def show_error(self, text: str) -> None:
        QMessageBox.warning(self, TITLE, text)

    def _save_settings(self) -> None:
        try:
            save_settings(self.settings, self._settings_path)
        except OSError as e:
            self.show_error(f"Could not save settings: {e}")

    def _folder(self) -> Path | None:
        """The folder in the field, resolved (no symlinks), if it is an existing directory."""
        text = self.folder_edit.text().strip()
        if not text:
            return None
        try:
            path = Path(text).expanduser().resolve()
        except (OSError, RuntimeError):  # e.g. a symlink loop
            return None
        return path if path.is_dir() else None

    def _selected_mode(self) -> str | None:
        for key, button in self.mode_buttons.items():
            if button.isChecked():
                return key
        return None

    def run_task(self, task: Task, on_result: Callable[[object], None],
                 on_error: Callable[[str], None]) -> bool:
        """Start task unless one is already running; return whether it started.

        Its signals go to the window's own slots (so on the GUI thread), which
        free the window, then pass the result or error to on_result or on_error.
        """
        if self.busy:
            return False
        self.busy = True
        self._on_result, self._on_error = on_result, on_error
        task.signals.progress.connect(self._on_progress)
        task.signals.result.connect(self._task_result)
        task.signals.error.connect(self._task_error)
        self._task = task
        task.start()
        return True

    def _task_result(self, value: object) -> None:
        self.busy = False
        self._on_result(value)

    def _task_error(self, message: str) -> None:
        self.busy = False
        self._on_error(message)

    def begin_progress(self, prefix: str, total: int | None = None,
                       joiner: str = "/") -> None:
        """Show the progress page: "<prefix> <i> <joiner> <total>"."""
        self._progress_prefix = prefix
        self._progress_joiner = joiner
        self.progress_bar.setRange(0, 0)  # busy until the first tick
        if total is None:
            self.progress_label.setText(prefix)
        else:
            self._on_progress(0, total)
        self.cancel_button.setEnabled(True)
        self.show_page(self.progress_page)

    def _on_progress(self, i: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(i)
        self.progress_label.setText(
            f"{self._progress_prefix} {i} {self._progress_joiner} {total}")

    # ----- home ------------------------------------------------------------

    def _update_start(self, *_) -> None:
        self.home_status_label.setText("")
        self.start_button.setEnabled(
            self._folder() is not None and self._selected_mode() is not None
        )

    def _browse(self) -> None:
        start = self._folder()
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder", str(start) if start else ""
        )
        if folder:
            self.folder_edit.setText(folder)

    @staticmethod
    def _dropped_folder(event) -> str | None:
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        urls = mime.urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            return None
        path = urls[0].toLocalFile()
        return path if Path(path).is_dir() else None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.current_page() is self.home and self._dropped_folder(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if self.current_page() is self.home and self._dropped_folder(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        folder = self._dropped_folder(event)
        if self.current_page() is self.home and folder:
            self.folder_edit.setText(folder)
            event.acceptProposedAction()

    def _open_history(self) -> None:
        if self.busy:
            return
        root = self._folder()
        if root is None:
            self.home_status_label.setText("Pick a folder first.")
            return
        self.home_status_label.setText("")
        HistoryDialog(root, self).exec()

    def _open_settings(self) -> None:
        SettingsDialog(self.settings, self._settings_path, self,
                       people=self.people_store).exec()

    def _subfolders_toggled(self, checked: bool) -> None:
        self.settings.include_subfolders = checked
        self._save_settings()

    def _start(self) -> None:
        if self.busy:
            return
        self.begin_sort()
        root = self._folder()
        if root is None:
            self.show_error("This folder doesn't exist any more.")
            self._update_start()
            return
        if not os.access(root, os.W_OK):
            self.show_error("Can't write to this folder.")
            return
        mode = self._selected_mode()
        spec = MODE_SPECS.get(mode)
        if spec is None:
            self.show_error("This way of sorting isn't ready yet.")
            return
        flow = _FLOWS[spec.kind]
        if flow.MODELS and not ensure_models(self, flow.MODELS):
            self.show_page(self.home)
            return
        self.root = root
        self.mode = mode
        self.include_subfolders = self.subfolders_check.isChecked()
        self.settings.last_folder = str(root)
        self.settings.include_subfolders = self.include_subfolders
        self._save_settings()
        flow.start(self, root, self.include_subfolders, spec)

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.progress_label.setText("Stopping…")

    def cancel_pressed(self) -> bool:
        """Whether Cancel was pressed for the last Task, which may have finished anyway."""
        return self._task is not None and self._task.cancel_event.is_set()

    # ----- summary ---------------------------------------------------------

    def begin_sort(self) -> None:
        """Forget the last sort before a new one: a stale plan must never be executed."""
        self._plan = None
        self.end_flow()

    def end_flow(self) -> None:
        """Forget the flow's working data, and its way back from the summary."""
        self.flow_state = None
        self.back_to = None

    def show_plan(self, plan: Plan | None) -> None:
        """Show plan on the summary; Home if there is none or Cancel was pressed."""
        if plan is None or self.cancel_pressed():
            self.show_page(self.home)
            return
        self._plan = plan
        lines = list(plan.summary)
        if not plan.moves:
            lines.insert(0, "Nothing to move.")
        self.show_summary(lines, can_go=bool(plan.moves))

    def show_summary(self, lines: list[str], can_go: bool) -> None:
        self.summary_label.setText("\n".join(lines))
        self.go_button.setEnabled(can_go)
        self.show_moves_button.setEnabled(can_go)
        self.moves_table.setRowCount(0)
        self.show_moves_button.setChecked(False)
        self.moves_table.hide()
        self.show_page(self.summary_page)

    def _back_from_summary(self) -> None:
        """Back where the flow says (its review, every answer intact), else Home."""
        if self.back_to is None:
            self.show_page(self.home)
            return
        self._plan = None  # going back makes a fresh one
        self.back_to()

    def _toggle_moves(self, checked: bool) -> None:
        if checked and self._plan is not None and self.moves_table.rowCount() == 0:
            moves = self._plan.moves
            self.moves_table.setRowCount(len(moves))
            for row, move in enumerate(moves):
                self.moves_table.setItem(row, 0, QTableWidgetItem(move.src))
                self.moves_table.setItem(row, 1, QTableWidgetItem(move.dst))
        self.moves_table.setVisible(checked)

    # ----- moving ----------------------------------------------------------

    def _go(self) -> None:
        if self.busy:
            return
        plan, root = self._plan, self.root
        if plan is None or root is None or not plan.moves:
            return
        try:
            journal = RunJournal.create(
                root, plan.mode, self.include_subfolders, planned=len(plan.moves)
            )
        except OSError as e:
            self.show_error(f"Could not start: {e}")
            return
        self._run_id = journal.run_id
        self.end_flow()
        self.events_page.stop_thumbnails()
        self.begin_progress("Moving…", len(plan.moves))
        self.run_task(Task(execute, root, plan, journal), self._on_executed,
                      self._on_execute_error)

    def _show_done(self, lines: list[str], can_undo: bool) -> None:
        self.done_label.setText("\n".join(lines))
        self.undo_label.setText("")
        self.undo_button.setEnabled(can_undo)
        self.home_button.setEnabled(True)
        self.show_page(self.done_page)

    def _open_folder(self) -> None:
        """Show the sorted folder in the system's file manager."""
        url = QUrl.fromLocalFile(str(self.root)) if self.root is not None else None
        if url is None or not QDesktopServices.openUrl(url):
            self.show_error("Couldn't open the folder.")

    def _on_executed(self, result: ExecResult) -> None:
        lines = [f"Moved {result.moved} files."]
        if result.skipped:
            lines.append(f"Skipped {len(result.skipped)} files that were no longer there.")
        if result.cancelled:
            lines.append("Cancelled — the files moved so far can be undone.")
        self._show_done(lines, can_undo=result.moved > 0)

    def _on_execute_error(self, message: str) -> None:
        # Some files may have moved before the error; the journal has them.
        self._show_done(
            ["The sort stopped because of a problem.",
             "The files moved so far can be undone."],
            can_undo=True,
        )
        self.show_error(f"The sort stopped: {message}")

    # ----- undo ------------------------------------------------------------

    def _undo(self) -> None:
        if self.busy or self.root is None or self._run_id is None:
            return
        self.undo_button.setEnabled(False)
        self.home_button.setEnabled(False)
        self.undo_label.setText("Putting files back…")
        self.run_task(
            Task(_undo_with_message, self.root, self._run_id, cancellable=False),
            self._on_undone, self._on_undo_error,
        )

    def _on_undone(self, result: UndoResult) -> None:
        self.undo_label.setText(undo_summary(result))
        self.undo_button.setEnabled(bool(result.failed))  # left to retry
        self.home_button.setEnabled(True)

    def _on_undo_error(self, message: str) -> None:
        self.undo_label.setText("")
        self.undo_button.setEnabled(True)
        self.home_button.setEnabled(True)
        self.show_error(message)

    # ----- closing ---------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._task is not None:
            self._task.cancel_event.set()
        self.review_page.stop_thumbnails()
        self.bad_page.stop_thumbnails()
        self.events_page.stop_thumbnails()
        self.category_review_page.stop_thumbnails()
        self.face_question_page.stop_loading()
        QThreadPool.globalInstance().waitForDone()
        super().closeEvent(event)
