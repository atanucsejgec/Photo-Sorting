"""Preview in the window, driven by keys: the tile, the page, N and M+digit,
Other folder…, the slot editor, Ctrl+Z, the arrows, the media keys, a move
that fails, leaving, the last file, an empty folder and 1024 × 600.

The files are tmp files made here (JPEGs with Pillow, a WAV with wave). The
folder picker is patched (preview_page.choose_folder); the app's own dialogs
are real where a test is marked real_dialogs, answered by keys.
"""

import threading
import wave
from pathlib import Path

import pytest
from keys import (
    _activate,
    _focused,
    _hold,
    _in_dialog,
    _key,
    _records,
    _start_by_keyboard,
    errors,  # noqa: F401  (fixtures, used by name)
    make_window,  # noqa: F401
    models_present,  # noqa: F401
    settings_path,  # noqa: F401
)
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QThreadPool
from PySide6.QtGui import QKeyEvent, QPainter, QPdfWriter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QPushButton

from fstree import tree
from photosort.journal import load_runs
from photosort.modes import preview as preview_mode
from photosort.modes.preview import NOT_NEEDED, outside_message
from photosort.settings import Settings, load_settings
from photosort.ui import icons
from photosort.ui import preview_page as pp
from photosort.ui import preview_viewers as pv
from photosort.ui.flows import preview as preview_flow
from photosort.ui.home_page import TILES
from photosort.ui.shortcuts import ShortcutStrip

WAIT_MS = 10000
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
PAGE_KEYS = ("←→ file · N not needed · M move to… · L edit folders · Ctrl+Z put back · "
             "Esc leave")
IMAGE_KEYS = "+− zoom · F fit · ↑↓ scroll"
ASK_STRIP = "←→ choose · Enter pick · Esc no/close"


