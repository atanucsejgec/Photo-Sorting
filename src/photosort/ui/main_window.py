"""The main window: Home, Progress, Review, Face setup, Face questions, Summary
and Done pages for a sort.

A mode either plans its moves straight away, or finds groups that the owner
reviews first; the files not kept are then planned to be set aside (see
photosort.modes.MODE_SPECS). Face mode first makes sure the face models are
there, then shows the remembered people to pick from (or to add to); the
photos are then searched for those people, the owner is asked about the
unsure matches, and the moves planned. Content and Notes & Pages first make
sure the CLIP models are there, then describe every photo with CLIP and judge
it; the owner picks a folder for each unsure photo, and the moves are planned.

The window owns the one PeopleStore of the app: the Face setup page, the
naming dialog and Settings all use it.

Slow work runs in a worker.Task. Only one Task runs at a time (self._busy
guards every way of starting one); the window keeps a reference to the last
Task (self._task) so it is not garbage-collected while its thread may still be
using it, and closing the window cancels it and waits for the pool to finish.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThreadPool, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from photosort.ai.models import CLIP_MODELS
from photosort.cache import AnalysisCache
from photosort.clip import ClipEngine
from photosort.executor import ExecResult, execute
from photosort.faces import FaceEngine
from photosort.journal import RunJournal
from photosort.groups import Group, plan_set_aside
from photosort.modes import MODE_SPECS, MODES, content, notes
from photosort.modes._clip_scan import ClipScan, embed_photos
from photosort.modes.content import Verdict
from photosort.modes.face_mode import Answer, FaceScan, find_face_matches, plan_faces
from photosort.people import PeopleError, PeopleStore, Person
from photosort.plan import Plan
from photosort.settings import Settings, default_path, save_settings
from photosort.ui.category_review_page import CategoryReviewPage
from photosort.ui.face_naming_dialog import FaceNamingDialog, pick_photo
from photosort.ui.face_question_page import FaceQuestionPage
from photosort.ui.face_setup_page import FaceSetupPage
from photosort.ui.history_dialog import HistoryDialog, undo_summary
from photosort.ui.model_download import ensure_models
from photosort.ui.review_page import ReviewPage
from photosort.ui.settings_dialog import SettingsDialog
from photosort.ui.worker import Task
from photosort.undo import UndoBlocked, UndoResult, undo_run

TITLE = "Photo Sorter"
FACE_MODELS = ("face_detection", "face_recognition")


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


def _scan_faces(root: Path, include_subfolders: bool, people: list[Person],
                engine_factory: Callable[[bool], FaceEngine], use_gpu: bool,
                progress=None, cancel=None) -> FaceScan | None:
    """find_face_matches with a face engine made here, on the worker thread,
    and root's analysis cache."""
    engine = engine_factory(use_gpu)
    with AnalysisCache(root) as cache:
        return find_face_matches(root, include_subfolders, people, engine.analyse,
                                 progress=progress, cancel=cancel, cache=cache)


@dataclass
class _ClipSort:
    """A Content or Notes & Pages sort, judged and waiting for its unsure answers."""

    choices: list[str]  # the categories (or kinds of paper) to pick from
    scan: ClipScan
    sure: dict[str, str]  # rel -> category
    unsure: list[Verdict]


