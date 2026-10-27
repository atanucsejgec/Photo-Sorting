"""The No people flow: the face models, the summary, Go and Undo."""

import threading
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QThreadPool

from photosort.ai import models
from photosort.faces import Face
from photosort.hashing import sha256_file
from photosort.journal import load_runs
from photosort.people import PeopleStore
from photosort.settings import Settings, load_settings
from photosort.thresholds import FACE_ENGINE_FAILS
from photosort.ui import dialogs, main_window, model_download
from photosort.ui.main_window import MainWindow


def _face() -> Face:
    v = np.zeros(512, np.float32)
    v[0] = 1.0
    return Face((2.0, 2.0, 10.0, 10.0), np.zeros((5, 2), np.float32), 0.9, v, (16, 16))


class FakeEngine:
    """Faces by file name (none if not named); records the calls."""

    def __init__(self, faces: dict[str, list[Face]] | None = None) -> None:
        self.faces = faces or {}
        self.calls: list[str] = []

    def analyse(self, path: Path) -> list[Face]:
        self.calls.append(Path(path).name)
        return list(self.faces.get(Path(path).name, []))


@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def asked(monkeypatch):
    """dialogs.ask records each question and gives asked.answer; the other
    dialogs must not open at all."""

    class Asked(list):
        answer = True

    questions = Asked()

    def ask(parent, title, text, yes, no, default="no"):
        questions.append((title, text, yes, no, default))
        return questions.answer

    def never(*args, **kwargs):
        raise AssertionError(f"unexpected dialog: {args[1:]}")

    monkeypatch.setattr(dialogs, "ask", ask)
    monkeypatch.setattr(dialogs, "choose", never)
    monkeypatch.setattr(dialogs, "warn", never)
    monkeypatch.setattr(dialogs, "info", never)
    return questions


@pytest.fixture
def models_present(monkeypatch):
    calls = []

    def ensure(parent, keys):
        calls.append(list(keys))
        return True

    monkeypatch.setattr(main_window, "ensure_models", ensure)
    return calls


@pytest.fixture
def make_window(qtbot, tmp_path, errors, asked):
    windows = []

    def _make(engine=None) -> MainWindow:
        w = MainWindow(Settings(), tmp_path / "config" / "settings.json",
                       people_store=PeopleStore(tmp_path / "people"))
        w.engine_factory = lambda use_gpu: engine or FakeEngine()
        qtbot.addWidget(w)
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def photos(tmp_path, make_jpeg):
    root = tmp_path / "photos"
    for rel in ("me.jpg", "sea.jpg", "trip/hills.jpg", "trip/us.jpg"):
        make_jpeg(root / rel)
    (root / "clip.mp4").write_bytes(b"not really a video")
    (root / "doc.pdf").write_bytes(b"%PDF-1.4")
    return root


def _start(w, root: Path, subfolders: bool = True) -> None:
    w.folder_edit.setText(str(root))
    w.subfolders_check.setChecked(subfolders)
    w.mode_buttons["no_people"].click()


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


def test_the_tile_comes_after_type_and_before_extension(make_window):
    w = make_window()
    keys = list(w.mode_buttons)
    assert keys[keys.index("type") + 1] == "no_people"
    assert keys[keys.index("no_people") + 1] == "extension"
    tile = w.mode_buttons["no_people"]
    assert (tile.name, tile.hint) == ("No people", "Photos without faces")
    assert tile.isEnabled()


def test_models_missing_asks_to_download_and_no_goes_home(make_window, photos, asked,
                                                         monkeypatch, tmp_path):
    looked_for = []
    monkeypatch.setattr(models, "missing",
                        lambda keys: looked_for.append(list(keys)) or list(keys))
    asked.answer = False  # Not now
    made = []
    w = make_window()
    w.engine_factory = lambda use_gpu: made.append(use_gpu) or FakeEngine()
    before = _hashes(photos)
    _start(w, photos)
    assert looked_for == [["face_detection", "face_recognition"]]
    assert [(title, text) for title, text, *_ in asked] == [
        (model_download.TITLE, model_download.QUESTION)]
    assert w.current_page() is w.home
    assert w._task is None and w._plan is None
    assert made == []  # no engine was made
    # Nothing else happened: the mode is not remembered as the last one.
    assert load_settings(tmp_path / "config" / "settings.json").last_mode != "no_people"
    assert _hashes(photos) == before


def test_summary_then_go_then_undo_restores_every_file(qtbot, make_window, photos,
                                                       models_present, errors):
    engine = FakeEngine({"me.jpg": [_face()], "us.jpg": [_face(), _face()]})
    made_on = []

    def factory(use_gpu):
        made_on.append((use_gpu, threading.current_thread() is threading.main_thread()))
        return engine

    before = _hashes(photos)
    w = make_window()
    w.engine_factory = factory
    _start(w, photos)
    assert models_present == [["face_detection", "face_recognition"]]
    assert w.current_page() is w.progress_page
    assert w.progress_label.text().startswith("Looking for faces…")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert made_on == [(w.settings.use_gpu, False)]  # made on the worker thread
    assert w.summary_label.text().splitlines() == [
        "2 photos without faces → No people",
        "2 photos with people stay",
    ]
    assert sorted(engine.calls) == ["hills.jpg", "me.jpg", "sea.jpg", "us.jpg"]
    assert (photos / ".photosort" / "cache.db").is_file()  # the face cache was used

    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 2 files." in w.done_label.text()
    assert _hashes(photos) == {
        "me.jpg": before["me.jpg"], "trip/us.jpg": before["trip/us.jpg"],
        "clip.mp4": before["clip.mp4"], "doc.pdf": before["doc.pdf"],
        "No people/sea.jpg": before["sea.jpg"],
        "No people/hills.jpg": before["trip/hills.jpg"]}
    assert [run.mode for run in load_runs(photos)] == ["no_people"]

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert "Undo complete: 2 files put back." in w.undo_label.text()
    assert _hashes(photos) == before
    assert errors == []


def test_a_broken_face_engine_goes_home_with_nothing_planned(qtbot, make_window, tmp_path,
                                                            make_jpeg, models_present,
                                                            errors):
    # Review Focus 4
    root = tmp_path / "broken"
    for n in range(FACE_ENGINE_FAILS + 1):
        make_jpeg(root / f"{n}.jpg")
    before = _hashes(root)

    class Broken:
        def analyse(self, path):
            raise RuntimeError("model trouble")

    w = make_window(Broken())
    _start(w, root)
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    assert errors == ["Could not check the files: model trouble"]
    assert w._plan is None
    assert _hashes(root) == before


def test_no_photo_without_faces_has_nothing_to_move(qtbot, make_window, tmp_path, make_jpeg,
                                                    models_present):
    root = tmp_path / "portraits"
    make_jpeg(root / "me.jpg")
    w = make_window(FakeEngine({"me.jpg": [_face()]}))
    _start(w, root, subfolders=False)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines() == [
        "Nothing to move.",
        "0 photos without faces → No people",
        "1 photos with people stay",
    ]
    assert not w.go_button.isEnabled()


def test_the_subfolders_warning_is_never_shown(qtbot, make_window, photos, asked,
                                               models_present):
    w = make_window()
    _start(w, photos, subfolders=True)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert asked == []