def _jpeg(path: Path, colour: str = "red", size=(64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, "JPEG")
    return path


def _wav(path: Path, seconds: float = 30.0, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\0\0" * int(rate * seconds))
    return path


@pytest.fixture
def root(tmp_path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


@pytest.fixture
def three(root) -> Path:
    """a.jpg, b.jpg and c.jpg at the top of root."""
    for name, colour in (("a.jpg", "red"), ("b.jpg", "green"), ("c.jpg", "blue")):
        _jpeg(root / name, colour)
    return root


@pytest.fixture(autouse=True)
def _speed_back_to_1():
    """MediaViewer.speed is kept from file to file (class-wide): each test starts at 1×."""
    pv.MediaViewer.speed = 1.0
    yield
    pv.MediaViewer.speed = 1.0


def _settings(*slots) -> Settings:
    settings = Settings()
    settings.preview_folders = [str(s) if s is not None else None for s in slots] + [None] * (
        10 - len(slots))
    return settings


def _open(qtbot, w, folder: Path):
    """Start Preview on folder by keys; the page, with its first file shown and focused."""
    _start_by_keyboard(qtbot, w, folder, "preview")
    page = w.preview_page
    qtbot.waitUntil(lambda: w.current_page() is page, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: _focused() is page._box, timeout=WAIT_MS)
    return page


def _showing(qtbot, page, text: str) -> None:
    """The top line starts with text (the size and date come a moment later)."""
    qtbot.waitUntil(lambda: page.text().startswith(text), timeout=WAIT_MS)


def _idle(qtbot, page) -> None:
    """Every move queued is done."""
    qtbot.waitUntil(lambda: page._running is None and not page._queue, timeout=WAIT_MS)


def _key_event(key: Qt.Key) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "")


# ----- the tile ----------------------------------------------------------------------------

def test_preview_tile_after_pick_faces_and_hint(qtbot, make_window):
    keys = [key for key, _ in TILES]
    assert keys[keys.index("pick_faces") + 1] == "preview"
    assert dict(TILES)["preview"] == "Look at each file, move it"
    assert icons.MODE_ICONS["preview"] == "eye"
    assert not icons.icon("eye").isNull()
    w = make_window()
    tile = w.mode_buttons["preview"]
    assert tile.text() == "Preview"
    assert tile.hint == "Look at each file, move it"


# ----- N and M -----------------------------------------------------------------------------

def test_n_moves_to_not_needed_and_shows_next(qtbot, make_window, three, errors):
    w = make_window()
    page = _open(qtbot, w, three)
    stamp = (three / "a.jpg").stat()
    _showing(qtbot, page, "1 of 3 · a.jpg · ")
    qtbot.waitUntil(lambda: page.text() == (
        f"1 of 3 · a.jpg · {pv.size_text(stamp.st_size)} · {pv.date_text(stamp.st_mtime)}"),
        timeout=WAIT_MS)
    assert page.folder_text() == "photos"
    assert isinstance(page.viewer(), pv.ImageViewer)
    _key(qtbot, Qt.Key.Key_N)
    assert page.text().startswith("1 of 2 · b.jpg")  # at once, not after the move
    assert page.current() == "b.jpg"
    _idle(qtbot, page)
    assert (three / NOT_NEEDED / "a.jpg").is_file()
    assert not (three / "a.jpg").exists()
    assert page.session.counts() == (1, 0)
    assert _focused() is page._box
    assert errors == []


def test_m_then_digit_moves_to_slot(qtbot, make_window, three, errors):
    (three / "Trips").mkdir()
    w = make_window(_settings(three / "Trips", None, three / "Family" / "Kids"))
    page = _open(qtbot, w, three)
    assert [label.text() for label in page.slot_labels[:4]] == [
        "1  Trips", "2  —", "3  Family/Kids", "4  —"]
    assert page.slot_labels[0].objectName() == ""
    assert page.slot_labels[1].objectName() == "muted"
    _key(qtbot, Qt.Key.Key_M)
    assert page.overlay.isVisible()
    assert _focused() is page.overlay
    assert page.overlay.text() == ("1 Trips · 2 — · 3 Family/Kids · 4 — · 5 — · 6 — · 7 — · "
                                   "8 — · 9 — · 0 —")
    assert w.strip.text() == "1…9, 0 move to folder · Enter other folder… · Esc close"
    _key(qtbot, Qt.Key.Key_1)
    assert not page.overlay.isVisible()
    assert page.current() == "b.jpg"
    assert _focused() is page._box
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_3)  # missing, inside the root: made
    _idle(qtbot, page)
    assert (three / "Trips" / "a.jpg").is_file()
    assert (three / "Family" / "Kids" / "b.jpg").is_file()
    assert page.session.counts() == (0, 2)
    assert page.current() == "c.jpg"
    assert errors == []


def test_m_empty_slot_and_outside_slot_messages(qtbot, make_window, three, tmp_path, errors):
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    w = make_window(_settings(three / "Trips", elsewhere))
    page = _open(qtbot, w, three)
    before = tree(three)
    assert page.slot_labels[1].objectName() == "muted"  # greyed: outside the root
    assert page.slot_labels[1].text() == "2  Elsewhere"
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_4)
    assert page.message() == "Slot 4 is empty"
    assert page.overlay.isVisible()
    _key(qtbot, Qt.Key.Key_2)
    assert page.message() == "Elsewhere isn't inside this folder"
    _key(qtbot, Qt.Key.Key_0)
    assert page.message() == "Slot 0 is empty"
    _key(qtbot, Qt.Key.Key_Escape)  # closes the overlay; no move, no leaving
    assert not page.overlay.isVisible()
    assert w.current_page() is page
    assert page.current() == "a.jpg"
    assert _focused() is page._box
    assert tree(three) == before
    assert page.session.moved == []
    assert errors == []


def test_m_enter_other_folder_inside_root_adds_to_first_free_slot(
        qtbot, make_window, three, settings_path, monkeypatch, errors):
    new = three / "New" / "Deep"
    new.mkdir(parents=True)  # the picker's New Folder made it
    asked = []

    def pick(parent, title, start):
        asked.append((title, Path(start)))
        return new

    monkeypatch.setattr(pp, "choose_folder", pick)
    w = make_window(_settings(three / "Trips"))
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_Return)
    assert asked == [(pp.PICK_TITLE, three)]
    assert not page.overlay.isVisible()
    assert page.current() == "b.jpg"
    _idle(qtbot, page)
    assert (new / "a.jpg").is_file()
    assert page.slots()[:3] == [str(three / "Trips"), str(new), None]
    assert page.slot_labels[1].text() == "2  New/Deep"
    assert load_settings(settings_path).preview_folders[:3] == [
        str(three / "Trips"), str(new), None]
    # Picked again: it is listed already, so the list stays.
    _key(qtbot, Qt.Key.Key_M)
    page.overlay.other_button.click()
    _idle(qtbot, page)
    assert (new / "b.jpg").is_file()
    assert load_settings(settings_path).preview_folders[:3] == [
        str(three / "Trips"), str(new), None]
    assert page.message() == ""
    assert errors == []


