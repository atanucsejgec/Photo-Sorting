"""Preview's viewers, one widget per kind of file.

Every viewer is a QWidget with the same contract, so the Preview page can
swap them freely:

- load(path): show path (an earlier file is let go first);
- release(): stop playing, let go of the source and drop any picture, so
  nothing holds the file while it is moved;
- handle_key(event) -> bool: the page hands each key press here first; True
  if the viewer used it (the page's own keys, such as ←/→ or N, are never
  used: a viewer never takes a key with Ctrl, Alt or Meta held);
- keys(): the (label, word) pairs the shortcut strip lists for this kind;
- failed (Signal(str)): the file can't be shown; the page then shows the
  InfoCard with this message.

None of a viewer's children takes the keyboard focus, so every key reaches
the page. Everything that touches the disk runs on pool threads
(ui.worker.Task): decoding a photo, reading a PDF's pages and drawing them,
reading a text file, and the info card's size, date and type. A result for
a file that is no longer the one loaded is dropped (each load gets a new
generation number).

PDFs are drawn with pypdfium2 (the Linux PySide6 wheel has no QtPdf). pdfium
is not thread-safe, so every pdfium call in the process holds one lock
(_PDFIUM_LOCK). A load opens its document once, on the pool, and keeps it
for its renders; release() (or the next load) closes it on the pool, after
any render under way. A render no longer wanted is skipped before it starts;
one page can't be stopped part-way, so a page is never drawn bigger than
PDF_MAX_PIXELS.
"""

import logging
import math
import threading
import weakref
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QMimeDatabase, QRect, QRectF, QSize, Qt, QThreadPool, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QFontDatabase, QImage, QKeyEvent, QPainter, QPixmap,
    QTransform,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractSlider, QButtonGroup, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
    QStyle, QStyleOptionSlider, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from photosort.imageinfo import UnreadableImage, image_size, open_image
from photosort.ui import icons
from photosort.ui.labels import literal
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.theme import COLORS
from photosort.ui.worker import Task

try:
    import pypdfium2 as pdfium
except Exception as _exc:  # not installed, or its pdfium library won't load
    pdfium = None
    _PDFIUM_PROBLEM = f"pypdfium2 can't be loaded: {_exc}"
else:
    _PDFIUM_PROBLEM = ""

_log = logging.getLogger("photosort")

# pdfium is not thread-safe: every pdfium call in the process is made holding this.
# It is held only on pool threads, and garbage (whose finalizers may take it) is
# collected only on the GUI thread (gc_guard), so it need not be re-entrant.
_PDFIUM_LOCK = threading.Lock()

ZOOM_MIN = 0.1
ZOOM_MAX = 8.0
ZOOM_STEP = 1.25
IMAGE_MAX_PX = 4096  # a photo is first decoded at most this many pixels on its longest side
FULL_MAX_PX = 12000  # zoomed in, it is decoded again in full, at most this
PDF_MAX_DPI = 400.0
PDF_MAX_PIXELS = 40_000_000  # a page drawn bigger than this is drawn at a lower scale
PDF_SCROLL_DELAY_MS = 30  # scroll steps closer together than this draw the pages once
PDF_ZOOM_DELAY_MS = 150  # zoom steps closer together than this draw the pages once
PDF_PAGE_GAP = 12  # px between pages
PDF_MARGIN = 16  # px round the pages
MAX_TEXT_BYTES = 1024 * 1024
TEXT_FONT_MIN, TEXT_FONT_MAX = 6.0, 48.0
WRAP_MAX_LINE = 10_000  # characters; a file with a longer line is not wrapped
SEEK_MS = 10_000
SLIDER_STEP_MS = 1_000
SPEEDS = (1.0, 2.0, 4.0)

CANT_SHOW_IMAGE = "Can't show this photo"
CANT_SHOW_PDF = "Can't show this PDF"
CANT_PLAY_VIDEO = "Can't play this video"
CANT_PLAY_AUDIO = "Can't play this audio"
CANT_SHOW_TEXT = "Can't show this file"
TEXT_CUT = "\n\n… only the first 1 MB is shown"
OPEN_BUTTON = "Open with the system app (O)"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ----- text helpers (the page's top line uses them too) -------------------------------

def size_text(n: int) -> str:
    """A file size as the app writes it: "900 B", "2.0 KB", "2.3 MB", "5.0 GB"."""
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


def date_text(timestamp: float) -> str:
    """A local date as the app writes it: "14 Feb 2025"."""
    d = datetime.fromtimestamp(timestamp)
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def _clock(ms: int) -> str:
    """m:ss (minutes grow past 59)."""
    seconds = max(0, ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


# ----- keys --------------------------------------------------------------------------

_FOREIGN = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier)


def _plain(event: QKeyEvent) -> bool:
    """No Ctrl, Alt or Meta held: those keys are the page's (Ctrl+Z, Alt+↑…)."""
    return not (event.modifiers() & _FOREIGN)


def _zoom_step(event: QKeyEvent) -> int:
    """+1 for +/=, -1 for -/−, 0 for anything else."""
    key, text = event.key(), event.text()
    if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) or text in ("+", "="):
        return 1
    if key == Qt.Key.Key_Minus or text in ("-", "−"):
        return -1
    return 0


