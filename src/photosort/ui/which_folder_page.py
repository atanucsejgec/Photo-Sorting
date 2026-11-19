"""The Which folder? page of Pick faces: the photos picked for two or more
folders, for the owner to say where each one goes.

One row per photo: the photo, its faces of those folders boxed, and a
button per folder ("1 Friend", "2 Family", …) and "0 Leave it here". Along
the top, "All to Friend", "All to Family"… and "Leave all here" answer every
row at once. A row not answered stays where it is (plan_pick leaves it, and
the summary counts it).

Photos are decoded on pool threads, tagged with the page generation and the
row they were asked for; one for a page loaded since is ignored.

Keys: ↑ and ↓ move between the rows (a GridNavigator, one column); on a row,
1–9 sends the photo to that folder and 0 leaves it here (a held key acts
once). Ctrl+Enter continues (chosen(answers)) and Esc goes back to the
people (left()), whose page is as it was. Tab reaches the All to buttons,
where ←→ move and Enter presses. Loading the page again for the same folder
keeps the answers still possible.
"""

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from photosort.faces import Face
from photosort.imageinfo import UnreadableImage, open_image
from photosort.ui import shortcuts
from photosort.ui.labels import literal
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.review_page import LOADING
from photosort.ui.shortcuts import GridNavigator
from photosort.ui.theme import COLORS, page_spacing, styled
from photosort.ui.widgets import ButtonBar, ButtonKeys
from photosort.ui.worker import Task

_log = logging.getLogger("photosort")

HEADING = "Which folder?"
SUBTITLE = ("These photos have people of more than one of your folders. Choose where each "
            "one goes; a photo you don't choose for stays where it is.")
LEAVE = "Leave it here"
LEAVE_ALL = "Leave all here"
CANT_SHOW = "Can't show"
NOT_CHOSEN = "Not chosen: stays where it is"
STAYS = "Stays where it is"

PHOTO_PX = 112  # the side of a row's photo
_RENDER_PX = 2 * PHOTO_PX  # decoded at, so it stays sharp on a high-density screen
BOX_PEN_PX = 2
MAX_KEYS = 9  # folders 1–9 have a key


def _render_photo(token: tuple[int, int], path: Path, progress=None, cancel=None):
    """Return (*token, QImage or CANT_SHOW), or (*token, None) if cancelled first.

    Runs on a pool thread; a QImage (unlike a QPixmap) may be made here. The
    photo is the right way up, as the face boxes are (faces.py decodes so).
    """
    if cancel is not None and cancel.is_set():
        return (*token, None)
    try:
        image = pil_to_qimage(open_image(path, _RENDER_PX))
    except UnreadableImage:
        return (*token, CANT_SHOW)
    except Exception:  # one photo must never stop the page
        _log.exception("Showing %s failed", path)
        return (*token, CANT_SHOW)
    return (*token, image)


class BoxedPhoto(QWidget):
    """A photo fitted in a PHOTO_PX square, with its faces boxed; or a message."""

    def __init__(self, faces: list[Face], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._faces = faces
        self._pixmap: QPixmap | None = None
        self._message = LOADING
        self.setFixedSize(PHOTO_PX, PHOTO_PX)

    def show_photo(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def show_message(self, text: str) -> None:
        self._pixmap = None
        self._message = text
        self.update()

    def has_photo(self) -> bool:
        return self._pixmap is not None

    def photo_rect(self) -> QRectF:
        if self._pixmap is None or self._pixmap.isNull():
            return QRectF()
        w, h = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / w, self.height() / h)
        return QRectF((self.width() - w * scale) / 2, (self.height() - h * scale) / 2,
                      w * scale, h * scale)

    def boxes(self) -> list[QRectF]:
        """Where the faces' boxes are drawn, in this widget's pixels."""
        photo = self.photo_rect()
        if photo.isEmpty():
            return []
        found = []
        for face in self._faces:
            sx = photo.width() / max(1, face.image_size[0])
            sy = photo.height() / max(1, face.image_size[1])
            x1, y1, x2, y2 = face.box
            found.append(QRectF(photo.left() + x1 * sx, photo.top() + y1 * sy,
                                (x2 - x1) * sx, (y2 - y1) * sy))
        return found

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["thumb_bg"]))
        if self._pixmap is None:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.photo_rect(), self._pixmap, QRectF(self._pixmap.rect()))
        pen = QPen(QColor(COLORS["face_box"]))
        pen.setWidth(BOX_PEN_PX)
        painter.setPen(pen)
        for box in self.boxes():
            painter.drawRect(box)