def test_other_folder_with_the_list_full_moves_and_says_so(qtbot, make_window, three,
                                                         monkeypatch, errors):
    full = [three / f"S{n}" for n in range(10)]
    new = three / "New"
    new.mkdir()
    monkeypatch.setattr(pp, "choose_folder", lambda parent, title, start: new)
    w = make_window(_settings(*full))
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_Return)
    assert page.message() == "The list is full; edit it with L"
    _idle(qtbot, page)
    assert (new / "a.jpg").is_file()
    assert page.slots() == [str(f) for f in full]
    assert errors == []


def test_other_folder_outside_root_refused(qtbot, make_window, three, tmp_path, monkeypatch,
                                           errors):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(pp, "choose_folder", lambda parent, title, start: out)
    w = make_window()
    page = _open(qtbot, w, three)
    before = tree(three)
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_Return)
    assert page.message() == outside_message(three)
    assert page.message() == ("Pick a folder inside photos — moves stay inside the folder "
                              "you opened, so Undo can put them back.")
    assert page.current() == "a.jpg"
    assert _focused() is page._box
    qtbot.wait(50)
    assert tree(three) == before
    assert list(out.iterdir()) == []
    assert page.slots() == [None] * 10
    assert errors == []


# ----- the slot editor ---------------------------------------------------------------------

@pytest.mark.real_dialogs
def test_slot_editor_add_remove_reorder_renumbers_and_saves(qtbot, make_window, three,
                                                            settings_path, tmp_path,
                                                            monkeypatch, errors):
    a, b, c, d = (three / name for name in ("A", "B", "C", "D"))
    elsewhere = tmp_path / "Elsewhere"
    picks = [elsewhere, d]
    monkeypatch.setattr(pp, "choose_folder", lambda parent, title, start: picks.pop(0))
    w = make_window(_settings(a, b, c))
    page = _open(qtbot, w, three)
    seen = {}

    def act(dialog):
        qtbot.waitUntil(lambda: _focused() is dialog.list_widget)
        rows = lambda: [dialog.list_widget.item(n).text() for n in range(4)]  # noqa: E731
        assert rows() == ["1  A", "2  B", "3  C", "4  —"]
        seen["strip"] = dialog.findChild(ShortcutStrip).text()
        _key(qtbot, Qt.Key.Key_Down)  # B
        _key(qtbot, Qt.Key.Key_Up, ALT)  # B to 1, A to 2
        assert rows() == ["1  B", "2  A", "3  C", "4  —"]
        _key(qtbot, Qt.Key.Key_Down)  # A, now 2
        _key(qtbot, Qt.Key.Key_Delete)  # C and the rest move up
        assert rows() == ["1  B", "2  C", "3  —", "4  —"]
        _key(qtbot, Qt.Key.Key_A)  # Elsewhere: refused
        seen["refused"] = dialog.message_label.text()
        _key(qtbot, Qt.Key.Key_A)  # D, into the first empty slot (2 is taken)
        assert rows() == ["1  B", "2  C", "3  D", "4  —"]
        seen["live"] = _records(_focused(), dialog)
        _key(qtbot, Qt.Key.Key_Return)  # OK

    _in_dialog(qtbot, act)
    _key(qtbot, Qt.Key.Key_L)
    assert seen["refused"] == outside_message(three)
    assert seen["strip"] == ("↑↓ choose · Enter OK · Insert/A add · Del remove · "
                             "Alt+↓ move down · Esc cancel · Tab/Shift+Tab area")
    assert "Alt+↑ move up" in seen["live"]
    expected = [str(b), str(c), str(d)] + [None] * 7
    assert page.slots() == expected
    assert load_settings(settings_path).preview_folders == expected
    assert [label.text() for label in page.slot_labels[:4]] == ["1  B", "2  C", "3  D", "4  —"]
    _activate(qtbot, w)
    assert errors == []


@pytest.mark.real_dialogs
def test_slot_editor_cancel_changes_nothing(qtbot, make_window, three, settings_path, errors):
    a = three / "A"
    w = make_window(_settings(a))
    page = _open(qtbot, w, three)

    def act(dialog):
        qtbot.waitUntil(lambda: _focused() is dialog.list_widget)
        _key(qtbot, Qt.Key.Key_Delete)
        _key(qtbot, Qt.Key.Key_Escape)

    _in_dialog(qtbot, act)
    _key(qtbot, Qt.Key.Key_L)
    assert page.slots()[0] == str(a)
    assert not settings_path.exists() or load_settings(settings_path).preview_folders[0] in (
        str(a), None)
    assert errors == []


# ----- Ctrl+Z and the arrows ---------------------------------------------------------------

