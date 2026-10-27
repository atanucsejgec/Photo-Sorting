"""The question page: the unsure face matches, asked about one at a time.

Each question shows the photo with the face in question boxed, beside the
person's remembered face picture: "Is this Atanu? 63% sure". Yes adds that
person to the photo, No leaves them out, Skip leaves the whole photo where
it is (face_mode.plan_faces), so the photo's other questions are passed
over (Previous still goes back to them). Stop asking finishes with the
answers so far; the photos with a question left unanswered stay where they
are.

Photos are decoded on pool threads (a big HEIC takes long enough to freeze
the window) and arrive at FaceQuestionPage._photo_ready tagged with the
generation they were asked for; one asked for an earlier question is ignored.
They are decoded the right way up (EXIF orientation applied), as faces.py
does, so the face box (in the decoded photo's pixels, Question.image_size)
lands on the face whatever size the photo is decoded at.

Keys: Y yes, N no, Space skip; Esc goes back a question, or on the first
asks whether to leave (left()); Ctrl+Enter stops asking (to the summary).
They work wherever the focus is on the page; none of its buttons takes the
focus, so the page keeps it. A held key answers once.
"""

import logging
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from photosort.imageinfo import UnreadableImage, open_image
from photosort.modes.face_mode import Answer, FaceScan, Question
from photosort.people import Person
from photosort.thresholds import PERSON_THUMB_PX
from photosort.ui import shortcuts
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.theme import COLORS
from photosort.ui.widgets import ButtonBar
from photosort.ui.worker import Task

_log = logging.getLogger("photosort")

PHOTO_PX = 1600  # photos are shown at most this many pixels on their longest side
BOX_PEN_PX = 3
BOX_COLOUR = COLORS["face_box"]
LOADING = "Loading…"
CANT_LOAD = "Couldn't load this photo"
ANSWER_TEXT = {"yes": "Yes", "no": "No", "skip": "Skip"}


def _render_photo(generation: int, path: Path, progress=None, cancel=None):
    """Return (generation, QImage or CANT_LOAD), or (generation, None) if cancelled first.

    Runs on a pool thread; a QImage (unlike a QPixmap) may be made here.
    """
    if cancel is not None and cancel.is_set():
        return generation, None
    try:
        image = pil_to_qimage(open_image(path, PHOTO_PX))
    except UnreadableImage:
        return generation, CANT_LOAD
    except Exception:  # one photo must never stop the questions
        _log.exception("Showing %s failed", path)
        return generation, CANT_LOAD
    return generation, image


def _person_picture(person: Person | None) -> QPixmap:
    """The person's remembered face picture, or an empty pixmap if it can't be read.

    It is a small JPEG in the app's own data folder, so it is read on the GUI thread.
    """
    if person is None:
        return QPixmap()
    try:
        with Image.open(person.thumb) as img:
            img.load()
            return QPixmap.fromImage(pil_to_qimage(img))
    except Exception:  # a missing or damaged picture must never stop the questions
        _log.warning("Face picture of %s can't be shown", person.thumb, exc_info=True)
        return QPixmap()


class PhotoView(QWidget):
    """The photo, fitted to the widget and centred, with the face boxed; or a message."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._message = LOADING
        self._box = (0.0, 0.0, 0.0, 0.0)
        self._box_space = (1, 1)  # (width, height) the box's pixels are of
        self.setMinimumSize(240, 180)  # leaves room for the buttons in a small window
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the page's answer keys are here

    def show_message(self, text: str) -> None:
        self._pixmap = None
        self._message = text
        self.update()

    def show_photo(self, image: QImage, box: tuple[float, float, float, float],
                   box_space: tuple[int, int]) -> None:
        """Show image; box is in the pixels of a photo box_space in size."""
        self._pixmap = QPixmap.fromImage(image)
        self._message = ""
        self._box = box
        self._box_space = (max(1, box_space[0]), max(1, box_space[1]))
        self.update()

    def has_photo(self) -> bool:
        return self._pixmap is not None

    def message(self) -> str:
        return self._message

    def image_size(self) -> tuple[int, int] | None:
        """(width, height) of the photo shown, as decoded."""
        if self._pixmap is None:
            return None
        return self._pixmap.width(), self._pixmap.height()

    def photo_rect(self) -> QRectF:
        """Where the photo is drawn: fitted inside the widget, centred."""
        if self._pixmap is None or self._pixmap.isNull():
            return QRectF()
        w, h = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / w, self.height() / h)
        shown_w, shown_h = w * scale, h * scale
        return QRectF((self.width() - shown_w) / 2, (self.height() - shown_h) / 2,
                      shown_w, shown_h)

    def box_rect(self) -> QRectF:
        """Where the face box is drawn, in this widget's pixels."""
        photo = self.photo_rect()
        if photo.isEmpty():
            return QRectF()
        sx = photo.width() / self._box_space[0]
        sy = photo.height() / self._box_space[1]
        x1, y1, x2, y2 = self._box
        return QRectF(photo.left() + x1 * sx, photo.top() + y1 * sy,
                      (x2 - x1) * sx, (y2 - y1) * sy)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["thumb_bg"]))
        if self._pixmap is None:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.photo_rect(), self._pixmap, QRectF(self._pixmap.rect()))
            pen = QPen(QColor(BOX_COLOUR))
            pen.setWidth(BOX_PEN_PX)
            painter.setPen(pen)
            painter.drawRect(self.box_rect())
        if self.hasFocus():  # the focus ring, as the cards have
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(COLORS["focus"]), 2))
            painter.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))