_DIGITS = {getattr(Qt.Key, f"Key_{n}"): n for n in range(10)}


class ConflictRow(QFrame):
    """One photo: its picture, its name, a button per folder and Leave it
    here. The row takes the focus; 1–9 and 0 answer it (answered(row))."""

    answered = Signal(object)

    def __init__(self, rel: str, folders: list[str], faces: list[Face],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rel = rel
        self.folders = folders
        self.answer: str | None = None
        self.is_answered = False
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.photo_view = BoxedPhoto(faces)
        self.name_label = QLabel(Path(rel).name)
        self.name_label.setToolTip(rel)
        self.status_label = QLabel(NOT_CHOSEN)
        self.status_label.setObjectName("muted")
        self.buttons: list[QPushButton] = []
        for n, name in enumerate(folders, start=1):
            label = f"{n} {name}" if n <= MAX_KEYS else name
            self.buttons.append(self._button(label, name))
        self.buttons.append(self._button(f"0 {LEAVE}", None))
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(self.name_label)
        text.addWidget(self.status_label)
        text.addWidget(ButtonBar(self.buttons, []))
        text.addStretch()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)
        layout.addWidget(self.photo_view, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)

    def _button(self, label: str, folder: str | None) -> QPushButton:
        button = QPushButton(literal(label))
        button.setCheckable(True)
        button.setObjectName("answerButton")  # the one chosen stands out (see theme)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the row's keys do these
        button.clicked.connect(lambda: self.set_answer(folder))
        return button

    def set_answer(self, folder: str | None) -> None:
        """Send the photo to folder (one of the row's), or leave it here (None)."""
        self.answer, self.is_answered = folder, True
        self._show()
        self.answered.emit(self)

    def _show(self) -> None:
        chosen = (self.folders.index(self.answer) if self.answer is not None
                  else len(self.folders)) if self.is_answered else None
        for n, button in enumerate(self.buttons):
            button.setChecked(n == chosen)
        self.status_label.setText(NOT_CHOSEN if not self.is_answered
                                  else STAYS if self.answer is None else f"→ {self.answer}")

    def keyPressEvent(self, event) -> None:
        n = _DIGITS.get(event.key())
        plain = (event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
                 == Qt.KeyboardModifier.NoModifier)
        if n is None or not plain:
            super().keyPressEvent(event)
            return
        event.accept()
        if event.isAutoRepeat():
            return  # a held key answers once
        if n == 0:
            self.set_answer(None)
        elif n <= min(len(self.folders), MAX_KEYS):
            self.set_answer(self.folders[n - 1])