def test_ctrl_z_puts_back_and_shows_file_again(qtbot, make_window, three, errors):
    before = tree(three)
    w = make_window()
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)  # a
    _key(qtbot, Qt.Key.Key_N)  # b
    _showing(qtbot, page, "1 of 1 · c.jpg")
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    _showing(qtbot, page, "1 of 2 · b.jpg")  # put back where it was, and shown again
    assert (three / "b.jpg").is_file()
    assert isinstance(page.viewer(), pv.ImageViewer)
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    _showing(qtbot, page, "1 of 3 · a.jpg")
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    qtbot.waitUntil(lambda: page.message() == "Nothing to put back", timeout=WAIT_MS)
    assert page.text().startswith("1 of 3 · a.jpg")
    _idle(qtbot, page)
    assert page.session.counts() == (0, 0)
    assert tree(three) == before | {NOT_NEEDED: "dir"}
    assert errors == []


@pytest.mark.real_dialogs
def test_everything_put_back_then_leave_goes_home(qtbot, make_window, three, errors):
    """Nothing stays moved: no Done page. The run is in History, done, harmless."""
    before = tree(three)
    w = make_window()
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)
    _showing(qtbot, page, "1 of 2 · b.jpg")
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    _showing(qtbot, page, "1 of 3 · a.jpg")
    _idle(qtbot, page)
    _in_dialog(qtbot, lambda dialog: _key(qtbot, Qt.Key.Key_Return))  # Leave
    _key(qtbot, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: w.current_page() is w.home, timeout=WAIT_MS)
    assert tree(three) == before  # the Not needed the session made is tidied
    [run] = load_runs(three)
    assert (run.mode, run.status) == ("preview", "done")
    assert errors == []


def test_arrows_navigate_without_moving(qtbot, make_window, three, errors):
    before = tree(three)
    w = make_window()
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_Left)  # nothing before the first
    _showing(qtbot, page, "1 of 3 · a.jpg")
    _key(qtbot, Qt.Key.Key_Right)
    _showing(qtbot, page, "2 of 3 · b.jpg")
    _key(qtbot, Qt.Key.Key_Right)
    _showing(qtbot, page, "3 of 3 · c.jpg")
    _key(qtbot, Qt.Key.Key_Left)
    _showing(qtbot, page, "2 of 3 · b.jpg")
    assert w.current_page() is page
    assert tree(three) == before
    assert load_runs(three) == []
    assert errors == []


def test_shift_arrows_seek_and_s_cycles_speed_on_audio(qtbot, make_window, root, errors):
    _wav(root / "song.wav")
    _jpeg(root / "z.jpg")
    w = make_window()
    page = _open(qtbot, w, root)
    viewer = page.viewer()
    assert isinstance(viewer, pv.MediaViewer) and not viewer.video
    assert w.strip.text() == f"Space play/pause · Shift+←→ ±10 s · S speed · {PAGE_KEYS}"
    qtbot.waitUntil(lambda: viewer.player.duration() > 0, timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Right, SHIFT)
    qtbot.waitUntil(lambda: viewer.player.position() >= 9000, timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Left, SHIFT)
    qtbot.waitUntil(lambda: viewer.player.position() <= 1000, timeout=WAIT_MS)
    assert page.current() == "song.wav"  # Shift+←/→ never changes the file
    _key(qtbot, Qt.Key.Key_S)
    assert pv.MediaViewer.speed == 2.0
    assert [b.isChecked() for b in viewer.speed_buttons] == [False, True, False]
    _key(qtbot, Qt.Key.Key_S)
    _key(qtbot, Qt.Key.Key_S)
    assert pv.MediaViewer.speed == 1.0
    _key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: viewer.player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState, timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Right)  # the next file: the sound stops, its file let go
    _showing(qtbot, page, "2 of 2 · z.jpg")
    assert viewer.player.source().isEmpty()
    assert viewer.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert errors == []


# ----- the shown file changing under the overlay or the picker ----------------------------

def _slow_moves(monkeypatch) -> threading.Event:
    """Moves wait for the gate (set it to let them through)."""
    gate = threading.Event()
    real = preview_mode.move_file

    def slow(src, dst):
        gate.wait(5)
        real(src, dst)

    monkeypatch.setattr(preview_mode, "move_file", slow)
    return gate


