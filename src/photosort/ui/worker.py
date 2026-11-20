"""Run slow work (scanning, moving, undo) off the GUI thread.

A Task calls its function on QThreadPool.globalInstance() (or the pool
given to start) and reports back through signals. TaskSignals is created on the GUI thread, so its signals are
queued to slots there: connect them to methods of a QObject that lives on the
GUI thread (such as the main window).

When the function raises, its traceback is logged to the "photosort" logger
before the error signal carries the message. Once a Task's result or error
has been delivered, the GUI thread's garbage check runs (see gc_guard).
"""

import logging
import threading
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from photosort.ui import gc_guard

_log = logging.getLogger("photosort")


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
        self._first: list[tuple[Callable[..., object], tuple]] = []
        self.signals = TaskSignals()
        self.cancel_event = threading.Event()

    def first(self, fn: Callable[..., object], *args) -> None:
        """Call fn(*args, cancel=...) on the pool thread before the Task's own
        function (old records are brought in before a sort, say). cancel is
        cancel_event, or, with cancellable=False, an Event never set: a Task
        that can't be stopped part-way runs its first functions in full too
        (an undo must see every run that may block it). The return value is
        dropped; an exception ends the Task as the function's would. Call
        before start."""
        self._first.append((fn, args))

    def start(self, pool: QThreadPool | None = None) -> None:
        """Run on pool (the global one by default). Connect to the signals
        first: the garbage check, connected here, then runs after them."""
        gc_guard.watch(self.signals)
        (pool or QThreadPool.globalInstance()).start(self)

    def run(self) -> None:
        # A Task runs once, but may be kept alive long after: it lets go of
        # the function and its arguments (a face engine, say) now.
        fn, args, first = self._fn, self._args, self._first
        self._fn, self._args, self._first = None, (), []
        kwargs: dict[str, object] = {"progress": self.signals.progress.emit}
        if self._cancellable:
            kwargs["cancel"] = self.cancel_event
        first_cancel = self.cancel_event if self._cancellable else threading.Event()
        try:
            for before, before_args in first:
                before(*before_args, cancel=first_cancel)
            value = fn(*args, **kwargs)
        except Exception as e:
            name = getattr(fn, "__name__", repr(fn))
            _log.error("Task %s failed:\n%s", name, traceback.format_exc())
            self.signals.error.emit(str(e) or type(e).__name__)
        else:
            self.signals.result.emit(value)
