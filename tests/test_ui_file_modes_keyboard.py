"""No people, Extension and Date (all files) by keyboard alone, from the
folder typed on Home to Undo on Done: the subfolders warning (Cancel, the
default, or Continue), the date choice (Modified, Created or Esc) and the
summary. After the window is shown only keys are pressed (qtbot.keyClick(s));
there is no .click(). The files are tmp files and the face engine a fake.

The warning's and the date choice's strips are checked against their live
keys. What the old-UI tests of these modes pinned is kept here: the date
chosen is remembered, Cancel and Esc plan nothing, and No people makes its
face engine off the GUI thread and goes Home, saying why, when it can't.
"""

import os
import threading
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
from keys import (
    _activate,
    _focused,
    _go,
    _in_dialog,
    _key,
    _on_summary,
    _records,
    _start_by_keyboard,
    _to_tile,
    _type_folder,
    errors,  # noqa: F401  (fixtures, used by name)
    make_window,  # noqa: F401
    models_present,  # noqa: F401
    settings_path,  # noqa: F401
)
from PySide6.QtCore import Qt

from photosort.faces import Face
from photosort.hashing import sha256_file
from photosort.modes import all_dates
from photosort.settings import Settings, load_settings
from photosort.ui.flows import files as files_flow
from photosort.ui.home_page import ModeTile
from photosort.ui.shortcuts import ShortcutStrip

ASK_STRIP = "←→ choose · Enter pick · Esc no/close"
CHOOSE_STRIP = "←→ choose · Enter pick · Esc cancel"


def _hashes(root: Path) -> dict[str, str]:
    """Every file under root (but the app's own .photosort), by path, to its sha256."""
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


def _answer(qtbot, seen: list, *keys) -> None:
    """In the next dialog: note its title, focused button, strip and live
    keys, then press keys."""
    _answer_each(qtbot, seen, keys)


def _answer_each(qtbot, seen: list, *answers: tuple) -> None:
    """In each of the next dialogs, one after another: note its title,
    focused button, strip and live keys, then press its answer's keys."""
    if not answers:
        return
    keys, rest = answers[0], answers[1:]

    def act(dialog):
        qtbot.waitUntil(lambda: _focused() is not None and _focused().window() is dialog)
        seen.append({"title": dialog.windowTitle(), "focus": _focused().text(),
                     "strip": dialog.findChild(ShortcutStrip).text(),
                     "live": _records(_focused(), dialog)})
        for key in keys:
            _key(qtbot, key)
        _answer_each(qtbot, seen, *rest)  # once this one has closed

    _in_dialog(qtbot, act)


def _subfolders_on(qtbot, w) -> None:
    """S on the tiles: subfolders included."""
    assert isinstance(_focused(), ModeTile)
    if not w.home.subfolders_switch.isChecked():
        _key(qtbot, Qt.Key.Key_S)
    assert w.home.subfolders_switch.isChecked()


def _start(qtbot, w, folder: Path, mode: str, subfolders: bool) -> None:
    """The folder typed, subfolders as asked (S), then Enter on mode's tile."""
    _type_folder(qtbot, w, folder)
    if subfolders:
        _subfolders_on(qtbot, w)
    assert w.home.subfolders_switch.isChecked() is subfolders
    _to_tile(qtbot, mode)
    _key(qtbot, Qt.Key.Key_Return)


def _undo_by_u(qtbot, w) -> None:
    """U on Done, → to Undo (Cancel is focused first) and Enter; the undo ends."""
    seen = []
    _answer(qtbot, seen, Qt.Key.Key_Right, Qt.Key.Key_Return)
    _key(qtbot, Qt.Key.Key_U)
    assert [(s["title"], s["focus"], s["strip"]) for s in seen] == [
        ("Undo this sort?", "Cancel", ASK_STRIP)]
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    _activate(qtbot, w)