def test_a_put_back_landing_while_the_overlay_is_open_closes_it(qtbot, make_window, three,
                                                               monkeypatch, errors):
    w = make_window(_settings(three / "Trips"))
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)  # a
    _idle(qtbot, page)
    gate = _slow_moves(monkeypatch)
    _key(qtbot, Qt.Key.Key_Z, CTRL)  # waits at the gate
    _key(qtbot, Qt.Key.Key_M)  # on b
    assert page.overlay.isVisible()
    gate.set()
    _showing(qtbot, page, "1 of 3 · a.jpg")  # a is back, and shown
    assert not page.overlay.isVisible()
    assert _focused() is page._box
    assert w.strip.text() == f"{IMAGE_KEYS} · {PAGE_KEYS}"
    # A late slot pick for b (a click already queued) moves nothing.
    page.overlay.picked.emit(0)
    assert page.message() == "The file changed — press M again"
    _key(qtbot, Qt.Key.Key_1)  # a digit with the overlay closed: nothing
    _key(qtbot, Qt.Key.Key_Right)  # the keys work
    _showing(qtbot, page, "2 of 3 · b.jpg")
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_1)
    _idle(qtbot, page)
    assert (three / "Trips" / "b.jpg").is_file()
    assert (three / "a.jpg").is_file()
    assert errors == []


def test_a_viewer_failing_while_the_overlay_is_open_closes_it(qtbot, make_window, root,
                                                             errors):
    (root / "broken.jpg").write_bytes(b"not a jpeg")
    w = make_window()
    page = _open(qtbot, w, root)
    _key(qtbot, Qt.Key.Key_M)  # before the decode fails, a moment later
    qtbot.waitUntil(lambda: isinstance(page.viewer(), pv.InfoCard), timeout=WAIT_MS)
    assert not page.overlay.isVisible()
    assert _focused() is page._box
    _key(qtbot, Qt.Key.Key_M)  # the keys work
    assert page.overlay.isVisible()
    _key(qtbot, Qt.Key.Key_Escape)
    assert errors == []


def test_a_put_back_landing_while_the_picker_is_open_moves_nothing(qtbot, make_window, three,
                                                                  monkeypatch, errors):
    (three / "Trips").mkdir()
    w = make_window()
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)  # a
    _idle(qtbot, page)
    gate = _slow_moves(monkeypatch)

    def picker(parent, title, start):
        assert page.current() == "b.jpg"
        gate.set()
        qtbot.waitUntil(lambda: page.current() == "a.jpg", timeout=WAIT_MS)  # put back
        return three / "Trips"

    monkeypatch.setattr(pp, "choose_folder", picker)
    _key(qtbot, Qt.Key.Key_Z, CTRL)
    _key(qtbot, Qt.Key.Key_M)  # on b
    _key(qtbot, Qt.Key.Key_Return)
    _idle(qtbot, page)
    assert page.message() == "The file changed — press M again"
    assert list((three / "Trips").iterdir()) == []
    assert (three / "a.jpg").is_file() and (three / "b.jpg").is_file()
    assert page.slots() == [None] * 10  # nothing moved: nothing listed
    assert page.current() == "a.jpg"
    assert errors == []


def test_a_failure_stays_visible_after_a_later_note(qtbot, make_window, three, monkeypatch,
                                                    errors):
    def move_file(src, dst):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr(preview_mode, "move_file", move_file)
    w = make_window()
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)
    _key(qtbot, Qt.Key.Key_Z, CTRL)  # nothing moved: nothing to put back
    qtbot.waitUntil(lambda: "Nothing to put back" in page.message(), timeout=WAIT_MS)
    assert page.message().startswith("Couldn't move a.jpg: ")
    assert "Permission denied" in page.message()
    assert errors == []


# ----- moving a file that is shown ---------------------------------------------------------

def test_playing_file_released_before_move(qtbot, make_window, root, monkeypatch, errors):
    """Review Focus 3: the player lets go of the file before it is moved."""
    _wav(root / "song.wav")
    _jpeg(root / "z.jpg")
    w = make_window()
    page = _open(qtbot, w, root)
    viewer = page.viewer()
    qtbot.waitUntil(lambda: viewer.player.duration() > 0, timeout=WAIT_MS)
    _key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: viewer.player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState, timeout=WAIT_MS)
    events = []
    real_release, real_move = viewer.release, preview_mode.move_file

    def release():
        events.append("release")
        real_release()

    def move_file(src, dst):
        events.append(("move", Path(src).name, threading.current_thread()
                       is threading.main_thread()))
        real_move(src, dst)

    monkeypatch.setattr(viewer, "release", release)
    monkeypatch.setattr(preview_mode, "move_file", move_file)
    _key(qtbot, Qt.Key.Key_N)
    _idle(qtbot, page)
    assert events[0] == "release"
    assert ("move", "song.wav", False) in events  # off the GUI thread
    assert events.index("release") < events.index(("move", "song.wav", False))
    assert viewer.player.source().isEmpty()
    assert viewer.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert (root / NOT_NEEDED / "song.wav").is_file()
    assert page.current() == "z.jpg"
    qtbot.wait(100)
    assert errors == []


