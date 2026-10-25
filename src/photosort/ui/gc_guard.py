"""Collect Python's garbage on the GUI thread only.

Python's own collector runs on whichever thread happens to allocate when a
threshold is crossed: often a worker Task on the thread pool. A collection
there, while the GUI thread is busy with Qt objects, can crash PySide (its
wrappers are traversed mid-change). So, once installed, the automatic
collector is off; the guard checks the same thresholds on the GUI thread,
every interval_ms and after each Task's result or error is delivered, and
collects on a schedule like the automatic collector's (not exactly the same:
CPython 3.12 can go straight to an older generation and holds gen 2 back
until enough long-lived objects are pending; the guard does neither).
"""

import gc

from PySide6.QtCore import QCoreApplication, QObject, QTimer

INTERVAL_MS = 1000


class GcGuard(QObject):
    """Lives on the GUI thread; check() collects if a threshold is crossed."""

    def __init__(self, app: QCoreApplication, interval_ms: int) -> None:
        super().__init__(app)
        self.was_enabled = gc.isenabled()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(interval_ms)

    def check(self, *_signal_args) -> None:
        """Collect the oldest generation whose threshold is crossed, if any
        (gen 0 by allocations, gen 1 and 2 by collections of the one younger)."""
        counts, thresholds = gc.get_count(), gc.get_threshold()
        generation = None
        for gen in range(3):
            if counts[gen] > thresholds[gen] and (gen == 0 or generation == gen - 1):
                generation = gen
        if generation is not None:
            gc.collect(generation)


_guard: GcGuard | None = None


def install(app: QCoreApplication, interval_ms: int = INTERVAL_MS) -> GcGuard:
    """Turn the automatic collector off and collect on app's (GUI) thread
    from now on. Installing again returns the guard already installed."""
    global _guard
    if _guard is None:
        _guard = GcGuard(app, interval_ms)
    return _guard


def uninstall() -> None:
    """Stop the guard and give the automatic collector back, if it was on."""
    global _guard
    if _guard is None:
        return
    guard, _guard = _guard, None
    guard.timer.stop()
    if guard.was_enabled:
        gc.enable()
    guard.deleteLater()


def check() -> None:
    """The guard's check now, if one is installed. GUI thread only."""
    if _guard is not None:
        _guard.check()


def watch(signals: QObject) -> None:
    """Check once a Task's result or error has been delivered (signals is
    its TaskSignals, made on the GUI thread)."""
    if _guard is not None:
        signals.result.connect(_guard.check)
        signals.error.connect(_guard.check)