def _no_focus(*widgets: QWidget) -> None:
    for widget in widgets:
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class Viewer(QWidget):
    """The contract every viewer keeps (see the module's docstring)."""

    failed = Signal(str)
    KEYS: tuple[tuple[str, str], ...] = ()

    def load(self, path: Path) -> None:
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError

    def handle_key(self, event: QKeyEvent) -> bool:
        return False

    def keys(self) -> list[tuple[str, str]]:
        return list(self.KEYS)


class _Tasks:
    """Pool tasks kept alive until they report; each load is a new generation."""

    def __init__(self, owner: QWidget) -> None:
        self._owner = owner
        self.generation = 0
        self._live: set[Task] = set()

    def new_generation(self) -> int:
        """Start a new load: the tasks still running are told to stop."""
        self.generation += 1
        for task in self._live:
            task.cancel_event.set()
        return self.generation

    def start(self, fn, *args, on_result, on_error) -> Task:
        task = Task(fn, *args)

        # A result that arrives after the viewer was deleted goes nowhere.
        def result(value, task=task):
            self._live.discard(task)
            if isValid(self._owner):
                on_result(value)

        def error(message, task=task):
            self._live.discard(task)
            if isValid(self._owner):
                on_error(message)

        task.signals.result.connect(result)
        task.signals.error.connect(error)
        self._live.add(task)
        task.start()
        return task


# ----- image -------------------------------------------------------------------------

def _decode(generation: int, path: Path, max_px: int, progress=None, cancel=None):
    """(generation, picture, original size) on a pool thread.

    picture is a QImage at most max_px on its longest side, CANT_SHOW_IMAGE,
    or None if cancelled first. original is the photo's own (width, height),
    the right way up; percentages of zoom are of those pixels.
    """
    if cancel is not None and cancel.is_set():
        return generation, None, None
    try:
        img = open_image(path, max_px)
    except UnreadableImage:
        return generation, CANT_SHOW_IMAGE, None
    try:
        original = image_size(path)
    except UnreadableImage:
        original = img.size
    # A size that disagrees with the picture's shape is not trusted.
    if (original[0] < img.width or original[1] < img.height
            or abs(original[0] / original[1] - img.width / img.height) > 0.02):
        original = img.size
    return generation, pil_to_qimage(img), original


