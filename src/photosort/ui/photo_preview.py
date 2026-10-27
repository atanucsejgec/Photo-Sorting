"""One photo shown large, fitted to the window, over the shortcut strip;
Esc closes it.

The photo is decoded on a pool thread (a HEIC at full size takes long enough
to freeze the window), at most PREVIEW_PX on its longest side. Once closed,
the dialog deletes itself as soon as that decode has reported; a photo that
arrives after it closed is ignored.
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QSizePolicy, QVBoxLayout, QWidget

from photosort.imageinfo import UnreadableImage, open_image
from photosort.thresholds import PREVIEW_PX
from photosort.ui import shortcuts
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.shortcuts import ShortcutStrip
from photosort.ui.worker import Task

_log = logging.getLogger("photosort")

LOADING = "Loading…"
CANT_SHOW = "Can't show this photo"
WINDOW_SHARE = 0.8  # the dialog opens this share of the screen wide and high


def _render(path: Path, progress=None, cancel=None) -> QImage | str | None:
    """The photo as a QImage, CANT_SHOW, or None if cancelled first (pool thread)."""
    if cancel is not None and cancel.is_set():
        return None
    try:
        return pil_to_qimage(open_image(path, PREVIEW_PX))
    except UnreadableImage:
        return CANT_SHOW
    except Exception:  # a preview must never stop the review
        _log.exception("Preview of %s failed", path)
        return CANT_SHOW


class _FittedLabel(QLabel):
    """Shows a photo as large as fits, never larger than its own size."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._photo: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # The label follows the window's size, not the photo's.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(1, 1)

    def set_photo(self, photo: QPixmap) -> None:
        self._photo = photo
        self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        photo = self._photo
        if photo is None:
            return
        if photo.width() > self.width() or photo.height() > self.height():
            photo = photo.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(photo)


class PhotoPreview(QDialog):
    """The photo at path, large. photo is the decoded photo once loaded."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.photo: QPixmap | None = None
        self._closed = False
        self._loading = True

        self.image_label = _FittedLabel(LOADING)
        self.image_label.setObjectName("thumb")  # see theme
        self.strip = ShortcutStrip(self)
        shortcuts.describe(self, "Esc", "close")  # QDialog's own
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.image_label, 1)
        strip_box = QVBoxLayout()
        strip_box.setContentsMargins(16, 0, 16, 10)
        strip_box.addWidget(self.strip)
        layout.addLayout(strip_box)
        self.strip.show_for(self)
        screen = self.screen().availableGeometry()
        self.resize(int(screen.width() * WINDOW_SHARE), int(screen.height() * WINDOW_SHARE))

        self._task = Task(_render, Path(path))  # kept alive until it reports
        self._task.signals.result.connect(self._ready)
        self._task.start()

    def _ready(self, value: QImage | str | None) -> None:
        self._loading = False
        if self._closed:
            self.deleteLater()
            return
        if isinstance(value, QImage):
            self.photo = QPixmap.fromImage(value)
            self.image_label.setText("")
            self.image_label.set_photo(self.photo)
        elif value is not None:
            self.image_label.setText(value)

    def done(self, result: int) -> None:
        """Close (Esc closes too), then delete the dialog once the photo has reported."""
        super().done(result)
        self._closed = True
        self._task.cancel_event.set()  # not decoded if still waiting for a thread
        if not self._loading:
            self.deleteLater()
