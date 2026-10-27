"""Name the faces in one photo, so Photo Sorter remembers those people.

The photo is analysed on a pool thread (the face models take a while to
open, and so does decoding a big photo); its faces and their pictures
arrive at FaceNamingDialog._analysed. Save adds each named face as a new
person, or, when the name is that of a remembered person, as another
sample of them; a face left without a name is skipped, as if "Skip this
face" were ticked, so in a group photo only the faces wanted need a name. Names are compared as they will be saved (people.clean_name,
then naming.fold), so "Zoe." is Zoe, whichever way a Bengali name is typed. A row saved once is locked, so
pressing Save again after fixing another row never saves it twice.

The dialog lets go of its face engine when it closes (an analysis still
running keeps it until it ends), so the models are not kept in memory.

Keys: Tab and Shift+Tab move between the faces' name boxes (and their Skip
ticks) and the buttons; Enter saves (a button Tab has reached is pressed
instead), once however long it is held; Esc cancels.
"""

import threading
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from photosort.faces import Face, face_crop
from photosort.imageinfo import UnreadableImage
from photosort.media import PHOTO_EXTS, kind
from photosort.naming import fold
from photosort.people import PeopleError, PeopleStore, Person, clean_name
from photosort.ui import shortcuts
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.shortcuts import ShortcutStrip
from photosort.ui.theme import heading, styled
from photosort.ui.widgets import ButtonBar, ButtonKeys
from photosort.ui.worker import Task

CROP_PX = 160
WIDTH_PX, HEIGHT_PX = 560, 520  # it opens at this size, or the screen's, if smaller
_ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
LOOKING = "Looking for faces…"
NO_FACES = "No faces found in this photo. Try another one."
CANT_OPEN = "This photo can't be opened. Try another one."
NAME_EACH = "Type a name under each face to remember. Faces left blank are skipped."
SAME_NAME = "Two faces can't have the same name."

# Analyses whose dialog may be gone before they report: a Task must outlive
# its run (worker.Task). Ones that have ended are let go when the next starts.
_analyses: list[tuple[Task, threading.Event]] = []  # (task, set once it has ended)


def _keep_alive(task: Task, ended: threading.Event) -> None:
    _analyses[:] = [(t, e) for t, e in _analyses if not e.is_set()]
    _analyses.append((task, ended))


def pick_photo(parent: QWidget | None, title: str = "Choose a photo") -> Path | None:
    """Ask for one photo file; None if the owner cancels."""
    patterns = " ".join(f"*{ext} *{ext.upper()}" for ext in sorted(PHOTO_EXTS))
    path, _ = QFileDialog.getOpenFileName(parent, title, "", f"Photos ({patterns})")
    return Path(path) if path else None


def dropped_photo(event) -> Path | None:
    """The one photo file a drag (or drop) event carries, or None."""
    mime = event.mimeData()
    if not mime.hasUrls():
        return None
    urls = mime.urls()
    if len(urls) != 1 or not urls[0].isLocalFile():
        return None
    path = Path(urls[0].toLocalFile())
    return path if kind(path) == "photo" and path.is_file() else None


def _name_key(name: str) -> str:
    """How name is compared: as PeopleStore saves it, case-insensitively; "" if
    there is no name to save."""
    if not name.replace(".", "").strip():
        return ""
    return fold(clean_name(name))


def _find_faces(engine, path: Path, ended: threading.Event, progress=None, cancel=None):
    """[(face, picture, picture as QImage)] for the faces in path, CANT_OPEN if
    it can't be read, or None if cancelled. Runs on a pool thread."""
    try:
        found = []
        for face in engine.analyse(path):
            if cancel is not None and cancel.is_set():
                return None
            crop = face_crop(path, face.box, CROP_PX)
            found.append((face, crop, pil_to_qimage(crop)))
        return found
    except UnreadableImage:
        return CANT_OPEN
    finally:
        ended.set()


class FaceRow(QWidget):
    """One face: its picture, a name to type, and Skip this face."""

    def __init__(self, face: Face, crop: Image.Image, image: QImage,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "section")
        self.face = face
        self.crop = crop
        self.saved_id: str | None = None  # the person it was saved as

        self.crop_label = QLabel()
        self.crop_label.setObjectName("thumb")  # see theme
        self.crop_label.setFixedSize(CROP_PX, CROP_PX)
        self.crop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.crop_label.setPixmap(QPixmap.fromImage(image))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name")
        self.skip_check = QCheckBox("Skip this face")
        shortcuts.describe(self.name_edit, "Enter", "save")
        shortcuts.describe(self.skip_check, "Space", "tick")
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("error")  # see theme
        self.error_label.hide()
        self.skip_check.toggled.connect(lambda skip: self.name_edit.setEnabled(not skip))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)
        layout.addWidget(self.crop_label)
        column = QVBoxLayout()
        column.setSpacing(8)
        column.addWidget(self.name_edit)
        column.addWidget(self.skip_check)
        column.addWidget(self.error_label)
        column.addStretch()
        layout.addLayout(column, 1)

    def name(self) -> str:
        return self.name_edit.text().strip()

    def pending(self) -> bool:
        """Whether Save has yet to do something with this face: it has a name
        and is not skipped or saved already."""
        return (self.saved_id is None and not self.skip_check.isChecked()
                and _name_key(self.name()) != "")

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def mark_saved(self, person_id: str) -> None:
        self.saved_id = person_id
        self.show_error("")
        self.name_edit.setEnabled(False)
        self.skip_check.setEnabled(False)


