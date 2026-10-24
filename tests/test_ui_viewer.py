from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QThreadPool
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem, QGraphicsSimpleTextItem

from photosort.groups import Candidate, Group
from photosort.ui.fullscreen_viewer import FullscreenViewer
from photosort.ui.review_page import ReviewPage


@pytest.fixture
def make_viewer(qtbot):
    def _make(root: Path, group: Group, keep: set[str]) -> FullscreenViewer:
        viewer = FullscreenViewer(root, group, keep)
        qtbot.addWidget(viewer)
        return viewer

    yield _make
    QThreadPool.globalInstance().waitForDone()


def _burst(root: Path, make_pattern_jpeg, n: int = 3) -> Group:
    members = []
    for i in range(n):
        rel = f"IMG_{i + 1}.jpg"
        path = make_pattern_jpeg(root / rel, 7, shift=i, blur=i)
        members.append(Candidate(rel, "photo", 800, 600, path.stat().st_size,
                                 datetime(2025, 2, 20, 18, 13, 25 + i), 100.0 - 40 * i))
    return Group("repeated", members, {members[0].rel})


def _key(qtbot, widget, key) -> None:
    qtbot.keyClick(widget, key)


def _texts(viewer: FullscreenViewer) -> list[str]:
    return [item.text() for item in viewer.scene.items()
            if isinstance(item, QGraphicsSimpleTextItem)]


def _pixmaps(viewer: FullscreenViewer) -> list[QGraphicsPixmapItem]:
    return [item for item in viewer.scene.items() if isinstance(item, QGraphicsPixmapItem)]


