"""The full-screen viewer for a group of repeated shots: flip, compare, zoom, keep.

Photos are decoded on pool threads (a HEIC at 2400 px takes long enough to
freeze the window); the photo after the ones on screen is loaded ahead. The
CACHE_IMAGES most recently wanted are kept; one let go is decoded again if it
comes back. Results that arrive after the dialog closed are ignored.

None of the dialog's children takes the keyboard focus, so every key press
reaches FullscreenViewer.keyPressEvent whatever was clicked last.
"""

import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from photosort.groups import Group, sharpness_label
from photosort.imageinfo import UnreadableImage, open_image
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.theme import COLORS
from photosort.ui.worker import Task

_log = logging.getLogger("photosort")

VIEW_PX = 2400  # photos are shown at most this many pixels on their longest side
CACHE_IMAGES = 8  # decoded photos kept (about 17 MB each at VIEW_PX)
ZOOM_STEP = 1.25
ZOOM_MAX = 8.0  # zoom 1.0 fits the whole photo; zooming out further shows nothing new
LOADING = "Loading…"
CANT_SHOW = "Can't show this photo"
MUST_KEEP_ONE = "At least one photo must be kept."
FLASH_MS = 2000
HINT = "← → flip · S side by side · + − 0 zoom · Space keep · Esc close"


def _render_image(index: int, path: Path, progress=None, cancel=None):
    """Return (index, QImage or CANT_SHOW), or (index, None) if cancelled first.

    Runs on a pool thread; a QImage (unlike a QPixmap) may be made here.
    """
    if cancel is not None and cancel.is_set():
        return index, None
    try:
        image = pil_to_qimage(open_image(path, VIEW_PX))
    except UnreadableImage:
        return index, CANT_SHOW
    except Exception:  # one photo must never stop the review
        _log.exception("Showing %s failed", path)
        return index, CANT_SHOW
    return index, image