def test_move_failure_message_and_file_stays(qtbot, make_window, three, monkeypatch, errors):
    """Review Focus 4: the file is back in the list where it was; the session goes on."""
    real = preview_mode.move_file

    def move_file(src, dst):
        if Path(src).name == "a.jpg":
            raise PermissionError(13, "Permission denied", str(dst))
        real(src, dst)

    monkeypatch.setattr(preview_mode, "move_file", move_file)
    w = make_window()
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)  # a fails
    qtbot.waitUntil(lambda: page.message().startswith("Couldn't move a.jpg: "),
                    timeout=WAIT_MS)
    assert "Permission denied" in page.message()
    assert page.files == ["a.jpg", "b.jpg", "c.jpg"]
    assert page.current() == "b.jpg"  # still shown
    _showing(qtbot, page, "2 of 3 · b.jpg")
    _key(qtbot, Qt.Key.Key_N)  # b moves
    _idle(qtbot, page)
    assert (three / NOT_NEEDED / "b.jpg").is_file()
    assert (three / "a.jpg").is_file()
    assert page.files == ["a.jpg", "c.jpg"]
    _key(qtbot, Qt.Key.Key_Left)
    _showing(qtbot, page, "1 of 2 · a.jpg")
    assert page.session.counts() == (1, 0)
    assert errors == []


def test_a_file_gone_before_its_move_says_so_and_stays_listed(qtbot, make_window, three,
                                                             errors):
    w = make_window()
    page = _open(qtbot, w, three)
    (three / "a.jpg").unlink()
    _key(qtbot, Qt.Key.Key_N)
    qtbot.waitUntil(lambda: page.message().startswith("Couldn't move a.jpg: "),
                    timeout=WAIT_MS)
    assert page.files == ["a.jpg", "b.jpg", "c.jpg"]
    assert page.current() == "b.jpg"
    _key(qtbot, Qt.Key.Key_N)
    _idle(qtbot, page)
    assert (three / NOT_NEEDED / "b.jpg").is_file()
    assert errors == []


# ----- ending ------------------------------------------------------------------------------

@pytest.mark.real_dialogs
def test_leave_finishes_run_done_page_counts_and_undo_restores_bytes(
        qtbot, make_window, three, errors):
    (three / "Trips").mkdir()
    before = tree(three)
    w = make_window(_settings(three / "Trips"))
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)  # a
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_1)  # b to Trips
    _showing(qtbot, page, "1 of 1 · c.jpg")
    seen = []

    def act(dialog):
        qtbot.waitUntil(lambda: _focused() is not None and _focused().window() is dialog)
        seen.append((dialog.windowTitle(), _focused().text(),
                     dialog.findChild(ShortcutStrip).text()))
        _key(qtbot, Qt.Key.Key_Return)

    _in_dialog(qtbot, act)
    _key(qtbot, Qt.Key.Key_Escape)
    assert seen == [("Leave Preview?", "Leave", ASK_STRIP)]
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: w.current_page() is w.done_page, timeout=WAIT_MS)
    assert w.done_label.text() == "Moved 2 files: 1 to Not needed, 1 to folders"
    assert page.session is None
    [run] = load_runs(three)
    assert (run.mode, run.status, run.moved) == ("preview", "done", 2)
    assert (three / "c.jpg").is_file()

    undo_seen = []

    def answer_undo(dialog):
        qtbot.waitUntil(lambda: _focused() is not None and _focused().window() is dialog)
        undo_seen.append(_focused().text())
        _key(qtbot, Qt.Key.Key_Right)
        _key(qtbot, Qt.Key.Key_Return)

    qtbot.waitUntil(lambda: _focused() is w.home_button)
    _in_dialog(qtbot, answer_undo)
    _key(qtbot, Qt.Key.Key_U)
    assert undo_seen == ["Cancel"]
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text(), timeout=WAIT_MS)
    assert tree(three) == before
    assert not (three / NOT_NEEDED).exists()
    assert errors == []


@pytest.mark.real_dialogs
def test_stay_keeps_the_preview_going(qtbot, make_window, three, errors):
    w = make_window()
    page = _open(qtbot, w, three)
    _in_dialog(qtbot, lambda dialog: _key(qtbot, Qt.Key.Key_Escape))  # Esc: Stay
    _key(qtbot, Qt.Key.Key_Escape)
    _activate(qtbot, w)
    assert w.current_page() is page
    assert page.session is not None
    _key(qtbot, Qt.Key.Key_Right)
    _showing(qtbot, page, "2 of 3 · b.jpg")
    assert errors == []