def test_arrows_flip_and_wrap(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    assert viewer.current_index == 0
    assert viewer.overlay_label.text() == "1 of 3 · IMG_1.jpg · Sharpness: best · KEEP"
    _key(qtbot, viewer, Qt.Key.Key_Right)
    assert viewer.current_index == 1
    assert viewer.overlay_label.text() == (
        "2 of 3 · IMG_2.jpg · Sharpness: slightly blurry · not kept"
    )
    _key(qtbot, viewer, Qt.Key.Key_Right)
    _key(qtbot, viewer, Qt.Key.Key_Right)
    assert viewer.current_index == 0
    _key(qtbot, viewer, Qt.Key.Key_Left)
    assert viewer.current_index == 2
    assert viewer.overlay_label.text().startswith("3 of 3 · IMG_3.jpg · Sharpness: blurry")


def test_space_toggles_keep(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    _key(qtbot, viewer, Qt.Key.Key_Right)
    assert not viewer.keep_check.isChecked()
    _key(qtbot, viewer, Qt.Key.Key_Space)
    assert viewer.keep_result() == {"IMG_1.jpg", "IMG_2.jpg"}
    assert viewer.keep_check.isChecked()
    assert viewer.overlay_label.text().endswith("· KEEP")
    _key(qtbot, viewer, Qt.Key.Key_Space)
    assert viewer.keep_result() == {"IMG_1.jpg"}
    assert not viewer.keep_check.isChecked()

    viewer.keep_check.click()  # the checkbox does the same
    assert viewer.keep_result() == {"IMG_1.jpg", "IMG_2.jpg"}
    assert viewer.overlay_label.text().endswith("· KEEP")


def test_space_on_the_checkbox_toggles_once(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    _key(qtbot, viewer, Qt.Key.Key_Right)
    _key(qtbot, viewer.keep_check, Qt.Key.Key_Space)
    assert viewer.keep_result() == {"IMG_1.jpg", "IMG_2.jpg"}


def test_keys_reach_the_dialog_whatever_has_focus(qtbot, tmp_path, make_viewer,
                                                  make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    # Nothing inside can take the keyboard focus, so key presses go to the dialog.
    assert viewer.view.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert viewer.keep_check.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert viewer.done_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    _key(qtbot, viewer.view, Qt.Key.Key_Right)  # the view hands keys on, too
    assert viewer.current_index == 1
    _key(qtbot, viewer.view, Qt.Key.Key_Space)
    assert viewer.keep_result() == {"IMG_1.jpg", "IMG_2.jpg"}


def test_cannot_unkeep_last(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    _key(qtbot, viewer, Qt.Key.Key_Space)
    assert viewer.keep_result() == {"IMG_1.jpg"}
    assert viewer.keep_check.isChecked()
    assert viewer.overlay_label.text() == "At least one photo must be kept."

    viewer.keep_check.click()
    assert viewer.keep_result() == {"IMG_1.jpg"}
    assert viewer.keep_check.isChecked()
    assert viewer.overlay_label.text() == "At least one photo must be kept."

    _key(qtbot, viewer, Qt.Key.Key_Right)  # the message gives way to the next photo
    assert viewer.overlay_label.text().startswith("2 of 3")


def test_zoom_keys_and_fit(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    assert viewer.zoom == 1.0
    _key(qtbot, viewer, Qt.Key.Key_Plus)
    assert viewer.zoom == pytest.approx(1.25)
    _key(qtbot, viewer, Qt.Key.Key_Equal)
    assert viewer.zoom == pytest.approx(1.25 ** 2)
    _key(qtbot, viewer, Qt.Key.Key_Minus)
    assert viewer.zoom == pytest.approx(1.25)
    _key(qtbot, viewer, Qt.Key.Key_0)
    assert viewer.zoom == 1.0
    _key(qtbot, viewer, Qt.Key.Key_Minus)  # never smaller than the whole photo
    assert viewer.zoom == 1.0


def test_wheel_zooms(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})

    def wheel(dy: int) -> None:
        pos = QPointF(10, 10)
        event = QWheelEvent(pos, pos, QPoint(), QPoint(0, dy), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                            False)
        QApplication.sendEvent(viewer.view.viewport(), event)

    wheel(120)
    assert viewer.zoom == pytest.approx(1.25)
    wheel(-120)
    assert viewer.zoom == 1.0


def test_side_by_side_toggle(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    _key(qtbot, viewer, Qt.Key.Key_Right)
    _key(qtbot, viewer, Qt.Key.Key_Right)
    _key(qtbot, viewer, Qt.Key.Key_Plus)
    assert not viewer.side_by_side
    _key(qtbot, viewer, Qt.Key.Key_S)
    assert viewer.side_by_side
    assert viewer.zoom == 1.0  # both fit
    assert viewer.shown_indexes() == [2, 0]  # current and next, wrapping
    qtbot.waitUntil(lambda: len(_pixmaps(viewer)) == 2)
    left, right = sorted(_pixmaps(viewer), key=lambda item: item.x())
    assert right.x() >= left.boundingRect().width()
    _key(qtbot, viewer, Qt.Key.Key_S)
    assert not viewer.side_by_side
    assert viewer.shown_indexes() == [2]


def test_images_load_off_the_gui_thread(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    group = _burst(tmp_path, make_pattern_jpeg)
    (tmp_path / "IMG_2.jpg").write_bytes(b"not a jpeg any more")
    viewer = make_viewer(tmp_path, group, {"IMG_1.jpg"})
    assert _texts(viewer) == ["Loading…"]
    qtbot.waitUntil(lambda: len(_pixmaps(viewer)) == 1)
    assert _texts(viewer) == []
    assert _pixmaps(viewer)[0].pixmap().width() == 800
    qtbot.waitUntil(lambda: 1 in viewer.loaded_indexes())  # the next one is preloaded
    _key(qtbot, viewer, Qt.Key.Key_Right)
    assert _texts(viewer) == ["Can't show this photo"]
    assert _pixmaps(viewer) == []


def test_results_after_close_are_ignored(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    viewer = make_viewer(tmp_path, _burst(tmp_path, make_pattern_jpeg), {"IMG_1.jpg"})
    _key(qtbot, viewer, Qt.Key.Key_Escape)
    QThreadPool.globalInstance().waitForDone()
    qtbot.wait(50)  # let any queued results arrive
    assert viewer.loaded_indexes() == set()
    assert _texts(viewer) == ["Loading…"]


def test_esc_and_enter_close(qtbot, tmp_path, make_viewer, make_pattern_jpeg):
    group = _burst(tmp_path, make_pattern_jpeg)
    for key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
        viewer = make_viewer(tmp_path, group, {"IMG_1.jpg"})
        viewer.show()
        _key(qtbot, viewer, key)
        assert not viewer.isVisible()


def test_review_page_applies_viewer_result(qtbot, tmp_path, make_pattern_jpeg, monkeypatch):
    group = _burst(tmp_path, make_pattern_jpeg)
    seen = []

    def fake_exec(self):
        seen.append((self.keep_result(), self.windowState()))
        self.toggle_keep()  # IMG_1.jpg, kept, would leave nothing: refused
        qtbot.keyClick(self, Qt.Key.Key_Right)
        qtbot.keyClick(self, Qt.Key.Key_Space)
        qtbot.keyClick(self, Qt.Key.Key_Right)
        qtbot.keyClick(self, Qt.Key.Key_Space)
        qtbot.keyClick(self, Qt.Key.Key_Left)
        qtbot.keyClick(self, Qt.Key.Key_Left)
        qtbot.keyClick(self, Qt.Key.Key_Space)
        return 0

    monkeypatch.setattr(FullscreenViewer, "exec", fake_exec)
    page = ReviewPage()
    qtbot.addWidget(page)
    page.load(tmp_path, "repeated", [group])
    assert not page.fullscreen_button.isHidden()
    page.fullscreen_button.click()
    assert seen == [({"IMG_1.jpg"}, Qt.WindowState.WindowFullScreen)]
    ticked = {c.candidate.rel for c in page.cards if c.keep_check.isChecked()}
    assert ticked == {"IMG_2.jpg", "IMG_3.jpg"}
    QThreadPool.globalInstance().waitForDone()
    qtbot.waitUntil(lambda: not page.findChildren(FullscreenViewer))  # freed once idle


def test_fullscreen_button_only_for_repeated(qtbot, tmp_path, make_pattern_jpeg):
    page = ReviewPage()
    qtbot.addWidget(page)
    page.load(tmp_path, "duplicates", [_burst(tmp_path, make_pattern_jpeg)])
    assert page.fullscreen_button.isHidden()
    QThreadPool.globalInstance().waitForDone()

