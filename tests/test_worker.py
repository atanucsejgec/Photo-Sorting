import gc
import logging
import threading
import weakref

from PySide6.QtCore import QThreadPool

from photosort.ui.worker import Task


def test_task_error_is_logged_with_traceback(qtbot, caplog):
    def boom(progress=None, cancel=None):
        raise ValueError("boom")

    caplog.set_level(logging.ERROR, logger="photosort")
    task = Task(boom)
    with qtbot.waitSignal(task.signals.error) as blocker:
        task.start()
    assert blocker.args == ["boom"]
    records = [r for r in caplog.records if r.name == "photosort"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    text = records[0].getMessage()
    assert "Traceback (most recent call last)" in text
    assert 'raise ValueError("boom")' in text
    assert "ValueError: boom" in text


def test_task_lets_go_of_its_arguments_once_run(qtbot):
    class Engine:
        pass

    engine = Engine()
    ref = weakref.ref(engine)
    task = Task(lambda engine, progress=None, cancel=None: 42, engine)
    del engine
    with qtbot.waitSignal(task.signals.result) as blocker:
        task.start()
    QThreadPool.globalInstance().waitForDone()
    gc.collect()
    assert blocker.args == [42]
    assert ref() is None  # the Task is still here, the argument is not


def test_task_first_runs_before_its_function_on_its_thread(qtbot):
    calls = []

    def before(x, cancel=None):
        calls.append(("first", x, threading.get_ident(), cancel))

    def fn(progress=None):
        calls.append(("fn", threading.get_ident()))
        return 7

    task = Task(fn, cancellable=False)
    task.first(before, "root")
    with qtbot.waitSignal(task.signals.result) as blocker:
        task.start()
    assert blocker.args == [7]
    (first, x, thread, cancel), (name, fn_thread) = calls
    assert (first, x, name) == ("first", "root", "fn")
    assert thread == fn_thread != threading.get_ident()
    assert cancel is task.cancel_event