def test_last_file_ends_session(qtbot, make_window, root, errors):
    _jpeg(root / "a.jpg")
    _jpeg(root / "sub" / "b.jpg")  # not listed: subfolders are off
    w = make_window()
    page = _open(qtbot, w, root)
    _showing(qtbot, page, "1 of 1 · a.jpg")
    _key(qtbot, Qt.Key.Key_N)
    qtbot.waitUntil(lambda: w.current_page() is w.done_page, timeout=WAIT_MS)
    assert w.done_label.text() == ("That was the last file.\n"
                                   "Moved 1 file: 1 to Not needed, 0 to folders")
    assert (root / NOT_NEEDED / "a.jpg").is_file()
    assert [r.status for r in load_runs(root)] == ["done"]
    assert errors == []


def test_moving_on_past_the_last_file_with_nothing_moved_goes_home(qtbot, make_window, three,
                                                                   errors):
    before = tree(three)
    w = make_window()
    page = _open(qtbot, w, three)
    for _ in range(2):
        _key(qtbot, Qt.Key.Key_Right)
    _showing(qtbot, page, "3 of 3 · c.jpg")
    _hold(Qt.Key.Key_Right)  # a held →'s repeats never run on past the last file
    assert w.current_page() is page
    _key(qtbot, Qt.Key.Key_Right)
    qtbot.waitUntil(lambda: w.current_page() is w.home, timeout=WAIT_MS)
    assert tree(three) == before
    assert load_runs(three) == []
    assert errors == []


def test_closing_the_window_does_the_queued_moves_and_finishes(qtbot, make_window, three,
                                                             monkeypatch, errors):
    gate = threading.Event()
    real = preview_mode.move_file

    def slow(src, dst):
        gate.wait(5)
        real(src, dst)

    monkeypatch.setattr(preview_mode, "move_file", slow)
    w = make_window()
    page = _open(qtbot, w, three)
    _key(qtbot, Qt.Key.Key_N)
    _key(qtbot, Qt.Key.Key_N)  # queued behind a, which waits
    assert page._queue
    threading.Timer(0.2, gate.set).start()
    w.close()
    assert (three / NOT_NEEDED / "a.jpg").is_file()
    assert (three / NOT_NEEDED / "b.jpg").is_file()
    [run] = load_runs(three)
    assert (run.status, run.moved) == ("done", 2)
    assert page.session is None
    assert errors == []


def test_closing_the_window_lets_go_of_the_pdf_before_waiting_for_the_pool(
        qtbot, make_window, root, monkeypatch, errors):
    writer = QPdfWriter(str(root / "a.pdf"))
    painter = QPainter(writer)
    for n in range(20):
        if n:
            writer.newPage()
        painter.drawText(100, 100, f"page {n + 1}")
    painter.end()
    w = make_window()
    page = _open(qtbot, w, root)
    viewer = page._box.viewer
    assert isinstance(viewer, pv.PdfViewer)
    qtbot.waitUntil(lambda: 1 in viewer.rendered_pages(), timeout=WAIT_MS)
    events = []
    real_release, real_wait = pv.PdfViewer.release, QThreadPool.waitForDone
    real_render = pv.PdfiumFile.render

    def release(self):
        events.append("release")
        real_release(self)

    def wait(self, *a):
        events.append("wait")
        return real_wait(self, *a)

    def render(self, *a, **k):
        drawn = real_render(self, *a, **k)
        events.append("drawn" if drawn is not None else "skipped")
        return drawn

    monkeypatch.setattr(pv.PdfViewer, "release", release)
    monkeypatch.setattr(QThreadPool, "waitForDone", wait)
    monkeypatch.setattr(pv.PdfiumFile, "render", render)
    viewer.handle_key(_key_event(Qt.Key.Key_PageDown))  # renders ahead get queued...
    viewer._update_pages()
    w.close()  # ...and are let go before the window waits for the pool
    assert "release" in events and "wait" in events
    assert events.index("release") < events.index("wait")
    assert "drawn" not in events[events.index("release"):]
    assert viewer._file is None
    assert errors == []


def test_empty_folder_says_no_files(qtbot, make_window, root, errors):
    (root / "empty sub").mkdir()
    w = make_window()
    _start_by_keyboard(qtbot, w, root, "preview")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page, timeout=WAIT_MS)
    assert w.summary_label.text() == preview_flow.NO_FILES == "No files to preview"
    assert not w.go_button.isEnabled()
    assert load_runs(root) == []
    assert errors == []