class FaceQuestionPage(QWidget):
    """Asks about a FaceScan's questions one by one; emits finished(answers),
    cancelled() (Cancel) or left() (Esc on the first question, then Leave).

    answers maps (rel, person_id) to "yes", "no" or "skip", for each
    question answered (the key plan_faces expects).
    """

    finished = Signal(object)  # dict[tuple[str, str], Answer]
    cancelled = Signal()
    left = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = Path()
        self._questions: list[Question] = []
        self._people: dict[str, Person] = {}
        self._pictures: dict[str, QPixmap] = {}  # person id -> face picture
        self._answers: dict[tuple[str, str], Answer] = {}
        self._index = 0
        self._generation = 0  # bumped whenever another question is shown
        self._tasks: dict[int, Task] = {}  # kept alive until they report

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 12)
        layout.setSpacing(16)
        top = QHBoxLayout()
        self.heading_label = QLabel()
        font = self.heading_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setWeight(QFont.Weight.Bold)
        self.heading_label.setFont(font)
        self.counter_label = QLabel()
        top.addWidget(self.heading_label, 1)
        top.addWidget(self.counter_label)
        layout.addLayout(top)

        middle = QHBoxLayout()
        self.photo_view = PhotoView()
        middle.addWidget(self.photo_view, 1)
        side = QVBoxLayout()
        self.person_label = QLabel()
        self.person_label.setFixedSize(PERSON_THUMB_PX, PERSON_THUMB_PX)
        self.person_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.person_label.setObjectName("thumb")  # see theme
        self.name_label = QLabel()
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setFixedWidth(PERSON_THUMB_PX)
        side.addWidget(self.person_label)
        side.addWidget(self.name_label)
        side.addStretch()
        middle.addLayout(side)
        layout.addLayout(middle, 1)

        self.answer_label = QLabel()
        layout.addWidget(self.answer_label)

        self.back_button = QPushButton("Previous")
        self.stop_button = QPushButton("Stop asking")
        self.cancel_button = QPushButton("Cancel")
        self.skip_button = QPushButton("Skip")
        self.no_button = QPushButton("No")
        self.yes_button = QPushButton("Yes")
        for button in (self.skip_button, self.no_button, self.yes_button):
            button.setMinimumWidth(120)
        for button in (self.back_button, self.stop_button, self.cancel_button,
                       self.skip_button, self.no_button, self.yes_button):
            button.setAutoDefault(True)  # Tab to one, Enter or Space presses it
            button.installEventFilter(self)  # ... once, however long Enter is held
        layout.addWidget(ButtonBar(
            [self.back_button, self.stop_button, self.cancel_button],
            [self.skip_button, self.no_button, self.yes_button]))

        self.back_button.clicked.connect(self._previous)
        self.stop_button.clicked.connect(self._finish)
        self.cancel_button.clicked.connect(self._cancel)
        self.yes_button.clicked.connect(lambda: self._answer("yes"))
        self.no_button.clicked.connect(lambda: self._answer("no"))
        self.skip_button.clicked.connect(lambda: self._answer("skip"))

        # Keys: the photo has the focus, and Space there skips; Y and N work
        # on the buttons too (where Space presses the button), as do the
        # page's Esc and Ctrl+Enter.
        shortcuts.describe(self.photo_view, "Y", "yes")
        shortcuts.describe(self.photo_view, "N", "no")
        shortcuts.bind(self.photo_view, "Space", "Space", "skip",
                       lambda: self._answer("skip"), repeat=False)
        for keys, label, word, slot in (
                ("Y", "Y", "yes", lambda: self._answer("yes")),
                ("N", "N", "no", lambda: self._answer("no")),
                ("Escape", "Esc", "back", self._back),
                ("Ctrl+Return", "Ctrl+Enter", "stop", self._finish)):
            shortcuts.bind(self, keys, label, word, slot, repeat=False)
        shortcuts.describe_tab(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (event.type() == QEvent.Type.KeyPress and event.isAutoRepeat()
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and isinstance(watched, QPushButton)):
            return True  # a held Enter: QPushButton would press it at each repeat
        return super().eventFilter(watched, event)

    # ----- loading ---------------------------------------------------------

    def load(self, root: Path, scan: FaceScan, people: list[Person]) -> None:
        """Start asking scan's questions (at least one) about photos in root."""
        self._root = Path(root)
        self._questions = list(scan.questions)
        self._people = {p.id: p for p in people}
        self._pictures = {}
        self._answers = {}
        self._show_question(0)

    def resume(self) -> None:
        """Show again the question the asking ended on, with the answers given so far."""
        self._show_question(self._index)

    def _show_question(self, index: int) -> None:
        self.stop_loading()
        self._generation += 1
        self._index = index
        q = self._questions[index]
        self.heading_label.setText(f"Is this {q.name}? {q.percent}% sure")
        self.counter_label.setText(f"{index + 1} of {len(self._questions)}")
        self.name_label.setText(q.name)
        picture = self._picture(q.person_id)
        if picture.isNull():
            self.person_label.setText(q.name[:1])  # no picture: their initial
        else:
            self.person_label.setPixmap(picture)
        answer = self._answers.get(self._key(q))
        self.answer_label.setText(f"Your answer: {ANSWER_TEXT[answer]}" if answer else "")
        self.back_button.setEnabled(index > 0)
        self.photo_view.show_message(LOADING)
        self.photo_view.setToolTip(q.rel)
        self._request_photo(self._root / q.rel)
        self.photo_view.setFocus()

    def _picture(self, person_id: str) -> QPixmap:
        if person_id not in self._pictures:
            self._pictures[person_id] = _person_picture(self._people.get(person_id))
        return self._pictures[person_id]

    def _request_photo(self, path: Path) -> None:
        task = Task(_render_photo, self._generation, path)
        task.signals.result.connect(self._photo_ready)
        self._tasks[self._generation] = task
        task.start()

    def _photo_ready(self, result: tuple[int, QImage | str | None]) -> None:
        generation, value = result
        self._tasks.pop(generation, None)
        if generation != self._generation or value is None:
            return  # asked for a question no longer shown
        if isinstance(value, QImage):
            q = self._questions[self._index]
            self.photo_view.show_photo(value, q.box, q.image_size)
        else:
            self.photo_view.show_message(value)

    def stop_loading(self) -> None:
        """Let the photos still waiting for a pool thread finish without decoding."""
        for task in self._tasks.values():
            task.cancel_event.set()

    # ----- answers ---------------------------------------------------------

    @staticmethod
    def _key(q: Question) -> tuple[str, str]:
        return q.rel, q.person_id

    def _answer(self, answer: Answer) -> None:
        """Record answer for the current question and move on."""
        self._answers[self._key(self._questions[self._index])] = answer
        following = self._next_question(self._index)
        if following is not None:
            self._show_question(following)
        else:
            self._finish()

    def _next_question(self, index: int) -> int | None:
        """The first question after index whose photo is not skipped, if any:
        a skipped photo stays put whatever the answers to its other questions."""
        skipped = {rel for (rel, _), answer in self._answers.items() if answer == "skip"}
        for n in range(index + 1, len(self._questions)):
            if self._questions[n].rel not in skipped:
                return n
        return None

    def _previous(self) -> None:
        if self._index > 0:
            self._show_question(self._index - 1)

    def _finish(self) -> None:
        self.stop_loading()
        self.finished.emit(dict(self._answers))

    def _cancel(self) -> None:
        self.stop_loading()
        self.cancelled.emit()

    def _back(self) -> None:
        """Esc: the previous question, or from the first, leave if the owner says so."""
        if self._index > 0:
            self._previous()
        elif shortcuts.confirm_leave(self):
            self.stop_loading()
            self.left.emit()
