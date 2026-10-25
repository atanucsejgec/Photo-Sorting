import gc
import threading
import weakref

import pytest
from PySide6.QtCore import QThreadPool

from photosort.ui import gc_guard
from photosort.ui.worker import Task


@pytest.fixture
def no_guard(qapp):
    """The test session's guard taken away for the test, and put back after."""
    gc_guard.uninstall()
    yield
    gc_guard.uninstall()
    gc_guard.install(qapp)


class _Cycle:
    def __init__(self) -> None:
        self.me = self


def test_install_turns_automatic_collection_off_until_uninstalled(qapp, no_guard):
    assert gc.isenabled()
    guard = gc_guard.install(qapp)
    assert not gc.isenabled()
    assert gc_guard.install(qapp) is guard  # one guard at a time
    gc_guard.uninstall()
    assert gc.isenabled()
    gc_guard.uninstall()  # twice is harmless
    assert gc.isenabled()


def test_uninstall_leaves_collection_off_if_it_was_off(qapp, no_guard):
    gc.disable()
    try:
        gc_guard.install(qapp)
        gc_guard.uninstall()
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_the_timer_collects_cycles_on_the_gui_thread(qtbot, qapp, no_guard):
    old = gc.get_threshold()
    gc.set_threshold(1, old[1], old[2])
    try:
        collected = []
        cycle = _Cycle()
        weakref.finalize(cycle, lambda: collected.append(threading.current_thread()))
        gc_guard.install(qapp, interval_ms=10)
        del cycle
        qtbot.waitUntil(lambda: collected != [])
        assert collected == [threading.main_thread()]
    finally:
        gc.set_threshold(*old)


@pytest.mark.parametrize("count, generation", [
    ((5, 0, 0), None),  # below every threshold: nothing to do
    ((701, 0, 0), 0),
    ((701, 11, 0), 1),
    ((701, 11, 11), 2),
])
def test_check_collects_the_oldest_generation_over_its_threshold(
        qapp, no_guard, monkeypatch, count, generation):
    guard = gc_guard.install(qapp)
    asked = []
    monkeypatch.setattr(gc, "get_count", lambda: count)
    monkeypatch.setattr(gc, "get_threshold", lambda: (700, 10, 10))
    monkeypatch.setattr(gc, "collect", lambda gen=2: asked.append(gen) or 0)
    guard.check()
    assert asked == ([] if generation is None else [generation])


def test_a_finished_task_is_followed_by_a_check_on_the_gui_thread(
        qtbot, qapp, no_guard, monkeypatch):
    checks = []
    monkeypatch.setattr(gc_guard.GcGuard, "check",
                        lambda self, *args: checks.append(threading.current_thread()))
    gc_guard.install(qapp, interval_ms=60_000)  # the timer stays out of it
    for fn in (lambda progress=None, cancel=None: 1, lambda progress=None, cancel=None: 1 / 0):
        task = Task(fn)
        task.start()
        QThreadPool.globalInstance().waitForDone()
        qtbot.waitUntil(lambda: len(checks) == 1)
        assert checks.pop() is threading.main_thread()


def test_check_without_a_guard_does_nothing(no_guard, monkeypatch):
    monkeypatch.setattr(gc, "collect", lambda gen=2: pytest.fail("collected"))
    gc_guard.check()
