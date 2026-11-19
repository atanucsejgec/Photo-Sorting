"""Pick faces: the people grid, the folders made from it, and the flow round it."""

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QPushButton

from keys import (
    _activate,
    _focused,
    _key,
    errors,  # noqa: F401  (fixtures, used by name)
    make_window,  # noqa: F401
    models_present,  # noqa: F401
    settings_path,  # noqa: F401
)
from photosort.faces import Face
from photosort.hashing import sha256_file
from photosort.modes import MODE_SPECS, MODES
from photosort.thresholds import FACE_ENGINE_FAILS
from photosort.ui import dialogs, main_window
from photosort.ui.flows import face as face_flow
from photosort.ui.flows import pick_faces as pick_flow
from photosort.ui.pick_faces_page import PersonTile

CTRL = Qt.KeyboardModifier.ControlModifier
STRIP = ("Arrows move · Space pick · Enter open · N name · Del remove folder · "
         "Ctrl+Enter continue · Esc leave · Tab/Shift+Tab area")


def _face(axis: int, seed: int) -> Face:
    """A face of the person on axis: about 0.7 alike to the person's other faces."""
    rng = np.random.default_rng(seed)
    v = np.zeros(512)
    v[axis] = 1.0
    v += rng.normal(0.0, 0.03, 512)
    v = (v / np.linalg.norm(v)).astype(np.float32)
    return Face((20.0, 20.0, 80.0, 80.0), np.zeros((5, 2), np.float32), 0.9, v, (200, 200))


class FakeEngine:
    """Faces by file name (none if not named)."""

    def __init__(self, faces: dict[str, list[Face]] | None = None) -> None:
        self.faces = faces or {}
        self.calls: list[str] = []

    def analyse(self, path: Path) -> list[Face]:
        self.calls.append(Path(path).name)
        return list(self.faces.get(Path(path).name, []))


# Ann is in three photos, Ben in two (one of them with Ann), Cat and Dev in one each.
PEOPLE = {
    "a1.jpg": [(0, 1)], "a2.jpg": [(0, 2)], "ab.jpg": [(0, 3), (1, 4)],
    "b1.jpg": [(1, 5)], "c1.jpg": [(2, 6)], "d1.jpg": [(3, 7)],
}


@pytest.fixture
def root(tmp_path, make_jpeg) -> Path:
    folder = tmp_path / "photos"
    for name in [*PEOPLE, "empty.jpg"]:
        make_jpeg(folder / name, size=(200, 200))
    (folder / "clip.mp4").write_bytes(b"not really a video")
    return folder


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine({name: [_face(axis, seed) for axis, seed in faces]
                       for name, faces in PEOPLE.items()})


@pytest.fixture
def asked(monkeypatch):
    class Asked(list):
        answer = {"yes": True}

    questions = Asked()

    def ask(parent, title, text, yes, no, default="no"):
        questions.append(title)
        return questions.answer["yes"]

    monkeypatch.setattr(dialogs, "ask", ask)
    return questions


def _start(qtbot, w, root: Path, engine) -> None:
    w.engine_factory = lambda use_gpu: engine
    w.folder_edit.setText(str(root))
    w.subfolders_check.setChecked(False)
    w.mode_buttons["pick_faces"].click()


def _on_grid(qtbot, w, root, engine):
    _start(qtbot, w, root, engine)
    page = w.pick_faces_page
    qtbot.waitUntil(lambda: w.current_page() is page)
    qtbot.waitUntil(lambda: isinstance(_focused(), PersonTile))
    return page


def _counts(tiles) -> list[str]:
    return [t.count_label.text() for t in tiles]


def _status(tiles) -> list[str]:
    return [t.status_label.text() for t in tiles]


def _type_name(qtbot, page, name: str) -> None:
    _key(qtbot, Qt.Key.Key_N)
    assert _focused() is page.name_edit
    page.name_edit.clear()
    if name:
        qtbot.keyClicks(page.name_edit, name)
    _key(qtbot, Qt.Key.Key_Return)


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


# ----- the mode ----------------------------------------------------------------------------