def _write(path: Path, data: bytes, day: date | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if day is not None:
        noon = datetime(day.year, day.month, day.day, 12).timestamp()
        os.utime(path, (noon, noon))


# ----- Extension ---------------------------------------------------------------------------

@pytest.fixture
def mixed(tmp_path) -> Path:
    """Two PDFs and an APK at the top, a program's DLL in a subfolder."""
    root = tmp_path / "downloads"
    for rel in ("a.pdf", "b.pdf", "c.apk", "app/lib.dll"):
        _write(root / rel, rel.encode())
    return root


@pytest.mark.real_dialogs
def test_extension_by_keyboard_with_the_warning_then_undo(qtbot, make_window, mixed, errors):
    before = _hashes(mixed)
    w = make_window()
    seen = []
    _answer(qtbot, seen, Qt.Key.Key_Right, Qt.Key.Key_Return)  # Continue, not Cancel
    _start(qtbot, w, mixed, "extension", subfolders=True)
    assert seen == [{"title": files_flow.SUBFOLDERS_TITLE, "focus": "Cancel",
                     "strip": ASK_STRIP, "live": ASK_STRIP}]
    _activate(qtbot, w)
    _go(qtbot, w)
    assert w.summary_page.heading_label.text() == "Ready to sort by Extension"
    assert "Moved 4 files." in w.done_label.text()
    assert sorted(_hashes(mixed)) == ["APK/c.apk", "DLL/lib.dll", "PDF/a.pdf", "PDF/b.pdf"]

    _undo_by_u(qtbot, w)
    assert _hashes(mixed) == before
    assert sorted(p.name for p in mixed.iterdir()) == [
        ".photosort", "a.pdf", "app", "b.pdf", "c.apk"]
    qtbot.waitUntil(lambda: _focused() is w.home_button)
    _key(qtbot, Qt.Key.Key_Return)  # Home
    assert w.current_page() is w.home
    assert errors == []


@pytest.mark.real_dialogs
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape], ids=["Enter", "Esc"])
def test_extension_cancel_or_esc_at_the_warning_goes_home(qtbot, make_window, mixed, errors,
                                                          key):
    before = _hashes(mixed)
    w = make_window()
    seen = []
    _answer(qtbot, seen, key)  # Enter on Cancel, the default, or Esc
    _start(qtbot, w, mixed, "extension", subfolders=True)
    assert [s["focus"] for s in seen] == ["Cancel"]
    assert w.current_page() is w.home
    assert w._plan is None
    assert not w.busy
    assert _hashes(mixed) == before
    assert errors == []


def test_space_on_the_extension_summary_moves_nothing(qtbot, make_window, mixed, errors):
    before = _hashes(mixed)
    w = make_window()
    _start(qtbot, w, mixed, "extension", subfolders=False)
    _on_summary(qtbot, w)  # Go focused, and armed
    _key(qtbot, Qt.Key.Key_Space)
    qtbot.wait(50)
    assert w.current_page() is w.summary_page
    assert _hashes(mixed) == before
    assert errors == []


def test_extension_without_subfolders_asks_nothing(qtbot, make_window, mixed, errors):
    # Any dialog would fail this test (see conftest): none is real here.
    w = make_window()
    _start(qtbot, w, mixed, "extension", subfolders=False)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines()[0] == "3 files → 2 folders"
    qtbot.waitUntil(lambda: _focused() is w.go_button)
    _key(qtbot, Qt.Key.Key_Escape)  # back: Home, nothing moved
    assert w.current_page() is w.home
    assert (mixed / "a.pdf").is_file()
    assert errors == []


# ----- Date (all files) --------------------------------------------------------------------

CREATED = date(2020, 1, 2)


@pytest.fixture
def dated(tmp_path, monkeypatch) -> Path:
    """Files modified on 5, 6 and 7 March 2024 (one in a subfolder), all
    created on 2 January 2020."""
    monkeypatch.setattr(all_dates, "created_time", lambda path: datetime(
        CREATED.year, CREATED.month, CREATED.day, 12).timestamp())
    root = tmp_path / "dated"
    for rel, day in (("app.apk", 5), ("doc.pdf", 6), ("sub/setup.exe", 7)):
        _write(root / rel, rel.encode(), date(2024, 3, day))
    return root


@pytest.mark.real_dialogs
def test_all_dates_by_keyboard_created_then_undo(qtbot, make_window, dated, settings_path,
                                                 errors):
    before = _hashes(dated)
    w = make_window()
    seen = []
    _answer_each(qtbot, seen, (Qt.Key.Key_Right, Qt.Key.Key_Return),  # Continue
                 (Qt.Key.Key_Right, Qt.Key.Key_Return))  # Created
    _start(qtbot, w, dated, "all_dates", subfolders=True)
    assert [(s["title"], s["focus"], s["strip"], s["live"]) for s in seen] == [
        (files_flow.SUBFOLDERS_TITLE, "Cancel", ASK_STRIP, ASK_STRIP),
        (files_flow.DATE_TITLE, "Modified", CHOOSE_STRIP, CHOOSE_STRIP)]
    assert load_settings(settings_path).file_date == "created"
    _activate(qtbot, w)
    _go(qtbot, w)
    assert w.summary_page.heading_label.text() == "Ready to sort by Date (all files)"
    assert sorted(_hashes(dated)) == [
        "2020/01-January/02-01-2020/app.apk", "2020/01-January/02-01-2020/doc.pdf",
        "2020/01-January/02-01-2020/setup.exe"]

    _undo_by_u(qtbot, w)
    assert _hashes(dated) == before
    assert not (dated / "2020").exists()
    assert errors == []