class WhichFolderPage(QWidget):
    """The photos of two or more folders; emits chosen(dict[rel, folder or
    None]) with the rows answered, or left()."""

    chosen = Signal(object)  # dict[str, str | None]
    left = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root: Path | None = None
        self._generation = 0
        self._tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self._answers: dict[str, str | None] = {}  # kept over a reload of the same folder
        self.rows: list[ConflictRow] = []
        self.all_buttons: list[QPushButton] = []

        layout = QVBoxLayout(self)
        page_spacing(layout)
        self.heading_label = QLabel(HEADING)
        font = self.heading_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setWeight(QFont.Weight.Bold)
        self.heading_label.setFont(font)
        self.subtitle_label = QLabel(SUBTITLE)
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.heading_label)
        layout.addWidget(self.subtitle_label)
        self._all_holder = QVBoxLayout()
        self._all_holder.setContentsMargins(0, 0, 0, 0)
        self._all_bar: QWidget | None = None
        layout.addLayout(self._all_holder)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the rows take it
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigator = GridNavigator([], lambda: 1, vertical_only=True, parent=self)
        layout.addWidget(self.scroll, 1)
        self._new_list()

        self.back_button = QPushButton("Back")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        self.back_button.clicked.connect(lambda: self.left.emit())
        self.continue_button.clicked.connect(self._continue)
        layout.addWidget(ButtonBar([self.back_button], [self.continue_button]))

        shortcuts.describe(self.scroll, "↑↓", "move")
        shortcuts.describe(self.scroll, "1–9", "folder")
        shortcuts.describe(self.scroll, "0", "leave here")
        shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "continue", self._continue,
                       repeat=False)
        shortcuts.bind(self, "Escape", "Esc", "back", self.left.emit, repeat=False)
        shortcuts.describe_tab(self)

    def load(self, root: Path, conflicts: dict[str, list[str]],
             faces: dict[str, list[Face]]) -> None:
        """Show conflicts (photo -> its folders, in the folders' order), each
        photo with faces[photo] boxed. Answers given before for the same root
        are kept where still possible."""
        self.stop_thumbnails()
        self._generation += 1
        root = Path(root)
        if root != self._root:
            self._answers = {}
        self._root = root
        self._answers = {rel: answer for rel, answer in self._answers.items()
                         if rel in conflicts and (answer is None or answer in conflicts[rel])}
        self._new_list()
        self.rows = []
        for n, (rel, folders) in enumerate(sorted(conflicts.items())):
            row = ConflictRow(rel, list(folders), list(faces.get(rel, [])), self._holder)
            if rel in self._answers:
                row.set_answer(self._answers[rel])
            row.answered.connect(self._row_answered)
            self._rows_layout.insertWidget(n, row)
            self.rows.append(row)
            task = Task(_render_photo, (self._generation, n), root / rel)
            task.signals.result.connect(self._photo_ready)
            self._tasks[(self._generation, n)] = task
            task.start()
        self._new_all_bar([name for folders in conflicts.values() for name in folders])
        self.navigator.set_cards(self.rows, 0)
        if self.isVisible():
            self.navigator.focus()

    def clear(self) -> None:
        """Let go of the photos and the answers (the sort ended or was left)."""
        self.load(Path(), {}, {})
        self._root = None
        self._answers = {}

    def choices(self) -> dict[str, str | None]:
        """The rows answered: photo -> its folder, or None to leave it here."""
        return {row.rel: row.answer for row in self.rows if row.is_answered}

    def focus_first(self) -> None:
        self.navigator.focus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.navigator.focus()

    def stop_thumbnails(self) -> None:
        """Let the photos still waiting for a pool thread finish without decoding."""
        for task in self._tasks.values():
            task.cancel_event.set()

    # ----- inside ----------------------------------------------------------

    def _new_list(self) -> None:
        """An empty list of rows, in place of the one shown (which is deleted)."""
        self._holder = QWidget()
        self._rows_layout = QVBoxLayout(self._holder)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(8)
        self._rows_layout.addStretch()
        self.scroll.setWidget(self._holder)

    def _new_all_bar(self, names: list[str]) -> None:
        """The All to buttons, one per folder of the rows (in the folders' order)."""
        if self._all_bar is not None:
            self._all_holder.removeWidget(self._all_bar)
            self._all_bar.deleteLater()
            self._all_bar = None
        self.all_buttons = []
        if not self.rows:
            return
        for name in dict.fromkeys(names):
            button = QPushButton(literal(f"All to {name}"))
            button.clicked.connect(lambda _=False, folder=name: self._answer_all(folder))
            shortcuts.describe(button, "Enter", f"all to {name}")
            self.all_buttons.append(button)
        leave = QPushButton(LEAVE_ALL)
        leave.clicked.connect(lambda: self._answer_all(None))
        shortcuts.describe(leave, "Enter", "leave all here")
        self.all_buttons.append(leave)
        for button in self.all_buttons:
            button.setAutoDefault(False)
            shortcuts.describe(button, "←→", "switch buttons")
        ButtonKeys(self.all_buttons, sideways=True)
        self._all_bar = ButtonBar(self.all_buttons, [])
        self._all_holder.addWidget(self._all_bar)

    def _answer_all(self, folder: str | None) -> None:
        """Every row to folder (those it is a folder of), or every row left here."""
        for row in self.rows:
            if folder is None or folder in row.folders:
                row.set_answer(folder)

    def _row_answered(self, row: ConflictRow) -> None:
        self._answers[row.rel] = row.answer

    def _photo_ready(self, result: tuple[int, int, QImage | str | None]) -> None:
        generation, n, value = result
        self._tasks.pop((generation, n), None)
        if value is None or generation != self._generation or n >= len(self.rows):
            return
        view = self.rows[n].photo_view
        if isinstance(value, QImage):
            view.show_photo(value)
        else:
            view.show_message(value)

    def _continue(self) -> None:
        self.chosen.emit(self.choices())