def test_the_tile_comes_after_face_with_its_hint(make_window, monkeypatch):
    keys = [key for key, _, _ in MODES]
    assert keys[keys.index("face") + 1] == "pick_faces"
    assert ("pick_faces", "Pick faces", True) in MODES
    assert MODE_SPECS["pick_faces"].kind == "pickfaces"
    assert pick_flow.MODELS == face_flow.MODELS
    w = make_window()
    tiles = list(w.mode_buttons)
    assert tiles[tiles.index("face") + 1] == "pick_faces"
    tile = w.mode_buttons["pick_faces"]
    assert (tile.name, tile.hint) == ("Pick faces", "Click faces, make folders")


def test_the_face_models_are_made_sure_of_first(qtbot, make_window, root, engine,
                                                monkeypatch):
    wanted = []
    monkeypatch.setattr(main_window, "ensure_models",
                        lambda parent, keys: wanted.append(list(keys)) or False)
    w = make_window()
    _start(qtbot, w, root, engine)
    assert wanted == [["face_detection", "face_recognition"]]
    assert w.current_page() is w.home
    assert engine.calls == []


# ----- the grid ----------------------------------------------------------------------------

def test_grid_shows_people_most_photos_first_and_seen_once_section(qtbot, make_window, root,
                                                                   engine):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    assert sorted(engine.calls) == sorted([*PEOPLE, "empty.jpg"])  # photos only
    assert page.heading_label.text() == "People in this folder"
    assert _counts(page.tiles) == ["in 3 photos", "in 2 photos"]
    assert _status(page.tiles) == ["", ""]
    assert _focused() is page.tiles[0]
    # The face crops arrive from the pool.
    qtbot.waitUntil(lambda: all(t.crop_label.pixmap() is not None
                                and not t.crop_label.pixmap().isNull() for t in page.tiles))
    # The people seen once wait, closed, under their header at the end.
    header = page.once_header
    assert header.isVisible()
    assert header.text_label.text() == "Seen once (2)"
    assert page.once_tiles == []
    _key(qtbot, Qt.Key.Key_Right)
    assert _focused() is page.tiles[1]
    _key(qtbot, Qt.Key.Key_Down)  # the header, on its own row
    assert _focused() is header
    _key(qtbot, Qt.Key.Key_Return)  # opens it
    assert _counts(page.once_tiles) == ["in 1 photo", "in 1 photo"]
    assert header.text_label.text().startswith("Seen once (2)")
    _key(qtbot, Qt.Key.Key_Down)
    assert _focused() is page.once_tiles[0]
    _key(qtbot, Qt.Key.Key_Space)
    assert page.once_tiles[0].picked
    _key(qtbot, Qt.Key.Key_Up)
    _key(qtbot, Qt.Key.Key_Space)  # closes it again; the pick is kept
    assert all(not t.isVisible() for t in page.once_tiles)
    assert page.once_tiles[0].picked


def test_select_two_tiles_add_folder_badges_them(qtbot, make_window, root, engine):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_Space)
    assert page.tiles[0].picked
    assert page.tiles[0].status_label.text() == "✓ Picked"
    _key(qtbot, Qt.Key.Key_Space)  # a second Space unpicks
    assert not page.tiles[0].picked
    _key(qtbot, Qt.Key.Key_Space)
    qtbot.mouseClick(page.tiles[1], Qt.MouseButton.LeftButton)  # a click picks too
    assert [t.picked for t in page.tiles] == [True, True]
    _type_name(qtbot, page, "Friend")
    assert _status(page.tiles) == ["→ Friend", "→ Friend"]
    assert [t.picked for t in page.tiles] == [False, False]
    assert len(page.folder_rows) == 1
    row = page.folder_rows[0]
    assert row.label.text() == "Friend: 2 people, 4 photos"
    assert row.remember_check.text() == "Remember on the People page"
    assert not row.remember_check.isChecked()
    assert page.name_edit.text() == ""
    assert page.message_label.text() == ""
    assert isinstance(_focused(), PersonTile)  # back on the grid
    assert w.strip.text() == STRIP
    # A tile in a folder can't be picked for another.
    page.navigator.focus(0)
    _key(qtbot, Qt.Key.Key_Space)
    assert not page.tiles[0].picked
    qtbot.mouseClick(page.tiles[0], Qt.MouseButton.LeftButton)
    assert not page.tiles[0].picked
    assert page.tiles[0].status_label.text() == "→ Friend"
    # Nothing picked: Add says so.
    _type_name(qtbot, page, "Family")
    assert page.message_label.text() == "Pick one or more faces first"
    assert len(page.folder_rows) == 1