@pytest.mark.real_dialogs
def test_esc_at_the_date_choice_goes_home(qtbot, make_window, dated, settings_path, errors):
    before = _hashes(dated)
    w = make_window(Settings(file_date="created"))
    seen = []
    _answer(qtbot, seen, Qt.Key.Key_Escape)  # no warning first: subfolders are off
    _start(qtbot, w, dated, "all_dates", subfolders=False)
    assert [(s["title"], s["focus"]) for s in seen] == [(files_flow.DATE_TITLE, "Created")]
    assert w.current_page() is w.home
    assert w._plan is None
    assert not w.busy
    assert load_settings(settings_path).file_date == "created"  # Esc changes nothing
    assert _hashes(dated) == before
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is w.mode_buttons["all_dates"])  # back on its tile
    assert errors == []


@pytest.mark.real_dialogs
def test_the_date_chosen_is_focused_next_time(qtbot, make_window, dated, settings_path,
                                              errors):
    w = make_window()
    seen = []
    _answer(qtbot, seen, Qt.Key.Key_Right, Qt.Key.Key_Return)  # Created
    _start(qtbot, w, dated, "all_dates", subfolders=False)
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    qtbot.waitUntil(lambda: _focused() is w.go_button)
    _key(qtbot, Qt.Key.Key_Escape)  # back Home, nothing moved
    w.close()

    # The app started again: Created is focused first.
    w2 = make_window(load_settings(settings_path))
    assert _focused() is w2.mode_buttons["all_dates"]  # the last mode's tile
    _answer(qtbot, seen, Qt.Key.Key_Escape)
    _key(qtbot, Qt.Key.Key_Return)
    assert [s["focus"] for s in seen] == ["Modified", "Created"]
    assert w2.current_page() is w2.home
    assert errors == []


# ----- No people ---------------------------------------------------------------------------

def _face() -> Face:
    v = np.zeros(512, np.float32)
    v[0] = 1.0
    return Face((2.0, 2.0, 10.0, 10.0), np.zeros((5, 2), np.float32), 0.9, v, (16, 16))


class FakeEngine:
    """Faces by file name (none if not named); records the photos looked at."""

    def __init__(self, faces: dict[str, list[Face]]) -> None:
        self.faces = faces
        self.calls: list[str] = []

    def analyse(self, path: Path) -> list[Face]:
        self.calls.append(Path(path).name)
        return list(self.faces.get(Path(path).name, []))


@pytest.fixture
def portraits(tmp_path, make_jpeg) -> Path:
    root = tmp_path / "portraits"
    for rel in ("me.jpg", "sea.jpg", "trip/hills.jpg"):
        make_jpeg(root / rel)
    _write(root / "doc.pdf", b"%PDF-1.4")
    return root


@pytest.mark.real_dialogs
def test_no_people_by_keyboard_then_undo(qtbot, make_window, portraits, errors):
    before = _hashes(portraits)
    engine = FakeEngine({"me.jpg": [_face()]})
    made_on = []

    def factory(use_gpu: bool) -> FakeEngine:
        made_on.append(threading.current_thread())
        return engine

    w = make_window()
    w.engine_factory = factory
    _start(qtbot, w, portraits, "no_people", subfolders=True)  # no warning for No people
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_page.heading_label.text() == "Ready to sort by No people"
    assert w.summary_label.text().splitlines() == [
        "2 photos without faces → No people", "1 photos with people stay"]
    _go(qtbot, w)
    assert made_on and threading.main_thread() not in made_on  # made on the worker
    assert sorted(_hashes(portraits)) == [
        "No people/hills.jpg", "No people/sea.jpg", "doc.pdf", "me.jpg"]

    _undo_by_u(qtbot, w)
    assert _hashes(portraits) == before
    assert not (portraits / "No people").exists()

    qtbot.waitUntil(lambda: _focused() is w.home_button)
    _key(qtbot, Qt.Key.Key_Return)  # Home, on No people's tile
    assert _focused() is w.mode_buttons["no_people"]
    _key(qtbot, Qt.Key.Key_Return)  # again: the faces come from the folder's cache
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert sorted(engine.calls) == ["hills.jpg", "me.jpg", "sea.jpg"]
    assert errors == []


def test_a_face_engine_that_cannot_be_made_goes_home(qtbot, make_window, portraits, errors):
    before = _hashes(portraits)

    def factory(use_gpu: bool):
        raise RuntimeError("no face model")

    w = make_window()
    w.engine_factory = factory
    _start_by_keyboard(qtbot, w, portraits, "no_people")
    qtbot.waitUntil(lambda: bool(errors))
    assert errors == ["Could not check the files: no face model"]
    assert w.current_page() is w.home
    assert w._plan is None
    assert not w.busy
    assert _hashes(portraits) == before