class _View(QGraphicsView):
    """A view that hands key presses to the dialog and turns the wheel into zoom."""

    resized = Signal()
    wheel_zoom = Signal(int)  # +1 in, -1 out

    def keyPressEvent(self, event: QKeyEvent) -> None:
        event.ignore()  # goes on to the dialog

    def wheelEvent(self, event) -> None:
        dy = event.angleDelta().y()
        if dy:
            self.wheel_zoom.emit(1 if dy > 0 else -1)
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class FullscreenViewer(QDialog):
    """Shows one group's photos large; the owner flips through them and picks the keepers."""

    def __init__(self, root: Path, group: Group, keep: set[str],
                 parent: QWidget | None = None, start: int = 0) -> None:
        """Show group's files, those in keep kept, from file start."""
        super().__init__(parent)
        self.root = Path(root)
        self.group = group
        self._keep = set(keep)
        self.current_index = max(0, min(start, len(group.members) - 1))
        self.side_by_side = False
        self.zoom = 1.0
        # index -> photo, or CANT_SHOW; least recently wanted first
        self._images: OrderedDict[int, QPixmap | str] = OrderedDict()
        self._tasks: dict[int, Task] = {}  # kept alive until they report
        self._closed = False
        self._dispose_when_idle = False

        self.setWindowTitle("Repeated shots")
        self.setObjectName("viewer")  # see theme

        self.scene = QGraphicsScene(self)
        self.view = _View(self.scene)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setBackgroundBrush(QColor(COLORS["deep"]))
        self.view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)  # pans when zoomed
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.resized.connect(self._view_resized)
        self.view.wheel_zoom.connect(lambda step: self._zoom_by(ZOOM_STEP ** step))

        self.overlay_label = QLabel(self.view)
        self.overlay_label.move(16, 16)
        self.overlay_label.setObjectName("viewerOverlay")
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(FLASH_MS)
        self._flash_timer.timeout.connect(self._update_overlay)

        self.keep_check = QCheckBox("Keep")
        self.keep_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keep_check.toggled.connect(self._keep_check_toggled)
        hint = QLabel(HINT)
        self.done_button = QPushButton("Done")
        self.done_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.done_button.setAutoDefault(False)
        self.done_button.clicked.connect(self.accept)

        bar = QHBoxLayout()
        bar.setContentsMargins(16, 8, 16, 12)
        bar.addWidget(self.keep_check)
        bar.addSpacing(24)
        bar.addWidget(hint)
        bar.addStretch()
        bar.addWidget(self.done_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view, 1)
        layout.addLayout(bar)

        self._show_current()

    # ----- what is shown ---------------------------------------------------

    def shown_indexes(self) -> list[int]:
        """The indexes of the photos on screen: the current one, then the next if side by side."""
        n = len(self.group.members)
        nxt = (self.current_index + 1) % n
        if self.side_by_side and nxt != self.current_index:
            return [self.current_index, nxt]
        return [self.current_index]

    def loaded_indexes(self) -> set[int]:
        """The indexes whose photo (or CANT_SHOW) has arrived."""
        return set(self._images)

    def _wanted(self) -> list[int]:
        """The photos on screen, then the one after them (loaded ahead)."""
        shown = self.shown_indexes()
        return shown + [(shown[-1] + 1) % len(self.group.members)]

    def _show_current(self) -> None:
        for index in self._wanted():
            if index in self._images:
                self._images.move_to_end(index)
            else:
                self._request(index)
        self._rebuild_scene()
        self._update_overlay()

    def _rebuild_scene(self) -> None:
        """Lay the shown photos out left to right, each scaled to the first one's height."""
        self.scene.clear()
        x = 0.0
        height = None
        for index in self.shown_indexes():
            item = self._slot_item(index)
            rect = item.boundingRect()
            if height is None:
                height = rect.height()
            else:
                item.setScale(height / rect.height())
            item.setPos(x, 0)
            self.scene.addItem(item)
            x += rect.width() * item.scale() + 0.02 * rect.width()
        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        if self.zoom == 1.0:
            self._fit_view()

    def _slot_item(self, index: int) -> QGraphicsItem:
        value = self._images.get(index, LOADING)
        if isinstance(value, QPixmap):
            item = QGraphicsPixmapItem(value)
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            return item
        # A grey box the size the photo will be, with the reason it is not there.
        c = self.group.members[index]
        width, height = c.width or 4, c.height or 3
        scale = min(1.0, VIEW_PX / max(width, height))  # as open_image will shrink it
        box = QGraphicsRectItem(QRectF(0, 0, width * scale, height * scale))
        box.setBrush(QBrush(QColor(COLORS["thumb_bg"])))
        box.setPen(Qt.PenStyle.NoPen)
        text = QGraphicsSimpleTextItem(value, box)
        font = QFont()
        font.setPixelSize(round(VIEW_PX / 30))
        text.setFont(font)
        text.setBrush(QBrush(QColor(COLORS["muted"])))
        bounds = text.boundingRect()
        text.setPos((width * scale - bounds.width()) / 2, (height * scale - bounds.height()) / 2)
        return box

    def _update_overlay(self) -> None:
        self._flash_timer.stop()
        group = self.group
        c = group.members[self.current_index]
        kept = c.rel in self._keep
        self._set_overlay(
            f"{self.current_index + 1} of {len(group.members)} · {c.rel.rpartition('/')[2]}"
            f" · Sharpness: {sharpness_label(c, group)} · {'KEEP' if kept else 'not kept'}"
        )
        self.keep_check.blockSignals(True)
        self.keep_check.setChecked(kept)
        self.keep_check.blockSignals(False)

    def _flash(self, text: str) -> None:
        self._set_overlay(text)
        self._flash_timer.start()

    def _set_overlay(self, text: str) -> None:
        self.overlay_label.setText(text)
        self.overlay_label.adjustSize()
        self.overlay_label.raise_()

    # ----- loading ---------------------------------------------------------

    def _request(self, index: int) -> None:
        if self._closed or index in self._images or index in self._tasks:
            return
        task = Task(_render_image, index, self.root / self.group.members[index].rel)
        task.signals.result.connect(self._image_ready)
        self._tasks[index] = task
        task.start()

    def _image_ready(self, result: tuple[int, QImage | str | None]) -> None:
        index, value = result
        self._tasks.pop(index, None)
        if self._closed or value is None:
            self._dispose_if_idle()
            return  # asked for by a viewer that is gone
        self._images[index] = QPixmap.fromImage(value) if isinstance(value, QImage) else value
        self._evict()
        if index in self.shown_indexes():
            self._rebuild_scene()

    def _evict(self) -> None:
        """Let the least recently wanted photos go, down to CACHE_IMAGES (never a wanted one)."""
        wanted = set(self._wanted())
        for index in list(self._images):
            if len(self._images) <= CACHE_IMAGES:
                break
            if index not in wanted:
                del self._images[index]

    def dispose(self) -> None:
        """Delete this viewer, once the photos still loading have reported."""
        self._dispose_when_idle = True
        self._stop_loading()
        self._dispose_if_idle()

    def _dispose_if_idle(self) -> None:
        if self._dispose_when_idle and not self._tasks:
            self.deleteLater()

    def done(self, result: int) -> None:
        self._stop_loading()
        super().done(result)

    def _stop_loading(self) -> None:
        """Ignore the photos still loading; those waiting for a thread are not decoded."""
        self._closed = True
        for task in self._tasks.values():
            task.cancel_event.set()

    # ----- moving around ---------------------------------------------------

    def _step(self, delta: int) -> None:
        self.current_index = (self.current_index + delta) % len(self.group.members)
        self._show_current()  # a zoomed-in view stays put, to compare the same spot

    def toggle_side_by_side(self) -> None:
        self.side_by_side = not self.side_by_side
        self.zoom = 1.0
        self._show_current()

    def _zoom_by(self, factor: float) -> None:
        zoom = min(max(self.zoom * factor, 1.0), ZOOM_MAX)
        if abs(zoom - 1.0) < 1e-9:
            zoom = 1.0
        if zoom == self.zoom:
            return
        self.view.scale(zoom / self.zoom, zoom / self.zoom)
        self.zoom = zoom

    def fit(self) -> None:
        self.zoom = 1.0
        self._fit_view()

    def _fit_view(self) -> None:
        self.view.resetTransform()
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        if self.zoom != 1.0:
            self.view.scale(self.zoom, self.zoom)

    def _view_resized(self) -> None:
        self._fit_view()

    # ----- keeping ---------------------------------------------------------

    def keep_result(self) -> set[str]:
        return set(self._keep)

    def toggle_keep(self) -> None:
        self._set_kept(self.group.members[self.current_index].rel not in self._keep)

    def _keep_check_toggled(self, checked: bool) -> None:
        self._set_kept(checked)

    def _set_kept(self, kept: bool) -> None:
        rel = self.group.members[self.current_index].rel
        if not kept and self._keep == {rel}:
            self._update_overlay()  # puts the tick back
            self._flash(MUST_KEEP_ONE)
            return
        if kept:
            self._keep.add(rel)
        else:
            self._keep.discard(rel)
        self._update_overlay()

    # ----- keys ------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        text = event.text()
        if key == Qt.Key.Key_Left:
            self._step(-1)
        elif key == Qt.Key.Key_Right:
            self._step(1)
        elif key == Qt.Key.Key_S:
            self.toggle_side_by_side()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) or text in ("+", "="):
            self._zoom_by(ZOOM_STEP)
        elif key == Qt.Key.Key_Minus or text == "-":
            self._zoom_by(1 / ZOOM_STEP)
        elif key == Qt.Key.Key_0:
            self.fit()
        elif key == Qt.Key.Key_Space:
            self.toggle_keep()
        elif key == Qt.Key.Key_Escape:
            self.reject()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        else:
            super().keyPressEvent(event)