def test_folder_name_refusals(qtbot, make_window, root, engine):
    (root / "Notes").write_bytes(b"a file, not a folder")
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_Space)
    _type_name(qtbot, page, "Friend")
    page.navigator.focus(1)
    _key(qtbot, Qt.Key.Key_Space)
    for name, message in (
        ("", "Type a name."),
        ("Ann & Ben", 'A name can\'t contain " & ". Photo Sorter uses it for photos '
                      "with several people."),
        ("friend", "There is already a folder called friend in your list"),
        ("Notes", "A file named Notes is already there"),
        (".photosort", "Photo Sorter uses that name for its own folder. Pick another name."),
    ):
        _type_name(qtbot, page, name)
        assert page.message_label.text() == message, name
        assert _focused() is page.name_edit  # still typing
        assert len(page.folder_rows) == 1
        assert page.tiles[1].picked
    _type_name(qtbot, page, "Family")
    assert page.message_label.text() == ""
    assert [r.label.text() for r in page.folder_rows] == [
        "Friend: 1 person, 3 photos", "Family: 1 person, 2 photos"]


def test_open_tile_untick_face_excludes_photo(qtbot, make_window, root, engine):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_Return)  # opens Ann's faces
    dialog = page.faces_dialog
    assert dialog is not None and dialog.isVisible()
    checks = dialog.findChildren(QCheckBox)
    assert [c.text() for c in checks] == ["a1.jpg", "a2.jpg", "ab.jpg"]
    assert all(c.isChecked() for c in checks)
    qtbot.waitUntil(lambda: all(not c.icon().isNull() for c in checks))
    _activate(qtbot, dialog)  # as the window manager does when a dialog opens
    qtbot.waitUntil(lambda: _focused() is checks[0])
    assert dialog.strip.text() == "Arrows move · Space tick · Esc close"
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)  # ab.jpg's face isn't Ann
    assert [c.isChecked() for c in checks] == [True, True, False]
    assert page.tiles[0].count_label.text() == "in 2 photos"
    _key(qtbot, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    _activate(qtbot, w)  # as the window manager does when a dialog closes
    qtbot.waitUntil(lambda: _focused() is page.tiles[0])  # back on the tile
    _key(qtbot, Qt.Key.Key_Space)
    _type_name(qtbot, page, "Friend")
    assert page.folder_rows[0].label.text() == "Friend: 1 person, 2 photos"
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines() == ["2 photos → Friend"]
    assert sorted(m.src for m in w._plan.moves) == ["a1.jpg", "a2.jpg"]
    # Back keeps the grid as it was, the untick too.
    w.back_button.click()
    assert w.current_page() is page
    assert [r.label.text() for r in page.folder_rows] == ["Friend: 1 person, 2 photos"]
    _key(qtbot, Qt.Key.Key_Return)
    assert [c.isChecked() for c in page.faces_dialog.findChildren(QCheckBox)] == [
        True, True, False]
    page.faces_dialog.reject()


def test_remove_folder_frees_tiles(qtbot, make_window, root, engine):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_Space)
    _type_name(qtbot, page, "Friend")
    page.navigator.focus(1)
    _key(qtbot, Qt.Key.Key_Space)
    _type_name(qtbot, page, "Family")
    assert _status(page.tiles) == ["→ Friend", "→ Family"]
    page.navigator.focus(0)
    _key(qtbot, Qt.Key.Key_Delete)  # the focused tile's folder
    assert [r.label.text() for r in page.folder_rows] == ["Family: 1 person, 2 photos"]
    assert _status(page.tiles) == ["", "→ Family"]
    _key(qtbot, Qt.Key.Key_Space)
    assert page.tiles[0].picked  # free again
    assert w.strip.text() == STRIP
    page.folder_rows[0].remove_button.click()
    assert page.folder_rows == []
    assert _status(page.tiles) == ["✓ Picked", ""]
    assert "Del" not in w.strip.text()  # no folder left to remove


def test_no_faces_found(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, FakeEngine())
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines() == ["No faces found"]
    assert not w.go_button.isEnabled()


def test_continue_without_folder_says_make_a_folder_first(qtbot, make_window, root, engine):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    assert page.message_label.text() == "Make a folder first"
    assert w.current_page() is page
    page.continue_button.click()
    assert page.message_label.text() == "Make a folder first"
    assert w.current_page() is page