class _ImageView(QGraphicsView):
    """Ctrl+wheel zooms about the cursor; the plain wheel scrolls; a drag pans."""

    wheel_zoom = Signal(int)  # +1 in, -1 out
    resized = Signal()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = event.angleDelta().y()
            if dy:
                self.wheel_zoom.emit(1 if dy > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        event.ignore()  # the page's

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class ImageViewer(Viewer):
    """A photo, fitted to the window at first (never enlarged past its own
    size); zoom 10 %–800 % of the original's pixels.

    It is first decoded at most IMAGE_MAX_PX on its longest side. Zoomed
    past 100 % of that copy, the original is decoded again in full (at most
    FULL_MAX_PX) and swapped in, so zooming shows the real detail.
    """

    KEYS = (("+−", "zoom"), ("F", "fit"), ("↑↓", "scroll"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tasks = _Tasks(self)
        self._zoom = 1.0
        self._fitted = True  # follows the window's size until zoomed
        self._item: QGraphicsPixmapItem | None = None
        self._path: Path | None = None
        self._original: tuple[int, int] | None = None
        self._full_asked = False
        self.scene = QGraphicsScene(self)
        self.view = _ImageView(self.scene)
        _no_focus(self, self.view)
        self.view.setBackgroundBrush(QColor(COLORS["deep"]))
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.wheel_zoom.connect(
            lambda step: self._set_zoom(self._zoom * ZOOM_STEP ** step, under_mouse=True))
        self.view.resized.connect(self._resized)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    def load(self, path: Path) -> None:
        self.release()
        self._path = Path(path)
        generation = self._tasks.generation
        self._tasks.start(_decode, generation, self._path, IMAGE_MAX_PX,
                          on_result=self._decoded, on_error=lambda _m, g=generation:
                          self._decode_failed(g))

    def _decoded(self, result) -> None:
        generation, image, original = result
        if generation != self._tasks.generation or image is None:
            return
        if not isinstance(image, QImage):
            self.failed.emit(image)
            return
        self._original = original
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._put(image)
        self.scene.addItem(self._item)
        self.scene.setSceneRect(QRectF(0, 0, *original))
        self.fit()

    def _put(self, image: QImage) -> None:
        """Show image, scaled so the scene is in the original's pixels."""
        self._item.setPixmap(QPixmap.fromImage(image))
        self._item.setScale(self._original[0] / image.width())

    def _decode_failed(self, generation: int) -> None:
        if generation == self._tasks.generation:
            self.failed.emit(CANT_SHOW_IMAGE)

    def _want_full(self) -> None:
        """Past 100 % of the copy shown, decode the original in full, once."""
        if self._item is None or self._full_asked:
            return
        decoded = self._item.pixmap().width()
        whole = min(FULL_MAX_PX, max(self._original))
        if self._zoom * self._original[0] / decoded <= 1.0 or max(self.decoded_size()) >= whole:
            return
        self._full_asked = True
        generation = self._tasks.generation
        self._tasks.start(_decode, generation, self._path, whole,
                          on_result=self._full_decoded, on_error=lambda _m: None)

    def _full_decoded(self, result) -> None:
        generation, image, _original = result
        if generation != self._tasks.generation or not isinstance(image, QImage) \
                or self._item is None:
            return
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        self._put(image)
        self.view.centerOn(centre)

    def release(self) -> None:
        self._tasks.new_generation()
        self.scene.clear()
        self._item = None
        self._path = None
        self._original = None
        self._full_asked = False
        self._zoom = 1.0
        self._fitted = True
        self.view.resetTransform()

    def is_loaded(self) -> bool:
        return self._item is not None

    def image_size(self) -> tuple[int, int] | None:
        """The original photo's size in pixels (the right way up), or None before it arrives."""
        return self._original

    def decoded_size(self) -> tuple[int, int] | None:
        """The size of the copy on screen, or None before it arrives."""
        if self._item is None:
            return None
        pixmap = self._item.pixmap()
        return pixmap.width(), pixmap.height()

    def zoom(self) -> float:
        """Screen pixels per pixel of the original: 1.0 is 100 %."""
        return self._zoom

    def _fit_zoom(self) -> float:
        viewport = self.view.viewport()
        if self._original is None or viewport.width() <= 0 or viewport.height() <= 0:
            return 1.0
        return min(viewport.width() / self._original[0],
                   viewport.height() / self._original[1], 1.0)

    def fit(self) -> None:
        """The whole photo in view; a small one at its real size, centred."""
        self._set_zoom(self._fit_zoom())
        self._fitted = True

    def _set_zoom(self, zoom: float, under_mouse: bool = False) -> None:
        if self._item is None:
            return
        self._fitted = False
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        self.view.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse if under_mouse
            else QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.setTransform(QTransform.fromScale(zoom, zoom))
        self._zoom = zoom
        self._want_full()

    def _resized(self) -> None:
        if self._fitted:
            self.fit()

    def _scroll(self, steps: int) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.value() + steps * max(1, self.view.viewport().height() // 10))

    def handle_key(self, event: QKeyEvent) -> bool:
        if not _plain(event):
            return False
        step = _zoom_step(event)
        if step:
            self._set_zoom(self._zoom * ZOOM_STEP ** step)
            return True
        key = event.key()
        if key == Qt.Key.Key_F:
            self.fit()
            return True
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._scroll(-1 if key == Qt.Key.Key_Up else 1)
            return True
        return False


# ----- PDF ---------------------------------------------------------------------------

class PdfiumFile:
    """One load's PDF document. pdfium is not thread-safe, so every pdfium
    call (opening, reading sizes, drawing, closing) is made holding
    _PDFIUM_LOCK. The document is opened once, on the pool, kept for that
    load's renders and closed by close(), after which nothing is drawn.

    If the file is dropped without close(), it is still closed under the
    lock (on the garbage collector's thread, the GUI thread), never by
    pdfium's own finalizer without the lock.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.document = None
        self._closed = False
        self._finalizer = None

    def open(self, cancel) -> list[tuple[float, float]] | str | None:
        """[(width, height) in points, one per page, as drawn (a turned page
        has them swapped)], a str saying why the file can't be read (a
        broken or locked file: expected, so not raised), or None if
        cancelled or closed first."""
        with _PDFIUM_LOCK:
            if self._closed or (cancel is not None and cancel.is_set()):
                return None
            try:
                document = pdfium.PdfDocument(self.path)
            except Exception as exc:  # broken, locked with a password, gone
                return f"pdfium: {str(exc) or type(exc).__name__}"
            self.document = document
            self._finalizer = weakref.finalize(self, _close_document, document)
            try:
                sizes = []
                for n in range(len(document)):
                    page = document[n]
                    try:
                        sizes.append(page.get_size())
                    finally:
                        page.close()
            except Exception as exc:
                return f"pdfium: {str(exc) or type(exc).__name__}"
            return sizes or "pdfium: no pages"

    def render(self, page: int, scale: float, cancel) -> QImage | str | None:
        """Page (from 1) drawn at scale pixels per point, lowered so the
        picture is at most PDF_MAX_PIXELS; a str saying why it can't be
        drawn, or None if cancelled or closed first."""
        with _PDFIUM_LOCK:
            if self._closed or self.document is None or (cancel is not None
                                                           and cancel.is_set()):
                return None
            try:
                pdf_page = self.document[page - 1]
                try:
                    w, h = pdf_page.get_size()
                    if w * h * scale * scale > PDF_MAX_PIXELS:
                        # each side a pixel short, so rounding it up stays within the cap
                        scale = math.sqrt(PDF_MAX_PIXELS / (w * h)) - 1 / min(w, h)
                    bitmap = pdf_page.render(scale=scale)
                    try:
                        return pil_to_qimage(bitmap.to_pil())
                    finally:
                        bitmap.close()
                finally:
                    pdf_page.close()
            except Exception as exc:
                return f"pdfium, page {page}: {str(exc) or type(exc).__name__}"

    def close(self) -> None:
        """Close the document (once the render under way, if any, is done)."""
        with _PDFIUM_LOCK:
            self._closed = True
            self.document = None
            finalizer, self._finalizer = self._finalizer, None
            held = finalizer.detach() if finalizer is not None else None
            if held is not None:
                _, _, (document,), _ = held
                document.close()

    def close_on_pool(self) -> None:
        """close() on a pool thread, so the GUI thread never waits for a render."""
        self._closed = True  # nothing new starts; the lock-holding close follows
        QThreadPool.globalInstance().start(self.close)


def _close_document(document) -> None:
    """Close a document dropped without PdfiumFile.close(), holding _PDFIUM_LOCK."""
    with _PDFIUM_LOCK:
        document.close()


def _pdf_open(generation: int, file: PdfiumFile, progress=None, cancel=None):
    """(generation, pages) on a pool thread; see PdfiumFile.open."""
    return generation, file.open(cancel)


def _pdf_render(generation: int, page: int, dpi: int, file: PdfiumFile, progress=None,
                cancel=None):
    """(generation, page, dpi, picture) on a pool thread. picture is a
    QImage, a str saying why the page can't be drawn, or None if cancelled."""
    return generation, page, dpi, file.render(page, dpi / 72, cancel)


class _PdfPage(QWidget):
    """One page's box: its picture scaled to the box, drawn only where it is
    exposed. (A QLabel with scaled contents keeps a copy scaled to the whole
    box: a 5000 pt page at zoom 8 would be 40000 px square, 6.4 GB.)"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def clear(self) -> None:
        self._pixmap = None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(COLORS["thumb_bg"]))
        if self._pixmap is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(self.rect()), self._pixmap,
                               QRectF(0, 0, self._pixmap.width(), self._pixmap.height()))
        painter.end()


class _PdfScroll(QScrollArea):
    """Ctrl+wheel zooms; the plain wheel scrolls."""

    wheel_zoom = Signal(int)
    resized = Signal()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = event.angleDelta().y()
            if dy:
                self.wheel_zoom.emit(1 if dy > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        event.ignore()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class PdfViewer(Viewer):
    """A PDF's pages one under the other. Only the pages in view and the one
    after them are drawn; zoom 1.0 is 72 dpi (one point, one pixel)."""

    KEYS = (("+−", "zoom"), ("F", "fit width"), ("↑↓", "scroll"), ("PgUp/PgDn", "page"))
    page_changed = Signal(str)  # page_text(), whenever it changes

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tasks = _Tasks(self)
        self._path: Path | None = None
        self._file: PdfiumFile | None = None
        self._pages: list[tuple[float, float]] = []
        self._labels: list[_PdfPage] = []
        self._drawn: dict[int, int] = {}  # page -> dpi of the picture shown
        self._pending: dict[int, Task] = {}  # page -> its render, for the current dpi
        self._pending_dpi: dict[int, int] = {}
        self._zoom = 1.0
        self._fitted = True
        self._failed = False
        self._page_text = ""

        self.scroll = _PdfScroll()
        _no_focus(self, self.scroll)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.scroll.wheel_zoom.connect(lambda step: self._set_zoom(self._zoom * ZOOM_STEP ** step))
        self.scroll.resized.connect(self._resized)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._schedule())
        self.scroll.viewport().setAutoFillBackground(True)
        palette = self.scroll.viewport().palette()
        palette.setColor(self.scroll.viewport().backgroundRole(), QColor(COLORS["deep"]))
        self.scroll.viewport().setPalette(palette)
        self._sheet = QWidget()
        self._sheet.setAutoFillBackground(True)
        self._sheet.setPalette(palette)
        self.scroll.setWidget(self._sheet)

        self._timer = QTimer(self)  # gathers scrolls and resizes into one update
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._update_pages)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

    # ----- loading --------------------------------------------------------------

    def load(self, path: Path) -> None:
        self.release()
        self._path = Path(path)
        if pdfium is None:
            self._fail(reason=_PDFIUM_PROBLEM or "pypdfium2 can't be loaded")
            return
        self._file = PdfiumFile(self._path)  # opened on the pool, closed by release()
        generation = self._tasks.generation
        self._tasks.start(_pdf_open, generation, self._file, on_result=self._got_pages,
                          on_error=lambda m, g=generation: self._fail(g, m))

    def _fail(self, generation: int | None = None, reason: str = "") -> None:
        """Say once, for this load, that the PDF can't be shown."""
        if generation is not None and generation != self._tasks.generation:
            return
        if not self._failed:
            self._failed = True
            _log.warning("Preview can't show %s: %s", self._path, reason)
            self.failed.emit(CANT_SHOW_PDF)

    def _got_pages(self, result) -> None:
        generation, pages = result
        if generation != self._tasks.generation or pages is None:
            return
        if isinstance(pages, str):
            self._fail(generation, pages)
            return
        self._pages = pages
        for _ in pages:
            self._labels.append(_PdfPage(self._sheet))
        self.fit()

    def release(self) -> None:
        self._tasks.new_generation()
        self._timer.stop()
        if self._file is not None:
            self._file.close_on_pool()  # after a render under way, never during it
            self._file = None
        for label in self._labels:
            label.deleteLater()
        self._labels = []
        self._pages = []
        self._drawn.clear()
        self._pending.clear()
        self._pending_dpi.clear()
        self._path = None
        self._failed = False
        self._fitted = True
        self._zoom = 1.0
        self._sheet.resize(0, 0)
        self._set_page_text("")

    # ----- what is shown -------------------------------------------------------

    def page_count(self) -> int:
        return len(self._pages)

    def page_text(self) -> str:
        """"page N of M" for the page at the middle of the view; "" before the count is known."""
        return self._page_text

    def rendered_pages(self) -> set[int]:
        """The pages (from 1) with a picture on screen."""
        return set(self._drawn)

    def zoom(self) -> float:
        return self._zoom

    def dpi(self) -> float:
        """The dpi pages are drawn at: 72·zoom·devicePixelRatio, at most PDF_MAX_DPI."""
        return min(PDF_MAX_DPI, 72 * self._zoom * self.devicePixelRatioF())

    def _layout_pages(self) -> None:
        widest = max((w for w, _ in self._pages), default=0) * self._zoom
        y = PDF_MARGIN
        for (w, h), label in zip(self._pages, self._labels):
            pw, ph = max(1, round(w * self._zoom)), max(1, round(h * self._zoom))
            label.setGeometry(QRect(PDF_MARGIN + round((widest - pw) / 2), y, pw, ph))
            label.show()
            y += ph + PDF_PAGE_GAP
        self._sheet.resize(QSize(round(widest) + 2 * PDF_MARGIN,
                                 y - PDF_PAGE_GAP + PDF_MARGIN if self._pages else 0))

    def _visible(self) -> list[int]:
        """The pages (from 1) at least partly in view."""
        top = self.scroll.verticalScrollBar().value()
        bottom = top + self.scroll.viewport().height()
        return [n for n, label in enumerate(self._labels, 1)
                if label.geometry().bottom() >= top and label.geometry().top() <= bottom]

    def _schedule(self) -> None:
        """Update the pages soon (a scroll); a wait already running is kept."""
        if self._pages and not self._timer.isActive():
            self._timer.start(PDF_SCROLL_DELAY_MS)

    def _update_pages(self) -> None:
        if not self._pages or self._file is None:
            return
        visible = self._visible() or [1]
        wanted = set(visible)
        if visible[-1] < len(self._pages):
            wanted.add(visible[-1] + 1)  # one ahead
        for page in list(self._drawn):
            if page not in wanted:
                self._labels[page - 1].clear()
                del self._drawn[page]
        for page in list(self._pending):
            if page not in wanted:
                self._pending.pop(page).cancel_event.set()
                self._pending_dpi.pop(page, None)
        dpi = max(1, round(self.dpi()))
        for page in sorted(wanted):
            if self._drawn.get(page) == dpi or self._pending_dpi.get(page) == dpi:
                continue
            if page in self._pending:
                self._pending.pop(page).cancel_event.set()
            generation = self._tasks.generation
            self._pending_dpi[page] = dpi
            self._pending[page] = self._tasks.start(
                _pdf_render, generation, page, dpi, self._file, on_result=self._rendered,
                on_error=lambda m, g=generation: self._fail(g, m))
        self._update_page_text()

    def _rendered(self, result) -> None:
        generation, page, dpi, image = result
        if generation != self._tasks.generation or image is None:
            return
        if isinstance(image, str):
            self._fail(generation, image)
            return
        if self._pending_dpi.get(page) == dpi:
            self._pending.pop(page, None)
            self._pending_dpi.pop(page, None)
        elif page not in self._pending:
            return  # no longer wanted
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self._labels[page - 1].setPixmap(pixmap)
        self._drawn[page] = dpi

    def _update_page_text(self) -> None:
        if not self._pages:
            return
        middle = (self.scroll.verticalScrollBar().value()
                  + self.scroll.viewport().height() // 2)
        current = len(self._pages)
        for n, label in enumerate(self._labels, 1):
            if middle <= label.geometry().bottom() + PDF_PAGE_GAP:
                current = n
                break
        self._set_page_text(f"page {current} of {len(self._pages)}")

    def _set_page_text(self, text: str) -> None:
        if text != self._page_text:
            self._page_text = text
            self.page_changed.emit(text)

    # ----- zoom and scroll -----------------------------------------------------

    def _fit_zoom(self) -> float:
        widest = max((w for w, _ in self._pages), default=0)
        room = self.scroll.viewport().width() - 2 * PDF_MARGIN
        if widest <= 0 or room <= 0:
            return 1.0
        return min(max(room / widest, ZOOM_MIN), ZOOM_MAX)

    def fit(self) -> None:
        """Fit the widest page to the view's width."""
        self._set_zoom(self._fit_zoom())
        self._fitted = True

    def _set_zoom(self, zoom: float) -> None:
        if not self._pages:
            return
        self._fitted = False
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        bar = self.scroll.verticalScrollBar()
        half = self.scroll.viewport().height() / 2
        old_height = self._sheet.height()
        middle = (bar.value() + half) / old_height if old_height > 0 else None
        self._zoom = zoom
        self._layout_pages()
        # the same spot stays in the middle of the view; a new file starts at its top
        bar.setValue(0 if middle is None else round(middle * self._sheet.height() - half))
        self._update_page_text()
        # Pages are drawn once the zooming stops: each step restarts the wait.
        self._timer.start(PDF_ZOOM_DELAY_MS)

    def _resized(self) -> None:
        if self._fitted:
            self.fit()
        else:
            self._schedule()

    def handle_key(self, event: QKeyEvent) -> bool:
        if not _plain(event):
            return False
        step = _zoom_step(event)
        if step:
            self._set_zoom(self._zoom * ZOOM_STEP ** step)
            return True
        key = event.key()
        bar = self.scroll.verticalScrollBar()
        page = max(1, round(self.scroll.viewport().height() * 0.9))
        moves = {Qt.Key.Key_Up: -bar.singleStep() * 3, Qt.Key.Key_Down: bar.singleStep() * 3,
                 Qt.Key.Key_PageUp: -page, Qt.Key.Key_PageDown: page}
        if key == Qt.Key.Key_F:
            self.fit()
        elif key in moves:
            bar.setValue(bar.value() + moves[key])
            self._update_page_text()
        else:
            return False
        return True


# ----- video and audio ---------------------------------------------------------------

class _SeekSlider(QSlider):
    """A position slider where a left click on the track jumps there (the
    Fusion style would only take a page step) and the handle can then be
    dragged on from the same press."""

    jumped = Signal(int)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            style = self.style()
            handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                          QStyle.SubControl.SC_SliderHandle, self)
            point = event.position().toPoint()
            if not handle.contains(point):
                groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                              QStyle.SubControl.SC_SliderGroove, self)
                span = max(1, groove.width() - handle.width())
                x = point.x() - groove.x() - handle.width() // 2
                value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                                       min(max(x, 0), span), span,
                                                       option.upsideDown)
                self.setValue(value)
                self.jumped.emit(value)
        super().mousePressEvent(event)  # the handle is under the pointer now: a drag starts


class MediaViewer(Viewer):
    """A video (video=True) or a sound, paused at the start: it never plays by
    itself. The speed (1×, 2×, 4×) is kept from one file to the next."""

    KEYS = (("Space", "play/pause"), ("Shift+←→", "±10 s"), ("S", "speed"))
    speed = 1.0  # class-wide: every MediaViewer plays at the speed last set

    def __init__(self, video: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.video = video
        self._loaded = False  # the source is loading or loaded (not released)
        self._failed = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.player.errorOccurred.connect(self._error)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)

        self.video_widget: QVideoWidget | None = None
        self.name_label: QLabel | None = None
        if video:
            self.video_widget = QVideoWidget()
            self.player.setVideoOutput(self.video_widget)
            picture: QWidget = self.video_widget
        else:
            picture = QWidget()
            icon_label = QLabel()
            icon_label.setPixmap(icons.icon("music", "muted").pixmap(128, 128))
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.name_label = QLabel()
            self.name_label.setTextFormat(Qt.TextFormat.PlainText)
            self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.name_label.setWordWrap(True)
            column = QVBoxLayout(picture)
            column.addStretch(1)
            column.addWidget(icon_label)
            column.addWidget(self.name_label)
            column.addStretch(1)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.play_pause)
        self.slider = _SeekSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setSingleStep(SLIDER_STEP_MS)  # the wheel over it
        self.slider.setPageStep(SEEK_MS)
        self.slider.jumped.connect(self.player.setPosition)
        self.slider.actionTriggered.connect(self._slider_action)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel(self.time_text())
        self.speed_buttons: list[QPushButton] = []
        self._speed_group = QButtonGroup(self)
        self._speed_group.setExclusive(True)
        for rate in SPEEDS:
            button = QPushButton(f"{rate:g}×")
            button.setObjectName("answerButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, r=rate: self.set_speed(r))
            self._speed_group.addButton(button)
            self.speed_buttons.append(button)
        _no_focus(self, self.play_button, self.slider, *self.speed_buttons, picture)

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 6, 12, 6)
        bar.addWidget(self.play_button)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_label)
        for button in self.speed_buttons:
            bar.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(picture, 1)
        layout.addLayout(bar)
        self._show_speed()

    def load(self, path: Path) -> None:
        self.release()
        path = Path(path)
        if self.name_label is not None:
            self.name_label.setText(path.name)
        self._loaded = True
        self.player.setPlaybackRate(MediaViewer.speed)
        self._show_speed()
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def release(self) -> None:
        self._loaded = False
        self._failed = False
        self.player.stop()
        self.player.setSource(QUrl())
        self.slider.setRange(0, 0)
        self.time_label.setText(self.time_text())
        if self.name_label is not None:
            self.name_label.clear()

    def _status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if not self._loaded:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia \
                and self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self.player.pause()  # shows the first frame, without playing
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._fail()

    def _error(self, error: QMediaPlayer.Error, message: str) -> None:
        if self._loaded and error != QMediaPlayer.Error.NoError:
            _log.info("Preview can't play %s: %s", self.player.source().toLocalFile(), message)
            self._fail()

    def _fail(self) -> None:
        if not self._failed:
            self._failed = True
            self.failed.emit(CANT_PLAY_VIDEO if self.video else CANT_PLAY_AUDIO)

    def play_pause(self) -> None:
        if not self._loaded:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek(self, delta_ms: int) -> None:
        """Jump delta_ms forward (or back), staying within the file."""
        duration = self.player.duration()
        if not self._loaded or duration <= 0:
            return
        self.player.setPosition(min(max(self.player.position() + delta_ms, 0), duration))

    def set_speed(self, rate: float) -> None:
        if rate not in SPEEDS:
            raise ValueError(f"speed {rate} is not one of {SPEEDS}")
        MediaViewer.speed = rate
        self.player.setPlaybackRate(rate)
        self._show_speed()

    def cycle_speed(self) -> None:
        """1× → 2× → 4× → 1×."""
        index = SPEEDS.index(MediaViewer.speed) if MediaViewer.speed in SPEEDS else -1
        self.set_speed(SPEEDS[(index + 1) % len(SPEEDS)])

    def _show_speed(self) -> None:
        for rate, button in zip(SPEEDS, self.speed_buttons):
            button.setChecked(rate == MediaViewer.speed)

    def time_text(self) -> str:
        """"m:ss / m:ss": where it is, and how long it is."""
        return f"{_clock(self.player.position())} / {_clock(self.player.duration())}"

    def _position_changed(self, position: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.time_label.setText(self.time_text())

    def _duration_changed(self, duration: int) -> None:
        self.slider.setRange(0, max(0, duration))
        self.time_label.setText(self.time_text())

    def _slider_action(self, action: int) -> None:
        """A wheel step over the slider moves the player too."""
        if action in (QAbstractSlider.SliderAction.SliderPageStepAdd,
                      QAbstractSlider.SliderAction.SliderPageStepSub,
                      QAbstractSlider.SliderAction.SliderSingleStepAdd,
                      QAbstractSlider.SliderAction.SliderSingleStepSub):
            self.player.setPosition(self.slider.sliderPosition())

    def _state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause" if playing else "Play")

    def handle_key(self, event: QKeyEvent) -> bool:
        if not _plain(event):
            return False
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_Space and not shift:
            self.play_pause()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and shift:
            self.seek(SEEK_MS if key == Qt.Key.Key_Right else -SEEK_MS)
        elif key == Qt.Key.Key_S and not shift:
            self.cycle_speed()
        else:
            return False
        return True


# ----- text --------------------------------------------------------------------------

def _read_text(generation: int, path: Path, progress=None, cancel=None):
    """(generation, (text, wrap)), or (generation, None) if it can't be read.
    On a pool thread. At most MAX_TEXT_BYTES are read, as UTF-8 with
    replacement characters."""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_TEXT_BYTES + 1)
    except OSError as exc:
        _log.info("Preview can't read %s: %s", path, exc)
        return generation, None
    text = data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    if len(data) > MAX_TEXT_BYTES:
        text += TEXT_CUT
    # Wrapping one very long line (minified JSON, say) takes Qt most of a
    # minute for 1 MB; such a file is shown unwrapped instead.
    longest = max(map(len, text.splitlines()), default=0)
    return generation, (text, longest <= WRAP_MAX_LINE)


