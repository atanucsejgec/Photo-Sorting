"""Ask once, then download the missing model files with a progress dialog.

The downloads run one after another as worker Tasks; ensure_models waits in
a local event loop, so the window keeps painting while it does.
"""

from collections.abc import Iterable

from PySide6.QtCore import QEventLoop, QObject, Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from photosort.ai import models
from photosort.ui.worker import Task

QUESTION = "Photo Sorter needs to download the face models once (180 MB). Download now?"
TITLE = "Face models"


def show_error(parent: QWidget | None, text: str) -> None:
    QMessageBox.warning(parent, TITLE, text)


def ensure_models(parent: QWidget | None, keys: Iterable[str]) -> bool:
    """True when every model in keys is present, downloading after a Yes."""
    keys = list(keys)
    todo = models.missing(keys)
    if not todo:
        return True
    answer = QMessageBox.question(parent, TITLE, QUESTION)
    if answer != QMessageBox.StandardButton.Yes:
        return False
    _Downloads(parent, todo).run()
    return not models.missing(keys)


class _Downloads(QObject):
    def __init__(self, parent: QWidget | None, keys: list[str]) -> None:
        super().__init__()
        self._parent = parent
        self._queue = list(keys)
        self._total = sum(models.MODELS[k].size for k in keys)
        self._finished = 0  # bytes of the models already downloaded
        self._key: str | None = None
        self._task: Task | None = None
        self._cancelled = False
        self._error_text: str | None = None
        self._loop = QEventLoop()
        # Progress counts KiB so the total fits in the dialog's int.
        self.dialog = QProgressDialog("Downloading the face models…", "Cancel",
                                      0, self._total // 1024, parent)
        self.dialog.setWindowTitle(TITLE)
        self.dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoReset(False)
        self.dialog.setAutoClose(False)
        self.dialog.canceled.connect(self._cancel)

    def run(self) -> None:
        self._start_next()
        self.dialog.show()
        self._loop.exec()
        self.dialog.canceled.disconnect(self._cancel)
        self.dialog.hide()
        self.dialog.deleteLater()
        if self._error_text is not None:
            show_error(self._parent, self._error_text)

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
        self._error_text = f"Could not download the face models: {message}"
        self._loop.quit()

    def _cancel(self) -> None:
        # The running Task notices within one chunk and reports back, which
        # ends the loop; waiting for it keeps the Task alive until then.
        self._cancelled = True
        if self._task is not None:
            self._task.cancel_event.set()
