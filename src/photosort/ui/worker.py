"""Run slow work (scanning, moving, undo) off the GUI thread.

A Task calls its function on QThreadPool.globalInstance() and reports back
through signals. TaskSignals is created on the GUI thread, so its signals are
queued to slots there: connect them to methods of a QObject that lives on the
GUI thread (such as the main window).
"""

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskSignals(QObject):
    progress = Signal(int, int)  # (processed, total)
    result = Signal(object)  # the function's return value
    error = Signal(str)  # the message of the exception it raised


class Task(QRunnable):
    """Call fn(*args, progress=..., cancel=self.cancel_event) on a pool thread.

    With cancellable=False the cancel argument is left out (for functions,
    such as undo_run, that cannot be stopped part-way).
    """

    def __init__(self, fn: Callable[..., object], *args, cancellable: bool = True) -> None:
        super().__init__()
        # The owner keeps a reference to the Task; Qt must not delete it.
        self.setAutoDelete(False)
        self._fn = fn
        self._args = args
        self._cancellable = cancellable
        self.signals = TaskSignals()
        self.cancel_event = threading.Event()

    def start(self) -> None:
        QThreadPool.globalInstance().start(self)

    def run(self) -> None:
        kwargs: dict[str, object] = {"progress": self.signals.progress.emit}
        if self._cancellable:
            kwargs["cancel"] = self.cancel_event
        try:
            value = self._fn(*self._args, **kwargs)
        except Exception as e:
            self.signals.error.emit(str(e) or type(e).__name__)
        else:
            self.signals.result.emit(value)
