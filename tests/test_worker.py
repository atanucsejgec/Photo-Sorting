import logging

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