class TextViewer(Viewer):
    """A text file, read-only and monospaced; at most MAX_TEXT_BYTES are read
    (on the pool)."""

    KEYS = (("+−", "text size"), ("↑↓", "scroll"), ("PgUp/PgDn", "page"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tasks = _Tasks(self)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFrameShape(QFrame.Shape.NoFrame)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSizeF(max(self.font().pointSizeF(), TEXT_FONT_MIN))
        self.edit.setFont(font)
        _no_focus(self, self.edit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)

    def load(self, path: Path) -> None:
        self.release()
        self._tasks.start(_read_text, self._tasks.generation, Path(path),
                          on_result=self._read, on_error=lambda _m: None)

    def _read(self, result) -> None:
        generation, read = result
        if generation != self._tasks.generation:
            return
        if read is None:
            self.failed.emit(CANT_SHOW_TEXT)
            return
        text, wrap = read
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if wrap
                                  else QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setPlainText(text)

    def release(self) -> None:
        self._tasks.new_generation()
        self.edit.clear()

    def _font_by(self, step: int) -> None:
        font = self.edit.font()
        size = min(max(font.pointSizeF() + step, TEXT_FONT_MIN), TEXT_FONT_MAX)
        font.setPointSizeF(size)
        self.edit.setFont(font)

    def handle_key(self, event: QKeyEvent) -> bool:
        if not _plain(event):
            return False
        step = _zoom_step(event)
        if step:
            self._font_by(step)
            return True
        bar = self.edit.verticalScrollBar()
        moves = {Qt.Key.Key_Up: -bar.singleStep(), Qt.Key.Key_Down: bar.singleStep(),
                 Qt.Key.Key_PageUp: -bar.pageStep(), Qt.Key.Key_PageDown: bar.pageStep()}
        if event.key() in moves:
            bar.setValue(bar.value() + moves[event.key()])
            return True
        return False


# ----- anything else -----------------------------------------------------------------

def _file_facts(generation: int, path: Path, progress=None, cancel=None):
    """(generation, type, size or None, modified time or None). On a pool thread."""
    kind = QMimeDatabase().mimeTypeForFile(str(path)).comment()
    if not kind:
        kind = f"{path.suffix[1:].upper()} file" if path.suffix else "File"
    try:
        stat = path.stat()
    except OSError:
        return generation, kind, None, None
    return generation, kind, stat.st_size, stat.st_mtime


class InfoCard(Viewer):
    """A file's name, type, size, modified date and full path, and a button
    that opens it in the system's app. set_message() shows why it is here
    ("Can't show this PDF"); load() clears that message."""

    KEYS = (("O", "open with the system app"),)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tasks = _Tasks(self)
        self._path: Path | None = None
        self.message_label = QLabel()
        self.message_label.setObjectName("error")
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.hide()
        self.name_label = QLabel()
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.name_label.setWordWrap(True)
        font = self.name_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.5)
        font.setWeight(QFont.Weight.Bold)
        self.name_label.setFont(font)
        self._values: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        for row, title in enumerate(("Type", "Size", "Modified", "Path")):
            key = QLabel(title)
            key.setObjectName("muted")
            value = QLabel()
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self._values[title] = value
        grid.setColumnStretch(1, 1)
        self.open_button = QPushButton(literal(OPEN_BUTTON))
        self.open_button.clicked.connect(self.open_externally)
        _no_focus(self, self.open_button)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)
        layout.addStretch(1)
        layout.addWidget(self.message_label)
        layout.addWidget(self.name_label)
        layout.addLayout(grid)
        layout.addLayout(buttons)
        layout.addStretch(2)

    def load(self, path: Path) -> None:
        self.release()
        path = Path(path)
        self._path = path
        self.name_label.setText(path.name)
        self._values["Path"].setText(str(path))
        self._tasks.start(_file_facts, self._tasks.generation, path,
                          on_result=self._got_facts, on_error=lambda _m: None)

    def _got_facts(self, result) -> None:
        generation, kind, size, modified = result
        if generation != self._tasks.generation:
            return
        self._values["Type"].setText(kind)
        self._values["Size"].setText("—" if size is None else size_text(size))
        self._values["Modified"].setText(
            "—" if modified is None
            else f"{date_text(modified)} {datetime.fromtimestamp(modified):%H:%M}")

    def release(self) -> None:
        self._tasks.new_generation()
        self._path = None
        self.set_message("")
        self.name_label.clear()
        for value in self._values.values():
            value.clear()

    def set_message(self, text: str) -> None:
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def text(self) -> str:
        """Everything the card says, one line each (for tests and screen readers)."""
        lines = [self.message_label.text(), self.name_label.text()]
        lines += [f"{title}: {value.text()}" for title, value in self._values.items()]
        return "\n".join(line for line in lines if line)

    def open_externally(self) -> None:
        if self._path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path)))

    def handle_key(self, event: QKeyEvent) -> bool:
        if _plain(event) and event.key() == Qt.Key.Key_O \
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.open_externally()
            return True
        return False