def test_fits_1024x600(qtbot, make_window, root, engine):
    w = make_window(size=(1024, 600))
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_Space)
    _type_name(qtbot, page, "Friend")
    qtbot.wait(20)
    room = w.stack.size()
    assert page.minimumSizeHint().width() <= room.width()
    assert page.minimumSizeHint().height() <= room.height()
    strip_top = w.strip.mapTo(w, w.strip.rect().topLeft()).y()
    for widget in (page.continue_button, page.back_button, page.add_button,
                   page.folder_rows[0].remove_button, page.folder_rows[0].remember_check):
        bottom_right = widget.mapTo(w, widget.rect().bottomRight())
        assert bottom_right.y() < strip_top, widget
        assert bottom_right.x() <= w.width(), widget
    for button in page.findChildren(QPushButton):
        if button.isVisible():  # none crushed below its text
            assert button.width() >= button.minimumSizeHint().width(), button.text()
    assert not page.scroll.horizontalScrollBar().isVisible()
    assert page.columns() >= 2
    tile = page.tiles[0]
    assert tile.mapTo(page.scroll.viewport(), tile.rect().bottomLeft()).y() <= (
        page.scroll.viewport().height())
    assert page.once_header.isVisible()


# ----- the flow ----------------------------------------------------------------------------

def test_go_moves_the_picked_photos_and_undo_puts_them_back(qtbot, make_window, root, engine,
                                                            errors):
    before = _hashes(root)
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)  # Ben
    _type_name(qtbot, page, "Friend")
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.summary_label.text().splitlines() == ["2 photos → Friend"]
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    after = _hashes(root)
    assert after["Friend/ab.jpg"] == before["ab.jpg"]
    assert after["Friend/b1.jpg"] == before["b1.jpg"]
    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert _hashes(root) == before
    assert errors == []


def test_esc_asks_then_leaves(qtbot, make_window, root, engine, asked):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    asked.answer["yes"] = False
    _key(qtbot, Qt.Key.Key_Escape)
    assert asked == ["Leave this review?"]
    assert w.current_page() is page
    asked.answer["yes"] = True
    _key(qtbot, Qt.Key.Key_Escape)
    assert w.current_page() is w.home
    assert page.tiles == []  # let go


def test_esc_in_the_name_box_goes_back_to_the_faces(qtbot, make_window, root, engine, asked):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    _key(qtbot, Qt.Key.Key_N)
    assert _focused() is page.name_edit
    assert w.strip.text() == ("Enter add · Esc back to faces · Ctrl+Enter continue · "
                              "Tab/Shift+Tab area")
    qtbot.keyClicks(page.name_edit, "Nn")  # N types here
    assert page.name_edit.text() == "Nn"
    _key(qtbot, Qt.Key.Key_Escape)
    assert _focused() is page.tiles[0]
    assert asked == []


def test_a_broken_face_engine_goes_home(qtbot, make_window, tmp_path, make_jpeg,
                                        errors):
    folder = tmp_path / "broken"
    for n in range(FACE_ENGINE_FAILS + 1):
        make_jpeg(folder / f"{n}.jpg")

    class Broken:
        def analyse(self, path):
            raise RuntimeError("model trouble")

    w = make_window()
    _start(qtbot, w, folder, Broken())
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    assert errors == ["Could not look for faces: model trouble"]
    assert w._plan is None


def test_a_double_click_opens_a_tile_and_leaves_its_pick(qtbot, make_window, root, engine):
    w = make_window()
    page = _on_grid(qtbot, w, root, engine)
    tile = page.tiles[1]
    qtbot.mouseClick(tile, Qt.MouseButton.LeftButton)  # a double-click's first click,
    qtbot.mouseDClick(tile, Qt.MouseButton.LeftButton)  # then its second
    assert page.faces_dialog is not None and page.faces_dialog.isVisible()
    assert [c.text() for c in page.faces_dialog.checks] == ["ab.jpg", "b1.jpg"]
    assert not tile.picked  # as it was before the double-click
    page.faces_dialog.reject()
    qtbot.mouseClick(page.once_header, Qt.MouseButton.LeftButton)  # a click opens it
    assert _counts(page.once_tiles) == ["in 1 photo", "in 1 photo"]
    assert all(t.isVisible() for t in page.once_tiles)