class FaceNamingDialog(QDialog):
    """Shows the faces found in a photo for the owner to name; see the module doc.

    With name, a photo with one face has that name filled in (for adding
    photos to a remembered person). added() gives the people saved.
    """

    def __init__(self, path: Path, engine, store: PeopleStore,
                 parent: QWidget | None = None, name: str = "") -> None:
        super().__init__(parent)
        self._engine = engine
        self._store = store
        self._name = name
        self._task: Task | None = None
        self._saved: list[str] = []  # ids of the people saved, in order
        self.rows: list[FaceRow] = []

        title = f"Add photos to {name}" if name else "Name the faces"
        self.setWindowTitle(title)
        self.setAcceptDrops(True)
        room = self.screen().availableGeometry()
        self.resize(min(WIDTH_PX, room.width()), min(HEIGHT_PX, room.height()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(12)
        layout.addWidget(heading(title))
        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Tab goes to the faces
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll, 1)

        self.choose_button = QPushButton("Choose photo…")
        self.cancel_button = QPushButton("Cancel")
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("primaryButton")  # see theme
        ButtonKeys([self.choose_button, self.cancel_button, self.save_button])
        self.save_button.setDefault(True)  # Enter in a name box saves
        layout.addWidget(ButtonBar([self.choose_button], [self.cancel_button, self.save_button]))
        self.strip = ShortcutStrip(self)
        layout.addWidget(self.strip)
        shortcuts.describe(self, "Esc", "cancel")
        shortcuts.describe_tab(self)
        self.strip.show_for(self)

        self.choose_button.clicked.connect(self._choose)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        self._analyse(Path(path))

    # ----- the photo -------------------------------------------------------

    def _analysing(self) -> bool:
        return self._task is not None

    def _analyse(self, path: Path) -> None:
        if self._engine is None:  # closed
            return
        self.rows = []
        self.scroll.setWidget(QWidget())  # deletes the previous photo's rows
        self.status_label.setText(LOOKING)
        self.save_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        ended = threading.Event()
        task = Task(_find_faces, self._engine, path, ended)
        task.signals.result.connect(self._analysed)
        task.signals.error.connect(self._analysis_failed)
        _keep_alive(task, ended)
        self._task = task
        task.start()

    def _analysed(self, found) -> None:
        self._task = None
        self.choose_button.setEnabled(True)
        if found is None:  # cancelled: the dialog is closing
            return
        if isinstance(found, str):
            self.status_label.setText(found)
            return
        if not found:
            self.status_label.setText(NO_FACES)
            return
        self.status_label.setText(
            f"Type {self._name} under their face." if self._name and len(found) > 1
            else NAME_EACH)
        faces = QWidget()
        column = QVBoxLayout(faces)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        for face, crop, image in found:
            row = FaceRow(face, crop, image)
            column.addWidget(row)
            self.rows.append(row)
        column.addStretch()
        self.scroll.setWidget(faces)
        if self._name and len(self.rows) == 1:
            self.rows[0].name_edit.setText(self._name)
        self.rows[0].name_edit.setFocus()
        self.save_button.setEnabled(True)

    def _analysis_failed(self, message: str) -> None:
        self._task = None
        self.choose_button.setEnabled(True)
        self.status_label.setText(f"Could not look for faces: {message}")

    def _choose(self) -> None:
        if self._analysing():
            return
        path = pick_photo(self)
        if path is not None:
            self._analyse(path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._analysing() and dropped_photo(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if not self._analysing() and dropped_photo(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        path = dropped_photo(event)
        if path is not None and not self._analysing():
            event.acceptProposedAction()
            self._analyse(path)

    # ----- saving ----------------------------------------------------------

    def _save(self) -> None:
        if self._analysing():
            return
        named = [r for r in self.rows if r.saved_id is not None or r.pending()]
        pending = [r for r in self.rows if r.pending()]
        for row in pending:
            row.show_error("")
        seen: set[str] = set()
        clash = False
        for row in named:
            key = _name_key(row.name())
            if key and key in seen:
                row.show_error(SAME_NAME)
                clash = True
            seen.add(key)
        if clash:
            return
        try:
            remembered = {_name_key(p.name): p for p in self._store.people()}
        except PeopleError as e:
            self.status_label.setText(str(e))
            return
        failed = False
        for row in pending:
            try:
                person = remembered.get(_name_key(row.name()))
                if person is not None:
                    self._store.add_samples(person.id, row.face.embedding)
                else:
                    person = self._store.add(row.name(), row.face.embedding, row.crop)
            except PeopleError as e:
                row.show_error(str(e))
                failed = True
                continue
            except OSError as e:  # say, the disk is full
                row.show_error(f"Could not save this face: {e}")
                failed = True
                continue
            row.mark_saved(person.id)
            self._saved.append(person.id)
        if not failed:
            self.accept()

    def added(self) -> list[Person]:
        """The people saved: new ones, and remembered ones given another sample."""
        try:
            by_id = {p.id: p for p in self._store.people()}
        except PeopleError:
            return []
        return [by_id[i] for i in dict.fromkeys(self._saved) if i in by_id]

    def keyPressEvent(self, event) -> None:
        if event.key() in _ENTER and event.isAutoRepeat():
            event.accept()  # a held Enter saves once
            return
        super().keyPressEvent(event)

    def done(self, result: int) -> None:
        if self._task is not None:
            self._task.cancel_event.set()
        self._engine = None  # closed: no more photos are analysed
        super().done(result)
