"""Ask once, then download the missing model files with a progress dialog.

The question is a themed dialogs.ask with "Not now" focused: a big download
wants a deliberate yes, so Enter alone answers no. The downloads run one
after another as worker Tasks; ensure_models waits in a local event loop,
so the window keeps painting while it does. Their dialog (DownloadDialog)
has Cancel, which Esc presses too; Enter does nothing there, so an Enter
held since the question can't cancel. The words used name what is
downloaded: the CLIP model, when only its files are asked for (with the
size of those missing), else the face models.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QEventLoop, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from photosort.ai import models
from photosort.ui import dialogs, shortcuts
from photosort.ui.shortcuts import ShortcutStrip
from photosort.ui.theme import heading
from photosort.ui.widgets import ButtonBar
from photosort.ui.worker import Task

QUESTION = "Photo Sorter needs to download the face models once (180 MB). Download now?"
TITLE = "Face models"


@dataclass(frozen=True)
class _Wording:
    title: str
    question: str  # {mb}: the missing files' size, in MB
    downloading: str
    failed: str  # ": <the error>" follows


_FACE = _Wording(TITLE, QUESTION, "Downloading the face models…",
                 "Could not download the face models")
_CLIP = _Wording(
    "Picture model",
    "Photo Sorter needs to download the picture-understanding model once ({mb} MB). "
    "Photos stay on this computer; only the model is downloaded. Download it now?",
    "Downloading the picture-understanding model…",
    "Could not download the picture-understanding model",
)


class DownloadDialog(QDialog):
    """The downloads' progress: text over a bar, with Cancel (and Esc),
    which emits canceled once; the dialog stays until its owner hides it."""

    canceled = Signal()

    def __init__(self, title: str, text: str, maximum: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, maximum)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumWidth(100)
        self.cancel_button.setAutoDefault(False)  # Enter never cancels a download
        self.cancel_button.clicked.connect(self.reject)
        self.strip = ShortcutStrip(self)
        shortcuts.describe(self, "Esc", "cancel")  # QDialog's own
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(14)
        layout.addWidget(heading(title))
        layout.addWidget(self._label)
        layout.addWidget(self.bar)
        layout.addWidget(ButtonBar([], [self.cancel_button]))
        layout.addWidget(self.strip)
        self.cancel_button.setFocus()
        self.strip.show_for(self)

    def labelText(self) -> str:  # as QProgressDialog names it
        return self._label.text()

    def setValue(self, value: int) -> None:
        self.bar.setValue(value)

    def reject(self) -> None:
        """Cancel or Esc: say so once; the owner closes the dialog when done."""
        if self.cancel_button.isEnabled():
            self.cancel_button.setEnabled(False)
            self.canceled.emit()


def _wording(keys: list[str]) -> _Wording:
    return _CLIP if keys and set(keys) <= set(models.CLIP_MODELS) else _FACE


def show_error(parent: QWidget | None, text: str, title: str = TITLE) -> None:
    dialogs.warn(parent, title, text)


def ensure_models(parent: QWidget | None, keys: Iterable[str]) -> bool:
    """True when every model in keys is present, downloading after a yes."""
    keys = list(keys)
    todo = models.missing(keys)
    if not todo:
        return True
    wording = _wording(keys)
    mb = round(sum(models.MODELS[k].size for k in todo) / 1_000_000)
    # A big download wants a deliberate yes: Enter alone answers no.
    if not dialogs.ask(parent, wording.title, wording.question.format(mb=mb),
                       yes="Download", no="Not now", default="no"):
        return False
    _Downloads(parent, todo, wording).run()
    return not models.missing(keys)


class _Downloads(QObject):
    def __init__(self, parent: QWidget | None, keys: list[str], wording: _Wording) -> None:
        super().__init__()
        self._parent = parent
        self._wording = wording
        self._queue = list(keys)
        self._total = sum(models.MODELS[k].size for k in keys)
        self._finished = 0  # bytes of the models already downloaded
        self._key: str | None = None
        self._task: Task | None = None
        self._cancelled = False
        self._error_text: str | None = None
        self._loop = QEventLoop()
        # Progress counts KiB so the total fits in the bar's int.
        self.dialog = DownloadDialog(wording.title, wording.downloading,
                                     self._total // 1024, parent)
        self.dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dialog.canceled.connect(self._cancel)

    def run(self) -> None:
        self._start_next()
        self.dialog.show()
        self._loop.exec()
        self.dialog.canceled.disconnect(self._cancel)
        self.dialog.hide()
        self.dialog.deleteLater()
        if self._error_text is not None:
            show_error(self._parent, self._error_text, self._wording.title)

    def _start_next(self) -> None:
        if not self._queue:
            self._loop.quit()
            return
        self._key = self._queue.pop(0)
        self._task = Task(models.download, self._key)
        self._task.signals.progress.connect(self._progress)
        self._task.signals.result.connect(self._done)
        self._task.signals.error.connect(self._error)
        self._task.start()

    def _progress(self, done: int, total: int) -> None:
        self.dialog.setValue((self._finished + done) // 1024)

    def _done(self, installed: bool) -> None:
        if not installed or self._cancelled:
            self._loop.quit()
            return
        self._finished += models.MODELS[self._key].size
        self._start_next()

    def _error(self, message: str) -> None:
        self._error_text = f"{self._wording.failed}: {message}"
        self._loop.quit()

    def _cancel(self) -> None:
        # The running Task notices within one chunk and reports back, which
        # ends the loop; waiting for it keeps the Task alive until then.
        self._cancelled = True
        if self._task is not None:
            self._task.cancel_event.set()