def test_the_listing_brings_old_records_in_and_skips_slots_and_not_needed(
        qtbot, make_window, root, monkeypatch, errors):
    _jpeg(root / "a.jpg")
    _jpeg(root / NOT_NEEDED / "old.jpg")
    _jpeg(root / "Trips" / "t.jpg")
    _jpeg(root / "sub" / "s.jpg")
    from photosort import migrate
    from photosort.ui.worker import Task

    first = []
    real_start = Task.start

    def start(task):
        first.extend(task._first)
        real_start(task)

    monkeypatch.setattr(Task, "start", start)
    w = make_window(_settings(root / "Trips"))
    w.home.subfolders_switch.setChecked(True)
    monkeypatch.setattr(preview_flow.dialogs, "ask", lambda *a, **k: True)  # Continue
    page = _open(qtbot, w, root)
    assert (migrate.bring_in, (root,)) in first
    assert page.files == ["a.jpg", "sub/s.jpg"]
    assert errors == []


def test_cancel_at_the_subfolders_question_goes_home(qtbot, make_window, root, monkeypatch,
                                                    errors):
    _jpeg(root / "a.jpg")
    asked = []

    def ask(parent, title, text, yes, no, default):
        asked.append((title, yes, no, default))
        return False

    monkeypatch.setattr(preview_flow.dialogs, "ask", ask)
    w = make_window()
    w.home.subfolders_switch.setChecked(True)
    _start_by_keyboard(qtbot, w, root, "preview")
    assert asked == [("Include subfolders?", "Continue", "Cancel", "no")]
    assert w.current_page() is w.home
    assert w.preview_page.session is None
    assert errors == []


# ----- the look ----------------------------------------------------------------------------

def test_fits_1024x600_and_strip_lists_keys(qtbot, make_window, root, errors):
    _jpeg(root / "a.jpg", size=(1600, 1200))
    (root / "notes.txt").write_text("hello\n")
    (root / "z.zip").write_bytes(b"PK")
    w = make_window(_settings(root / "Trips", root / "A very long folder name indeed" / "and"
                              / "deeper still"), size=(1024, 600))
    page = _open(qtbot, w, root)
    assert w.strip.text() == f"{IMAGE_KEYS} · {PAGE_KEYS}"
    assert _records(_focused(), page) == w.strip.text()
    _key(qtbot, Qt.Key.Key_Tab)  # nowhere else to go: the sidebar is locked
    assert _focused() is page._box
    assert w.minimumSizeHint().width() <= 1024 and w.minimumSizeHint().height() <= 600
    room = w.stack.size()
    assert page.minimumSizeHint().width() <= room.width()
    assert page.minimumSizeHint().height() <= room.height()
    window = w.rect()
    strip_top = w.strip.mapTo(w, QPoint(0, 0)).y()
    for widget in (page.title_label, page.folder_label, page.edit_button, page.viewers,
                   *page.slot_labels):
        assert widget.isVisible(), widget
        rect = QRect(widget.mapTo(w, QPoint(0, 0)), widget.size())
        assert window.contains(rect), widget
        assert rect.bottom() < strip_top, widget
    for button in page.findChildren(QPushButton):
        if button.isVisible():
            assert button.width() >= button.minimumSizeHint().width(), button.text()
    assert page.viewers.height() >= 250
    _key(qtbot, Qt.Key.Key_M)
    overlay = QRect(page.overlay.mapTo(w, QPoint(0, 0)), page.overlay.size())
    assert window.contains(overlay)
    assert page.overlay.other_button.width() >= page.overlay.other_button.minimumSizeHint(
    ).width()
    _key(qtbot, Qt.Key.Key_Escape)
    _key(qtbot, Qt.Key.Key_Right)
    _showing(qtbot, page, "2 of 3 · notes.txt")
    assert w.strip.text() == f"+− text size · ↑↓ scroll · PgUp/PgDn page · {PAGE_KEYS}"
    _key(qtbot, Qt.Key.Key_Right)
    _showing(qtbot, page, "3 of 3 · z.zip")
    assert w.strip.text() == f"O open with the system app · {PAGE_KEYS}"
    assert isinstance(page.viewer(), pv.InfoCard)
    assert errors == []


def test_a_photo_that_cant_be_shown_shows_the_info_card(qtbot, make_window, root, errors):
    (root / "broken.jpg").write_bytes(b"not a jpeg")
    w = make_window()
    page = _open(qtbot, w, root)
    qtbot.waitUntil(lambda: isinstance(page.viewer(), pv.InfoCard), timeout=WAIT_MS)
    assert pv.CANT_SHOW_IMAGE in page.viewer().text()
    assert _focused() is page._box
    assert errors == []
