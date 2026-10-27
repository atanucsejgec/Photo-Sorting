import dataclasses
import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import onnxruntime
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QDialog, QPushButton

from photosort.ai import models, runtime
from photosort.executor import execute
from photosort.journal import RunJournal, load_runs
from photosort.plan import Move, Plan
from photosort.settings import DEFAULT_CONTENT_CATEGORIES, Settings, load_settings
from photosort.ui import dialogs, history_page, model_download
from photosort.ui.category_editor import CategoryEditor
from photosort.ui.history_page import HistoryPage, undo_summary
from photosort.ui.main_window import MainWindow
from photosort.ui.settings_page import SettingsPage
from photosort.undo import UndoResult

REPO = Path(__file__).resolve().parents[1]
DATE, MODE, FILES, STATUS = range(4)


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _run(root: Path, moves) -> str:
    plan = Plan("date", [Move(s, d) for s, d in moves], [])
    journal = RunJournal.create(root, "date", False, len(moves))
    execute(root, plan, journal)
    return journal.run_id


@pytest.fixture
def dialog_errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(HistoryPage, "show_error", lambda self, text: shown.append(text))
    monkeypatch.setattr(SettingsPage, "show_error", lambda self, text: shown.append(text))
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def make_history(qtbot, dialog_errors, monkeypatch):
    """A History page showing root's runs; its "Undo this sort?" is answered Yes."""
    monkeypatch.setattr(dialogs, "ask", lambda *args, **kwargs: True)

    def _make(root: Path) -> HistoryPage:
        d = HistoryPage()
        qtbot.addWidget(d)
        d.load(root)
        return d

    yield _make
    QThreadPool.globalInstance().waitForDone()


def _column(d: HistoryPage, col: int) -> list[str]:
    return [d.runs_table.item(r, col).text() for r in range(d.runs_table.rowCount())]


def test_undo_summary_wording():
    assert undo_summary(UndoResult(restored=2)) == "Undo complete: 2 files put back."
    result = UndoResult(restored=1, missing=["a.jpg", "b.jpg"],
                        renamed=[("c.jpg", "c (1).jpg")])
    assert undo_summary(result).splitlines() == [
        "Undo complete: 1 files put back.",
        "1 files were put back under a new name because the original name was taken.",
        "2 files were moved or deleted after the sort, so they were left where they are.",
    ]
    stuck = UndoResult(restored=1, failed=[("a.jpg", "in use"), ("b.jpg", "in use")])
    assert undo_summary(stuck).splitlines() == [
        "Undo not finished: 1 files put back.",
        "2 files couldn't be put back yet. Close any program using them and "
        "press Undo again.",
    ]


def test_history_lists_runs_and_undoes(qtbot, make_history, tmp_path, dialog_errors):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    _write(tmp_path / "c.jpg", b"c")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    _run(tmp_path, [("b.jpg", "e/b.jpg"), ("c.jpg", "e/c.jpg")])

    d = make_history(tmp_path)
    assert d.runs_table.rowCount() == 2
    assert _column(d, FILES) == ["2", "1"]  # newest first
    assert _column(d, MODE) == ["Date", "Date"]
    assert _column(d, STATUS) == ["Done", "Done"]
    started = load_runs(tmp_path)[0].started.astimezone()
    assert d.runs_table.item(0, DATE).text() == started.strftime("%Y-%m-%d %H:%M")

    d.runs_table.selectRow(0)
    assert d.undo_button.isEnabled()
    d.undo_button.click()
    qtbot.waitUntil(lambda: _column(d, STATUS)[0] == "Undone")
    assert _column(d, STATUS) == ["Undone", "Done"]
    assert d.result_label.text() == "Undo complete: 2 files put back."
    assert (tmp_path / "b.jpg").read_bytes() == b"b"
    assert (tmp_path / "c.jpg").read_bytes() == b"c"
    assert (tmp_path / "d/a.jpg").read_bytes() == b"a"

    d.runs_table.selectRow(0)
    assert not d.undo_button.isEnabled()  # already undone
    d.runs_table.selectRow(1)
    assert d.undo_button.isEnabled()
    assert dialog_errors == []