def _sort_by_clip(root: Path, include_subfolders: bool, mode: str, choices: list[str],
                  engine_factory: Callable[[bool], ClipEngine], use_gpu: bool,
                  progress=None, cancel=None) -> _ClipSort | None:
    """Describe every photo with a CLIP engine made here, on the worker
    thread, through root's analysis cache; then judge them for mode
    ("content", choices being the categories, or "notes")."""
    engine = engine_factory(use_gpu)
    with AnalysisCache(root) as cache:
        scan = embed_photos(root, include_subfolders, engine.image_embedding,
                            progress=progress, cancel=cancel, cache=cache)
    if scan is None:
        return None
    if not scan.embeddings:  # nothing to judge: the text model is never opened
        return _ClipSort(choices, scan, {}, [])
    text = engine.text_embeddings
    if mode == "content":
        sure, unsure = content.judge(scan, choices, text(content.prompts(choices)),
                                     text(content.background_prompts()))
    else:
        sure, unsure = notes.judge(scan, text(notes.PAPER_PROMPTS), text(notes.OTHER_PROMPTS))
    return _ClipSort(choices, scan, sure, unsure)


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
        self._face_scan: FaceScan | None = None  # kept for Back on the summary
        self._face_people: list[Person] = []  # the people the scan looked for
        # Makes the CLIP engine, given Settings' use_gpu; the models open on first use.
        self.clip_engine_factory: Callable[[bool], ClipEngine] = ClipEngine
        self._clip: _ClipSort | None = None  # kept for Back on the summary
        self._clip_answers: dict[str, str | None] = {}  # the unsure ones, as last answered
        self._task: Task | None = None
        self._busy = False  # a Task is running
        self._root: Path | None = None
        self._include_subfolders = False
        self._mode: str | None = None
        self._groups: list[Group] = []  # being reviewed; kept for Back on the summary
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
        self.face_setup_page = self._build_face_setup()
        self.face_question_page = self._build_face_questions()
        self.category_review_page = self._build_category_review()
        self.summary_page = self._build_summary()
        self.done_page = self._build_done()
        for page in (self.home, self.progress_page, self.review_page, self.face_setup_page,
                     self.face_question_page, self.category_review_page, self.summary_page,
                     self.done_page):
            self.stack.addWidget(page)
        self._show(self.home)

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
        page.finished.connect(self._on_reviewed)
        page.cancelled.connect(lambda: self._show(self.home))
        return page

    def _build_face_setup(self) -> FaceSetupPage:
        page = FaceSetupPage(self.settings)
        page.start.connect(self._start_face_sort)
        page.add_requested.connect(self._add_person)
        page.photo_dropped.connect(self._add_person)
        page.more_requested.connect(self._add_photos)
        page.selection_changed.connect(self._save_settings)
        page.back_button.clicked.connect(lambda: self._show(self.home))
        return page

    def _build_face_questions(self) -> FaceQuestionPage:
        page = FaceQuestionPage()
        page.finished.connect(self._plan_face_moves)
        page.cancelled.connect(self._show_face_setup)
        return page

    def _build_category_review(self) -> CategoryReviewPage:
        page = CategoryReviewPage()
        page.finished.connect(self._plan_clip_moves)
        page.cancelled.connect(lambda: self._show(self.home))
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
        self.home_button.clicked.connect(lambda: self._show(self.home))
        _buttons(layout, self.undo_button, self.open_folder_button, self.home_button)
        return page

    # ----- helpers ---------------------------------------------------------

    def current_page(self) -> QWidget:
        return self.stack.currentWidget()

    def _show(self, page: QWidget) -> None:
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

    def _run(self, task: Task, on_result, on_error) -> bool:
        """Start task unless one is already running; return whether it started."""
        if self._busy:
            return False
        self._busy = True
        task.signals.progress.connect(self._on_progress)
        # Connected first, so the window is free again before on_result/on_error run.
        task.signals.result.connect(self._task_finished)
        task.signals.error.connect(self._task_finished)
        task.signals.result.connect(on_result)
        task.signals.error.connect(on_error)
        self._task = task
        task.start()
        return True

    def _task_finished(self, _value: object = None) -> None:
        self._busy = False

    def _begin_progress(self, prefix: str, total: int | None = None,
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
        self._show(self.progress_page)

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
        if self._busy:
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
        if self._busy:
            return
        self._plan = None  # a stale plan must never be executed
        self._groups = []
        self._face_scan = None
        self._clip = None
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
        if spec.kind == "face" and not ensure_models(self, FACE_MODELS):
            self._show(self.home)
            return
        if spec.kind in ("content", "notes") and not ensure_models(self, CLIP_MODELS):
            self._show(self.home)
            return
        self._root = root
        self._mode = mode
        self._include_subfolders = self.subfolders_check.isChecked()
        self.settings.last_folder = str(root)
        self.settings.include_subfolders = self._include_subfolders
        self._save_settings()

        if spec.kind == "face":
            self._show_face_setup()
            return
        if spec.kind in ("content", "notes"):
            self._start_clip_sort()
            return
        self._begin_progress("Checking files…")
        on_result = self._on_plan if spec.kind == "plan" else self._on_groups
        self._run(
            Task(spec.prepare, root, self._include_subfolders), on_result, self._on_plan_error
        )

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.progress_label.setText("Stopping…")

    def _cancel_pressed(self) -> bool:
        """Whether Cancel was pressed for the last Task, which may have finished anyway."""
        return self._task is not None and self._task.cancel_event.is_set()

    # ----- review ----------------------------------------------------------

    def _on_groups(self, groups: list[Group] | None) -> None:
        if groups is None or self._cancel_pressed():
            self._show(self.home)
            return
        if not groups:
            self._show_summary([MODE_SPECS[self._mode].none_found], can_go=False)
            return
        self._groups = groups
        self.review_page.load(self._root, self._mode, groups)
        self._show(self.review_page)

    def _on_reviewed(self, keep: dict[int, set[str]]) -> None:
        folder = MODE_SPECS[self._mode].set_aside_folder
        try:
            plan = plan_set_aside(self._root, self._mode, folder, self._groups, keep)
        except OSError as e:  # e.g. the drive was unplugged during the review
            self._groups = []
            self._show(self.home)
            self.show_error(f"Could not plan the moves: {e}")
            return
        self._on_plan(plan)

    # ----- face ------------------------------------------------------------

    def _show_face_setup(self) -> None:
        self.face_setup_page.load(self.people_store)
        self._show(self.face_setup_page)

    def _name_faces(self, path: Path, name: str = "") -> tuple[list[Person], bool]:
        """Let the owner name the faces in path. Return the people saved (now
        ticked) and whether the owner finished with Save.

        The dialog has a face engine of its own, let go when it closes, so
        the models are not held in memory twice for the life of the app.
        """
        engine = self.engine_factory(self.settings.use_gpu)
        dialog = FaceNamingDialog(path, engine, self.people_store, self, name=name)
        finished = dialog.exec() == QDialog.DialogCode.Accepted
        saved = dialog.added()
        dialog.deleteLater()
        if saved:
            ids = [*self.settings.face_people, *(p.id for p in saved)]
            self.settings.face_people = list(dict.fromkeys(ids))
            self._save_settings()
        self.face_setup_page.load(self.people_store)
        return saved, finished

    def _add_person(self, path: Path | None = None) -> None:
        """Name the faces in path, or in a photo the owner picks."""
        if self._busy:
            return
        if path is None:
            path = pick_photo(self, "Choose a photo of the person")
        if path is None:
            return
        nobody_yet = not self.face_setup_page.has_people()
        saved, finished = self._name_faces(path)
        if saved and finished and nobody_yet:  # §5.2: sorting starts after naming
            self._start_face_sort(self.face_setup_page.ticked_ids())

    def _add_photos(self, person_id: str) -> None:
        if self._busy:
            return
        try:
            person = next((p for p in self.people_store.people() if p.id == person_id), None)
        except PeopleError as e:
            self.show_error(str(e))
            return
        if person is None:
            self.face_setup_page.load(self.people_store)
            return
        path = pick_photo(self, f"Choose a photo of {person.name}")
        if path is not None:
            self._name_faces(path, person.name)

    def _start_face_sort(self, person_ids: list[str]) -> None:
        if self._busy or self._root is None:
            return
        try:
            people = [p for p in self.people_store.people() if p.id in set(person_ids)]
        except PeopleError as e:
            self.show_error(str(e))
            return
        if not people:
            self.face_setup_page.load(self.people_store)
            return
        self._save_settings()  # the ticks, also when left as they first came up
        self._plan = None
        self._face_scan = None
        self._face_people = people
        self._begin_progress("Looking for faces…", joiner="of")
        self._run(
            Task(_scan_faces, self._root, self._include_subfolders, people,
                 self.engine_factory, self.settings.use_gpu),
            self._on_face_scan, self._on_face_scan_error,
        )

    def _on_face_scan(self, scan: FaceScan | None) -> None:
        if scan is None or self._cancel_pressed():
            self._show_face_setup()
            return
        self._face_scan = scan
        if scan.questions:
            self._ask_face_questions()
        else:
            self._plan_face_moves({})

    def _ask_face_questions(self) -> None:
        """Ask the owner about the unsure matches; the page's answers are then planned."""
        self.face_question_page.load(self._root, self._face_scan, self._face_people)
        self._show(self.face_question_page)

    def _plan_face_moves(self, answers: dict[tuple[str, str], Answer]) -> None:
        try:
            plan = plan_faces(self._root, self._face_scan, self._face_people, answers)
        except OSError as e:  # e.g. the drive was unplugged
            self._face_scan = None
            self._show(self.home)
            self.show_error(f"Could not plan the moves: {e}")
            return
        self._on_plan(plan)

    def _on_face_scan_error(self, message: str) -> None:
        self._show(self.home)
        self.show_error(f"Could not look for faces: {message}")

    # ----- content and notes & pages ---------------------------------------

    def _start_clip_sort(self) -> None:
        choices = (list(self.settings.content_categories) if self._mode == "content"
                   else list(notes.KINDS))
        self._clip_answers = {}
        self._begin_progress("Looking at photos…", joiner="of")
        self._run(
            Task(_sort_by_clip, self._root, self._include_subfolders, self._mode, choices,
                 self.clip_engine_factory, self.settings.use_gpu),
            self._on_clip_sort, self._on_clip_sort_error,
        )

    def _on_clip_sort(self, sort: _ClipSort | None) -> None:
        if sort is None or self._cancel_pressed():
            self._show(self.home)
            return
        self._clip = sort
        if sort.unsure:
            self._ask_clip_questions()
        else:
            self._plan_clip_moves({})

    def _ask_clip_questions(self) -> None:
        """Show the unsure photos, picked as last answered; the answers are then planned."""
        sort = self._clip
        self.category_review_page.load(self._root, sort.unsure, sort.choices,
                                       self._clip_answers)
        self._show(self.category_review_page)

    def _plan_clip_moves(self, answers: dict[str, str | None]) -> None:
        self._clip_answers = dict(answers)
        planner = content.plan_content if self._mode == "content" else notes.plan_notes
        sort = self._clip
        try:
            plan = planner(self._root, sort.scan, sort.sure, answers)
        except OSError as e:  # e.g. the drive was unplugged during the review
            self._clip = None
            self._show(self.home)
            self.show_error(f"Could not plan the moves: {e}")
            return
        self._on_plan(plan)

    def _on_clip_sort_error(self, message: str) -> None:
        self._show(self.home)
        self.show_error(f"Could not look at the photos: {message}")

    # ----- summary ---------------------------------------------------------

    def _on_plan(self, plan: Plan | None) -> None:
        if plan is None or self._cancel_pressed():
            self._show(self.home)
            return
        self._plan = plan
        lines = list(plan.summary)
        if not plan.moves:
            lines.insert(0, "Nothing to move.")
        self._show_summary(lines, can_go=bool(plan.moves))

    def _show_summary(self, lines: list[str], can_go: bool) -> None:
        self.summary_label.setText("\n".join(lines))
        self.go_button.setEnabled(can_go)
        self.show_moves_button.setEnabled(can_go)
        self.moves_table.setRowCount(0)
        self.show_moves_button.setChecked(False)
        self.moves_table.hide()
        self._show(self.summary_page)

    def _back_from_summary(self) -> None:
        """Back to the review, the face questions or the unsure photos, every
        answer intact, if there was one; to the people picked for a face
        sort; else Home."""
        if self._clip is not None and self._clip.unsure:
            self._plan = None  # the answers make a fresh one
            self._ask_clip_questions()
        elif self._groups:
            self._plan = None  # the review makes a fresh one
            self.review_page.resume()
            self._show(self.review_page)
        elif self._face_scan is not None and self._face_scan.questions:
            self._plan = None  # the answers make a fresh one
            self.face_question_page.resume()
            self._show(self.face_question_page)
        elif self._face_scan is not None:
            self._plan = None
            self._show_face_setup()
        else:
            self._show(self.home)

    def _on_plan_error(self, message: str) -> None:
        self._show(self.home)
        self.show_error(f"Could not check the files: {message}")

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
        if self._busy:
            return
        plan, root = self._plan, self._root
        if plan is None or root is None or not plan.moves:
            return
        try:
            journal = RunJournal.create(
                root, plan.mode, self._include_subfolders, planned=len(plan.moves)
            )
        except OSError as e:
            self.show_error(f"Could not start: {e}")
            return
        self._run_id = journal.run_id
        self._groups = []
        self._face_scan = None
        self._clip = None
        self._begin_progress("Moving…", len(plan.moves))
        self._run(Task(execute, root, plan, journal), self._on_executed, self._on_execute_error)

    def _show_done(self, lines: list[str], can_undo: bool) -> None:
        self.done_label.setText("\n".join(lines))
        self.undo_label.setText("")
        self.undo_button.setEnabled(can_undo)
        self.home_button.setEnabled(True)
        self._show(self.done_page)

    def _open_folder(self) -> None:
        """Show the sorted folder in the system's file manager."""
        url = QUrl.fromLocalFile(str(self._root)) if self._root is not None else None
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
        if self._busy or self._root is None or self._run_id is None:
            return
        self.undo_button.setEnabled(False)
        self.home_button.setEnabled(False)
        self.undo_label.setText("Putting files back…")
        self._run(
            Task(_undo_with_message, self._root, self._run_id, cancellable=False),
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
        self.category_review_page.stop_thumbnails()
        self.face_question_page.stop_loading()
        QThreadPool.globalInstance().waitForDone()
        super().closeEvent(event)