def test_history_blocks_older_dependent_run(make_history, tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    run2 = _run(tmp_path, [("d/a.jpg", "e/a.jpg")])

    d = make_history(tmp_path)
    d.runs_table.selectRow(1)
    assert not d.undo_button.isEnabled()
    assert d.undo_button.toolTip() == f"Undo these runs first: {run2}"
    assert d.blocked_label.text() == f"Undo these runs first: {run2}"
    d.runs_table.selectRow(0)
    assert d.undo_button.isEnabled()
    assert d.undo_button.toolTip() == ""
    assert d.blocked_label.text() == ""


def test_history_names_folder_of_blocking_run_inside(make_history, tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    inner = _run(tmp_path / "d", [("a.jpg", "e/a.jpg")])  # owner picked "d" later

    d = make_history(tmp_path)
    d.runs_table.selectRow(0)
    assert not d.undo_button.isEnabled()
    assert d.blocked_label.text() == f"Undo these runs first: {inner} (in d)"


def test_history_shows_interrupted_run(make_history, tmp_path):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    j = RunJournal.create(tmp_path, "date", False, planned=5)
    for name in ("a.jpg", "b.jpg"):
        i = j.record_intent(name, f"d/{name}")
        (tmp_path / "d").mkdir(exist_ok=True)
        os.rename(tmp_path / name, tmp_path / "d" / name)
        j.record_done(i)
    j._log.close()  # the process died: finish() never ran, meta "moved" is 0

    d = make_history(tmp_path)
    assert _column(d, STATUS) == ["Interrupted"]
    assert _column(d, FILES) == ["2"]
    d.runs_table.selectRow(0)
    assert d.undo_button.isEnabled()


def test_history_undo_error_is_shown(qtbot, make_history, tmp_path, dialog_errors,
                                     monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])

    def broken(root, run_id, progress=None):
        raise OSError("disk unplugged")

    monkeypatch.setattr(history_page, "undo_run", broken)
    d = make_history(tmp_path)
    d.runs_table.selectRow(0)
    d.undo_button.click()
    qtbot.waitUntil(lambda: dialog_errors != [])
    assert dialog_errors == ["Could not undo: disk unplugged"]
    assert d.undo_button.isEnabled()
    assert _column(d, STATUS) == ["Done"]


def test_history_says_while_it_undoes(qtbot, make_history, tmp_path, monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _run(tmp_path, [("a.jpg", "d/a.jpg")])
    started, release = threading.Event(), threading.Event()

    def slow(root, run_id, progress=None):
        started.set()
        release.wait(10)
        raise OSError("stopped")

    monkeypatch.setattr(history_page, "undo_run", slow)
    d = make_history(tmp_path)
    running = []
    d.undo_running.connect(running.append)
    d.runs_table.selectRow(0)
    d.undo_button.click()
    assert running == [True] and d.undoing()
    assert not d.undo_button.isEnabled()
    d.undo_button.click()  # one undo at a time
    assert started.wait(5)
    release.set()
    qtbot.waitUntil(lambda: running == [True, False])
    assert not d.undoing()


def test_settings_page_saves_gpu(qtbot, tmp_path, dialog_errors):
    path = tmp_path / "config" / "settings.json"
    settings = Settings(use_gpu=True)
    d = SettingsPage(settings, path)
    qtbot.addWidget(d)
    assert d.gpu_check.text() == "Use GPU"
    assert d.gpu_check.isChecked()
    assert d.note_label.text() == (
        "The GPU makes face recognition faster. Without one, the CPU is used."
    )
    d.gpu_check.setChecked(False)  # saved at once
    assert settings.use_gpu is False
    assert load_settings(path).use_gpu is False
    assert dialog_errors == []


def test_settings_page_saves_the_event_settings(qtbot, tmp_path, dialog_errors):
    path = tmp_path / "config" / "settings.json"
    settings = Settings()
    d = SettingsPage(settings, path)
    qtbot.addWidget(d)
    assert (d.gap_label.text(), d.gap_unit_label.text()) == ("New event after a gap of", "hours")
    assert (d.min_files_label.text(), d.min_files_unit_label.text()) == (
        "An event needs at least", "files")
    assert (d.gap_spin.minimum(), d.gap_spin.maximum(), d.gap_spin.value()) == (1, 168, 6)
    assert (d.min_files_spin.minimum(), d.min_files_spin.maximum(),
            d.min_files_spin.value()) == (2, 1000, 10)
    d.gap_spin.setValue(12)
    d.min_files_spin.setValue(25)
    assert (settings.event_gap_hours, settings.event_min_files) == (12, 25)
    saved = load_settings(path)
    assert (saved.event_gap_hours, saved.event_min_files) == (12, 25)
    assert dialog_errors == []


def test_settings_page_says_when_it_cannot_save(qtbot, tmp_path, dialog_errors):
    (tmp_path / "config").write_text("a file, not a folder")
    settings = Settings()
    d = SettingsPage(settings, tmp_path / "config" / "settings.json")
    qtbot.addWidget(d)
    d.gap_spin.setValue(30)
    assert settings.event_gap_hours == 30  # it still applies until the app closes
    assert len(dialog_errors) == 1 and dialog_errors[0].startswith("Could not save settings")


def test_settings_shows_runtime_status(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_gpu_failed", False)
    monkeypatch.setattr(runtime, "_cuda_driver_present", lambda: True)
    monkeypatch.setattr(onnxruntime, "get_available_providers",
                        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    d = SettingsPage(Settings(use_gpu=True), tmp_path / "settings.json")
    qtbot.addWidget(d)
    assert d.runtime_label.text() == ""  # asked when shown: it loads the AI runtime
    d.show()
    assert d.runtime_label.text() == "Running on: GPU (CUDA)"
    d.gpu_check.setChecked(False)
    assert d.runtime_label.text() == "Running on: CPU (GPU switched off)"

    monkeypatch.setattr(onnxruntime, "get_available_providers",
                        lambda: ["CPUExecutionProvider"])
    d.gpu_check.setChecked(True)
    assert d.runtime_label.text() == "Running on: CPU (no usable GPU found)"


def test_settings_edits_the_categories(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "config" / "settings.json"
    settings = Settings()
    d = SettingsPage(settings, path)
    qtbot.addWidget(d)
    assert d.categories_button.text() == "Content categories…"
    seen = []

    def edit(editor):
        seen.append(editor.categories())
        editor.list_widget.setCurrentRow(0)
        editor.remove_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CategoryEditor, "exec", edit)
    d.categories_button.click()
    assert seen == [list(DEFAULT_CONTENT_CATEGORIES)]
    assert settings.content_categories == list(DEFAULT_CONTENT_CATEGORIES[1:])
    assert load_settings(path).content_categories == list(DEFAULT_CONTENT_CATEGORIES[1:])


def test_settings_editor_cancelled_changes_nothing(qtbot, tmp_path, monkeypatch):
    settings = Settings(content_categories=["Food", "Cars"])
    path = tmp_path / "settings.json"
    d = SettingsPage(settings, path)
    qtbot.addWidget(d)

    def edit(editor):
        editor.reset_button.click()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(CategoryEditor, "exec", edit)
    d.categories_button.click()
    assert settings.content_categories == ["Food", "Cars"]
    assert not path.exists()


@pytest.fixture
def model_store(tmp_path, monkeypatch):
    """Two small models served from file:// URLs; the real files are never touched.

    The download question is answered with store.answer; errors are collected
    in store.errors instead of being shown.
    """
    table = {}
    for key, data in (("a", os.urandom(3 << 20)), ("b", b"small model")):
        src = _write(tmp_path / "src" / f"{key}.onnx", data)
        table[key] = models.Model(key, f"{key}/model.onnx", src.as_uri(), len(data),
                                  hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(models, "MODELS", table)
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path / "models")
    store = SimpleNamespace(answer=True, asked=[], defaults=[], errors=[],
                            titles=[], sources={k: tmp_path / "src" / f"{k}.onnx" for k in table})

    def ask(parent, title, text, yes, no, default="no"):
        store.titles.append(title)
        store.asked.append(text)
        store.defaults.append(default)
        return store.answer

    def show_error(parent, text, title=model_download.TITLE):
        store.titles.append(title)
        store.errors.append(text)

    monkeypatch.setattr(dialogs, "ask", ask)
    monkeypatch.setattr(model_download, "show_error", show_error)
    yield store
    QThreadPool.globalInstance().waitForDone()


def test_ensure_models_present_asks_nothing(qtbot, model_store):
    for key, m in models.MODELS.items():
        _write(models.model_path(key), model_store.sources[key].read_bytes())
    assert model_download.ensure_models(None, ["a", "b"]) is True
    assert model_store.asked == []


def test_ensure_models_downloads_after_yes(qtbot, model_store):
    assert model_download.ensure_models(None, ["a", "b"]) is True
    assert model_store.asked == [
        "Photo Sorter needs to download the face models once (180 MB). Download now?"]
    assert models.missing(["a", "b"]) == []
    assert model_store.errors == []
    assert model_store.defaults == ["no"]  # a download wants a deliberate yes


def test_ensure_models_no_downloads_nothing(qtbot, model_store):
    model_store.answer = False
    assert model_download.ensure_models(None, ["a", "b"]) is False
    assert models.missing(["a", "b"]) == ["a", "b"]


def test_ensure_models_cancel_stops(qtbot, model_store, monkeypatch):
    real_download = models.download

    def held_download(key, progress=None, cancel=None):
        progress(1, models.MODELS[key].size)
        assert cancel.wait(5)  # until the owner presses Cancel
        return real_download(key, progress=progress, cancel=cancel)

    real_progress = model_download._Downloads._progress

    def press_cancel(self, done, total):
        real_progress(self, done, total)
        self.dialog.findChild(QPushButton).click()

    monkeypatch.setattr(models, "download", held_download)
    monkeypatch.setattr(model_download._Downloads, "_progress", press_cancel)
    assert model_download.ensure_models(None, ["a", "b"]) is False
    assert models.missing(["a", "b"]) == ["a", "b"]
    assert model_store.errors == []


def test_ensure_models_reports_damaged_download(qtbot, model_store):
    models.MODELS["a"] = dataclasses.replace(models.MODELS["a"], sha256="0" * 64)
    assert model_download.ensure_models(None, ["a", "b"]) is False
    assert len(model_store.errors) == 1
    assert "is damaged, please try again" in model_store.errors[0]
    assert models.missing(["a", "b"]) == ["a", "b"]  # stops at the first error


@pytest.fixture
def clip_store(tmp_path, monkeypatch, model_store):
    """model_store with the CLIP keys: small files that claim the real sizes
    in the question (the download itself is checked against the small ones)."""
    table = {}
    for key, data in (("clip_visual", os.urandom(1 << 20)), ("clip_textual", b"text"),
                      ("clip_tokenizer", b"words")):
        src = _write(tmp_path / "src" / f"{key}.onnx", data)
        table[key] = models.Model(key, f"{key}/model.onnx", src.as_uri(), len(data),
                                  hashlib.sha256(data).hexdigest())
        model_store.sources[key] = src
    monkeypatch.setattr(models, "MODELS", table)
    return model_store


def _clip_question(mb: int) -> str:
    return (f"Photo Sorter needs to download the picture-understanding model once ({mb} MB). "
            "Photos stay on this computer; only the model is downloaded. Download it now?")


def test_clip_question_names_the_model_and_the_missing_size(qtbot, clip_store, monkeypatch):
    sizes = {"clip_visual": 351_613_724, "clip_textual": 254_193_396,
             "clip_tokenizer": 3_642_073}
    real_missing = models.missing
    monkeypatch.setattr(models, "missing", lambda keys: [
        k for k in real_missing(keys) if k != "clip_tokenizer"])  # as if it were there
    for key, size in sizes.items():
        models.MODELS[key] = dataclasses.replace(models.MODELS[key], size=size)
    clip_store.answer = False
    assert model_download.ensure_models(None, models.CLIP_MODELS) is False
    assert clip_store.asked == [_clip_question(606)]  # 351.6 + 254.2 MB, not the tokenizer
    assert all("face" not in title.casefold() for title in clip_store.titles)

    monkeypatch.setattr(models, "missing", real_missing)
    clip_store.asked.clear()
    assert model_download.ensure_models(None, models.CLIP_MODELS) is False
    assert clip_store.asked == [_clip_question(609)]


def test_clip_download_never_says_face(qtbot, clip_store, monkeypatch):
    shown = []
    real_progress = model_download._Downloads._progress

    def watch(self, done, total):
        shown.append((self.dialog.windowTitle(), self.dialog.labelText()))
        real_progress(self, done, total)

    monkeypatch.setattr(model_download._Downloads, "_progress", watch)
    assert model_download.ensure_models(None, models.CLIP_MODELS) is True
    assert models.missing(models.CLIP_MODELS) == []
    assert shown and all("face" not in " ".join(texts).casefold() for texts in shown)
    assert shown[0][1] == "Downloading the picture-understanding model…"

    for key in models.CLIP_MODELS:
        models.model_path(key).unlink()
    models.MODELS["clip_visual"] = dataclasses.replace(models.MODELS["clip_visual"],
                                                       sha256="0" * 64)
    assert model_download.ensure_models(None, models.CLIP_MODELS) is False
    [error] = clip_store.errors
    assert error.startswith("Could not download the picture-understanding model: ")
    assert all("face" not in title.casefold() for title in clip_store.titles)


def test_face_download_keeps_its_wording(qtbot, model_store, monkeypatch):
    shown = []
    real_progress = model_download._Downloads._progress

    def watch(self, done, total):
        shown.append((self.dialog.windowTitle(), self.dialog.labelText()))
        real_progress(self, done, total)

    monkeypatch.setattr(model_download._Downloads, "_progress", watch)
    assert model_download.ensure_models(None, ["a", "b"]) is True
    assert set(shown) == {("Face models", "Downloading the face models…")}
    assert model_store.titles == ["Face models"]


@pytest.fixture
def window(qtbot, tmp_path, dialog_errors):
    w = MainWindow(Settings(), tmp_path / "config" / "settings.json")
    qtbot.addWidget(w)
    yield w
    w.close()
    QThreadPool.globalInstance().waitForDone()


def test_the_sidebar_pages_use_the_windows_folder_and_settings(window, tmp_path):
    window.sidebar.items["history"].click()
    assert window.history_page.root is None
    window.folder_edit.setText(str(tmp_path))
    window.sidebar.items["sort"].click()
    window.sidebar.items["history"].click()
    assert window.history_page.root == tmp_path
    assert window.settings_page.settings is window.settings


def _launch(tmp_path, nvidia: bool) -> list[str]:
    """Run the launcher through a symlink with a fake uv; return uv's arguments.

    PATH holds only what the script needs, so whether nvidia-smi is found
    does not depend on this computer.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "dirname", "readlink"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    fake_uv = bin_dir / "uv"
    fake_uv.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
    fake_uv.chmod(0o755)
    if nvidia:
        fake_smi = bin_dir / "nvidia-smi"
        fake_smi.write_text("#!/bin/sh\n")
        fake_smi.chmod(0o755)
    link = tmp_path / "launch"
    link.symlink_to(REPO / "scripts" / "photo-sorter.sh")

    env = {**os.environ, "PATH": str(bin_dir)}
    out = subprocess.run([str(link), "--x", "a b"], env=env, cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def test_launcher_resolves_repo_through_symlink(tmp_path):
    assert _launch(tmp_path, nvidia=True) == [
        "run", "--project", str(REPO), "photo-sorter", "--x", "a b"]


def test_launcher_skips_cuda_libraries_without_nvidia_gpu(tmp_path):
    assert _launch(tmp_path, nvidia=False) == [
        "run", "--project", str(REPO), "--no-group", "gpu", "photo-sorter", "--x", "a b"]


def test_scripts_are_executable():
    for name in ("photo-sorter.sh", "install-desktop-entry.sh"):
        assert os.access(REPO / "scripts" / name, os.X_OK)


def _fail_undo_moves_of(monkeypatch, names):
    import errno

    from photosort import undo

    real = undo.move_file

    def flaky(src, dst):
        if os.path.basename(src) in names:
            raise PermissionError(errno.EACCES, "File in use", str(src))
        return real(src, dst)

    monkeypatch.setattr(undo, "move_file", flaky)


def test_history_partial_undo_keeps_undo_available(qtbot, make_history, tmp_path,
                                                   dialog_errors, monkeypatch):
    _write(tmp_path / "a.jpg", b"a")
    _write(tmp_path / "b.jpg", b"b")
    _run(tmp_path, [("a.jpg", "d/a.jpg"), ("b.jpg", "d/b.jpg")])
    d = make_history(tmp_path)
    d.runs_table.selectRow(0)

    _fail_undo_moves_of(monkeypatch, {"b.jpg"})
    d.undo_button.click()
    qtbot.waitUntil(lambda: "couldn't be put back" in d.result_label.text())
    monkeypatch.undo()
    monkeypatch.setattr(dialogs, "ask", lambda *args, **kwargs: True)  # undone too
    assert _column(d, STATUS) == ["Done"]
    assert d.undo_button.isEnabled()

    d.undo_button.click()
    qtbot.waitUntil(lambda: _column(d, STATUS) == ["Undone"])
    assert d.result_label.text() == "Undo complete: 1 files put back."
    assert (tmp_path / "b.jpg").read_bytes() == b"b"
    assert dialog_errors == []


def test_an_unpatched_dialog_fails_the_test_at_once(_no_waiting_dialogs):
    """The conftest guard: a dialog nobody answers fails, not hangs."""
    from photosort.ui.dialogs import MessageDialog

    dialog = MessageDialog(None, "Leave this review?", "Your answers so far will be lost.",
                           [("Stay", False), ("Leave", True)], default=0, keys=())
    with pytest.raises(AssertionError, match="MessageDialog 'Leave this review\\?' opened"):
        dialog.exec()
    assert not dialog.isVisible()
    _no_waiting_dialogs.clear()  # seen: this test passes
    dialog.deleteLater()
