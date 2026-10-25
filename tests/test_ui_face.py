import gc
import threading
import weakref
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from photosort.faces import Face
from photosort.modes.face_mode import FaceScan, Question
from photosort.people import PeopleStore
from photosort.settings import Settings, load_settings
from photosort.ui import main_window
from photosort.ui import face_question_page
from photosort.ui.face_naming_dialog import FaceNamingDialog
from photosort.ui.face_question_page import FaceQuestionPage
from photosort.ui.face_setup_page import FaceSetupPage, RememberedPeopleDialog
from photosort.ui.main_window import MainWindow
from photosort.ui.settings_dialog import SettingsDialog

NO_FACES = "No faces found in this photo. Try another one."


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _towards(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    v = (1 - t) * a + t * b
    return (v / np.linalg.norm(v)).astype(np.float32)


def _face(embedding: np.ndarray, x: float = 20) -> Face:
    return Face((x, 20.0, x + 60, 80.0), np.zeros((5, 2), np.float32), 0.9,
                embedding, (200, 200))


class FakeEngine:
    """Faces by file name; analyse() of a name in hold waits for hold[name]."""

    def __init__(self, faces: dict[str, list[Face]] | None = None) -> None:
        self.faces = faces or {}
        self.hold: dict[str, threading.Event] = {}
        self.calls: list[str] = []

    def analyse(self, path: Path) -> list[Face]:
        self.calls.append(Path(path).name)
        if Path(path).name in self.hold:
            assert self.hold[Path(path).name].wait(10)
        return list(self.faces.get(Path(path).name, []))


@pytest.fixture
def store(tmp_path) -> PeopleStore:
    return PeopleStore(tmp_path / "people")


def _remember(store: PeopleStore, name: str, seed: int):
    return store.add(name, _unit(seed), Image.new("RGB", (40, 40), "blue"))


@pytest.fixture
def photo(tmp_path, make_jpeg):
    return make_jpeg(tmp_path / "faces" / "two.jpg", size=(200, 200))


@pytest.fixture(autouse=True)
def wait_for_pool():
    yield
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def asked(monkeypatch):
    """QMessageBox.question answers with asked.answer; the texts go in asked.texts."""

    class Asked:
        answer = QMessageBox.StandardButton.Yes
        texts: list[str] = []

    record = Asked()
    record.texts = []

    def question(parent, title, text, *args, **kwargs):
        record.texts.append(text)
        return record.answer

    monkeypatch.setattr(QMessageBox, "question", question)
    return record


# ----- setup page -----------------------------------------------------------

def _rows(page: FaceSetupPage) -> list[tuple[str, bool]]:
    out = []
    for n in range(page.people_list.count()):
        item = page.people_list.item(n)
        out.append((item.text(), item.checkState() == Qt.CheckState.Checked))
    return out


def _item(page: FaceSetupPage, name: str):
    for n in range(page.people_list.count()):
        if page.people_list.item(n).text() == name:
            return page.people_list.item(n)
    raise AssertionError(name)


def _make_page(qtbot, settings: Settings) -> FaceSetupPage:
    page = FaceSetupPage(settings)
    qtbot.addWidget(page)
    return page


def test_setup_page_lists_people_all_ticked_first_time(qtbot, store):
    zoe = _remember(store, "Zoe", 2)
    atanu = _remember(store, "Atanu", 1)
    page = _make_page(qtbot, Settings())
    page.load(store)
    assert _rows(page) == [("Atanu", True), ("Zoe", True)]
    assert not _item(page, "Atanu").icon().isNull()  # the remembered face picture
    assert page.add_button.text() == "Add person…"
    assert page.start_button.text() == "Start sorting"
    assert page.start_button.isEnabled()
    started = []
    page.start.connect(started.append)
    page.start_button.click()
    assert started == [[atanu.id, zoe.id]]


def test_setup_page_ticks_persist_and_start_needs_a_tick(qtbot, store):
    atanu = _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    settings = Settings()
    page = _make_page(qtbot, settings)
    page.load(store)
    changed = []
    page.selection_changed.connect(lambda: changed.append(True))
    _item(page, "Zoe").setCheckState(Qt.CheckState.Unchecked)
    assert changed
    assert settings.face_people == [atanu.id]

    again = _make_page(qtbot, settings)
    again.load(store)
    assert _rows(again) == [("Atanu", True), ("Zoe", False)]
    _item(again, "Atanu").setCheckState(Qt.CheckState.Unchecked)
    assert not again.start_button.isEnabled()


def test_setup_page_buttons_name_the_selected_person(qtbot, store):
    atanu = _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    page = _make_page(qtbot, Settings())
    page.load(store)
    page.people_list.setCurrentItem(_item(page, "Zoe"))
    assert page.more_button.text() == "Add photos to Zoe…"
    assert page.delete_button.text() == "Forget Zoe"
    page.people_list.setCurrentItem(_item(page, "Atanu"))
    assert page.more_button.text() == "Add photos to Atanu…"
    asked_for = []
    page.more_requested.connect(asked_for.append)
    page.more_button.click()
    assert asked_for == [atanu.id]


def test_setup_page_with_nobody_remembered(qtbot, store):
    page = _make_page(qtbot, Settings())
    page.load(store)
    assert page.people_list.count() == 0
    assert not page.start_button.isEnabled()
    assert not page.more_button.isEnabled()
    assert not page.delete_button.isEnabled()
    assert page.add_button.isEnabled()


def test_forget_asks_first_then_removes(qtbot, store, asked):
    _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    page = _make_page(qtbot, Settings())
    page.load(store)
    page.people_list.setCurrentItem(_item(page, "Zoe"))

    asked.answer = QMessageBox.StandardButton.No
    page.delete_button.click()
    assert len(asked.texts) == 1 and "Zoe" in asked.texts[0]
    assert [p.name for p in store.people()] == ["Atanu", "Zoe"]

    asked.answer = QMessageBox.StandardButton.Yes
    page.delete_button.click()
    assert [p.name for p in store.people()] == ["Atanu"]
    assert _rows(page) == [("Atanu", True)]


def test_remembered_people_dialog_is_delete_only(qtbot, store, asked):
    _remember(store, "Atanu", 1)
    d = RememberedPeopleDialog(store)
    qtbot.addWidget(d)
    page = d.page
    assert page.people_list.count() == 1
    item = page.people_list.item(0)
    assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    for button in (page.add_button, page.more_button, page.start_button, page.back_button):
        assert button.isHidden()
    page.people_list.setCurrentRow(0)
    page.delete_button.click()
    assert store.people() == []
    assert page.people_list.count() == 0


def test_settings_remembered_people_uses_the_given_store(qtbot, store, tmp_path,
                                                          monkeypatch):
    opened = []
    monkeypatch.setattr(RememberedPeopleDialog, "exec", lambda self: opened.append(self))
    d = SettingsDialog(Settings(), tmp_path / "settings.json", people=store)
    qtbot.addWidget(d)
    assert d.people_button.text() == "Remembered people…"
    d.people_button.click()
    assert len(opened) == 1 and opened[0].store is store


# ----- naming dialog --------------------------------------------------------

def _make_dialog(qtbot, path, engine, store, **kwargs) -> FaceNamingDialog:
    d = FaceNamingDialog(path, engine, store, **kwargs)
    qtbot.addWidget(d)
    return d


def _two_faces(photo) -> FakeEngine:
    return FakeEngine({photo.name: [_face(_unit(1), 10), _face(_unit(2), 110)]})


def test_naming_dialog_shows_a_row_per_face_and_saves(qtbot, store, photo):
    engine = _two_faces(photo)
    d = _make_dialog(qtbot, photo, engine, store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    assert engine.calls == ["two.jpg"]
    for row in d.rows:
        assert not row.crop_label.pixmap().isNull()
        assert row.skip_check.text() == "Skip this face"
    d.rows[0].name_edit.setText("Atanu")
    d.rows[1].name_edit.setText(" Zoe ")
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert [p.name for p in store.people()] == ["Atanu", "Zoe"]
    assert [p.name for p in d.added()] == ["Atanu", "Zoe"]
    atanu = store.people()[0]
    assert np.array_equal(atanu.embeddings[0], _unit(1))
    assert atanu.thumb.is_file()


def test_naming_dialog_skipped_face_adds_nothing(qtbot, store, photo):
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("Atanu")
    d.rows[1].skip_check.setChecked(True)
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert [p.name for p in store.people()] == ["Atanu"]
    assert [p.name for p in d.added()] == ["Atanu"]


def test_naming_dialog_same_name_twice_is_shown_inline(qtbot, store, photo):
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("Zoe")
    d.rows[1].name_edit.setText("zoe")
    d.save_button.click()
    assert d.result() != QDialog.DialogCode.Accepted
    assert d.rows[0].error_label.text() == ""
    assert d.rows[1].error_label.text() == "Two faces can't have the same name."
    assert not d.rows[1].error_label.isHidden()
    assert store.people() == []


def test_naming_dialog_store_error_is_shown_inline(qtbot, store, photo):
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("Atanu")
    d.rows[1].name_edit.setText("Duplicates")
    d.save_button.click()
    assert d.result() != QDialog.DialogCode.Accepted
    assert d.rows[1].error_label.text() == (
        "Photo Sorter uses that name for its own folder. Pick another name.")
    assert [p.name for p in store.people()] == ["Atanu"]  # the good row is saved
    assert not d.rows[0].name_edit.isEnabled()

    d.rows[1].name_edit.setText("Zoe")
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert [p.name for p in store.people()] == ["Atanu", "Zoe"]  # Atanu saved once
    assert len(store.people()[0].embeddings) == 1
    assert [p.name for p in d.added()] == ["Atanu", "Zoe"]


def test_naming_dialog_existing_name_adds_samples(qtbot, store, photo):
    atanu = _remember(store, "Atanu", 1)
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("ATANU")
    d.rows[1].skip_check.setChecked(True)
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    (person,) = store.people()
    assert person.id == atanu.id and person.name == "Atanu"
    assert len(person.embeddings) == 2
    assert [p.id for p in d.added()] == [atanu.id]


def test_naming_dialog_empty_name_needs_a_name(qtbot, store, photo):
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("Atanu")
    d.save_button.click()
    assert d.result() != QDialog.DialogCode.Accepted
    assert d.rows[1].error_label.text() == "Type a name."


def test_naming_dialog_no_faces(qtbot, store, photo):
    d = _make_dialog(qtbot, photo, FakeEngine(), store)
    qtbot.waitUntil(lambda: d.status_label.text() == NO_FACES)
    assert d.rows == []
    assert not d.save_button.isEnabled()


def test_naming_dialog_unreadable_photo(qtbot, store, tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not a photo")

    class Unreadable:
        def analyse(self, path):
            from photosort.imageinfo import open_image
            open_image(path, 100)

    d = _make_dialog(qtbot, bad, Unreadable(), store)
    qtbot.waitUntil(lambda: d.status_label.text() == "This photo can't be opened. "
                                                     "Try another one.")
    assert d.rows == []


def test_naming_dialog_prefills_the_person_for_more_photos(qtbot, store, photo):
    atanu = _remember(store, "Atanu", 1)
    engine = FakeEngine({photo.name: [_face(_unit(1))]})
    d = _make_dialog(qtbot, photo, engine, store, name="Atanu")
    qtbot.waitUntil(lambda: len(d.rows) == 1)
    assert d.rows[0].name_edit.text() == "Atanu"
    d.save_button.click()
    assert len(store.people()[0].embeddings) == 2
    assert [p.id for p in d.added()] == [atanu.id]


def test_naming_dialog_choose_and_drop_another_photo(qtbot, store, photo, make_jpeg,
                                                    tmp_path, monkeypatch):
    other = make_jpeg(tmp_path / "faces" / "one.jpg", size=(200, 200))
    third = make_jpeg(tmp_path / "faces" / "three.jpg", size=(200, 200))
    engine = FakeEngine({other.name: [_face(_unit(3))],
                         third.name: [_face(_unit(4)), _face(_unit(5), 110), _face(_unit(6))]})
    d = _make_dialog(qtbot, photo, engine, store)
    qtbot.waitUntil(lambda: d.status_label.text() == NO_FACES)

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(other), ""))
    d.choose_button.click()
    qtbot.waitUntil(lambda: len(d.rows) == 1)
    assert d.status_label.text() != NO_FACES

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(third))])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    d.dropEvent(event)
    qtbot.waitUntil(lambda: len(d.rows) == 3)
    assert engine.calls == ["two.jpg", "one.jpg", "three.jpg"]


def test_naming_dialog_matches_names_as_they_are_saved(qtbot, store, photo):
    zoe = _remember(store, "Zoe", 2)
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("Zoe.")  # saved as "Zoe": the same person
    d.rows[1].skip_check.setChecked(True)
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    (person,) = store.people()
    assert person.id == zoe.id and len(person.embeddings) == 2


def test_naming_dialog_same_saved_name_twice_is_refused(qtbot, store, photo):
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("Zoe")
    d.rows[1].name_edit.setText("zoe.")
    d.save_button.click()
    assert d.result() != QDialog.DialogCode.Accepted
    assert d.rows[1].error_label.text() == "Two faces can't have the same name."
    assert store.people() == []


def test_naming_dialog_closed_while_looking_is_fine(qtbot, store, photo):
    engine = _two_faces(photo)
    engine.hold[photo.name] = threading.Event()
    d = FaceNamingDialog(photo, engine, store)
    qtbot.waitUntil(lambda: engine.calls == [photo.name])
    d.reject()
    d.deleteLater()
    qtbot.wait(10)  # the dialog is gone before the analysis reports
    engine.hold[photo.name].set()
    QThreadPool.globalInstance().waitForDone()
    qtbot.wait(10)  # the result arrives for a dialog that is gone: no error


def test_naming_dialog_lets_go_of_its_engine_when_closed(qtbot, store, photo):
    engine = _two_faces(photo)
    ref = weakref.ref(engine)
    d = _make_dialog(qtbot, photo, engine, store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.reject()
    del engine
    QThreadPool.globalInstance().waitForDone()
    gc.collect()
    assert ref() is None


# ----- question page --------------------------------------------------------

@pytest.fixture
def asking(tmp_path, store, make_jpeg):
    """Three questions: a.jpg (Atanu), b.jpg (Atanu, then Zoe)."""
    root = tmp_path / "asked"
    make_jpeg(root / "a.jpg", size=(200, 200))
    make_jpeg(root / "b.jpg", size=(300, 200))
    atanu = _remember(store, "Atanu", 1)
    zoe = _remember(store, "Zoe", 2)
    questions = [
        Question("a.jpg", atanu.id, "Atanu", (20.0, 40.0, 80.0, 120.0), (200, 200), 0.40, 63),
        Question("b.jpg", atanu.id, "Atanu", (10.0, 10.0, 50.0, 50.0), (300, 200), 0.35, 50),
        Question("b.jpg", zoe.id, "Zoe", (150.0, 10.0, 200.0, 60.0), (300, 200), 0.32, 38),
    ]
    scan = FaceScan({}, questions, photos=2, unreadable=0)
    return root, scan, [atanu, zoe]


def _question_page(qtbot, asking) -> FaceQuestionPage:
    root, scan, people = asking
    page = FaceQuestionPage()
    qtbot.addWidget(page)
    page.resize(900, 600)
    page.load(root, scan, people)
    return page


def _keys(scan):
    return [(q.rel, q.person_id) for q in scan.questions]


def test_question_shows_name_percent_and_counter(qtbot, asking):
    page = _question_page(qtbot, asking)
    assert page.heading_label.text() == "Is this Atanu? 63% sure"
    assert page.counter_label.text() == "1 of 3"
    assert not page.person_label.pixmap().isNull()  # Atanu's remembered face
    assert page.yes_button.text() == "Yes"
    assert page.no_button.text() == "No"
    assert page.skip_button.text() == "Skip"
    assert page.back_button.text() == "Previous"
    assert page.stop_button.text() == "Stop asking"
    assert not page.back_button.isEnabled()
    qtbot.waitUntil(page.photo_view.has_photo)
    assert page.photo_view.image_size() == (200, 200)


def test_face_box_is_drawn_scaled_to_the_photo_shown(qtbot, asking):
    page = _question_page(qtbot, asking)
    qtbot.waitUntil(page.photo_view.has_photo)
    view = page.photo_view
    view.resize(600, 300)  # the 200x200 photo is shown 300x300, centred
    box = view.box_rect()
    assert (box.left(), box.top(), box.width(), box.height()) == pytest.approx(
        (150 + 30, 60, 90, 120))
    view.resize(400, 400)  # shown 400x400: twice the photo's pixels
    box = view.box_rect()
    assert (box.left(), box.top(), box.width(), box.height()) == pytest.approx(
        (40, 80, 120, 160))


def test_face_box_follows_the_photos_orientation(qtbot, tmp_path, store, make_pattern_jpeg):
    # Stored 300x200 and turned upright (EXIF 6) to 200x300, as faces.py decodes it.
    root = tmp_path / "turned"
    make_pattern_jpeg(root / "t.jpg", 1, size=(300, 200), orientation=6)
    atanu = _remember(store, "Atanu", 1)
    question = Question("t.jpg", atanu.id, "Atanu", (0.0, 150.0, 100.0, 300.0), (200, 300),
                        0.4, 63)
    page = FaceQuestionPage()
    qtbot.addWidget(page)
    page.load(root, FaceScan({}, [question], 1, 0), [atanu])
    qtbot.waitUntil(page.photo_view.has_photo)
    assert page.photo_view.image_size() == (200, 300)
    view = page.photo_view
    view.resize(400, 600)  # shown at twice the decoded size
    box = view.box_rect()
    assert (box.left(), box.top(), box.width(), box.height()) == pytest.approx(
        (0, 300, 200, 300))


def test_keys_answer_and_move_on(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    assert page.counter_label.text() == "2 of 3"
    assert page.heading_label.text() == "Is this Atanu? 50% sure"
    assert page.back_button.isEnabled()
    qtbot.keyClick(page, Qt.Key.Key_N)
    assert page.counter_label.text() == "3 of 3"
    assert page.heading_label.text() == "Is this Zoe? 38% sure"
    assert done == []
    qtbot.keyClick(page, Qt.Key.Key_S)
    a, b_atanu, b_zoe = _keys(scan)
    assert done == [{a: "yes", b_atanu: "no", b_zoe: "skip"}]


def test_buttons_answer_too(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    page.no_button.click()
    page.skip_button.click()
    page.yes_button.click()
    a, b_atanu, b_zoe = _keys(scan)
    assert done == [{a: "no", b_atanu: "skip", b_zoe: "yes"}]


def test_previous_lets_an_answer_be_changed(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    qtbot.keyClick(page, Qt.Key.Key_Left)
    assert page.counter_label.text() == "2 of 3"
    assert page.answer_label.text() == "Your answer: Yes"
    page.back_button.click()
    assert page.counter_label.text() == "1 of 3"
    qtbot.keyClick(page, Qt.Key.Key_N)  # changed
    assert page.counter_label.text() == "2 of 3"
    page.stop_button.click()
    a, b_atanu, _ = _keys(scan)
    assert done == [{a: "no", b_atanu: "yes"}]


def test_stop_finishes_with_the_answers_so_far(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    page.stop_button.click()
    assert done == [{_keys(scan)[0]: "yes"}]


def test_cancel_question_page(qtbot, asking):
    page = _question_page(qtbot, asking)
    cancelled = []
    page.cancelled.connect(lambda: cancelled.append(True))
    page.cancel_button.click()
    assert cancelled == [True]


def test_resume_shows_the_last_question_with_its_answer(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    for key in (Qt.Key.Key_Y, Qt.Key.Key_N, Qt.Key.Key_S):
        qtbot.keyClick(page, key)
    page.resume()
    assert page.counter_label.text() == "3 of 3"
    assert page.answer_label.text() == "Your answer: Skip"
    page.stop_button.click()
    assert done[-1] == done[0]


def test_unreadable_photo_can_still_be_answered(qtbot, asking):
    root, scan, _ = asking
    (root / "a.jpg").write_bytes(b"not a photo")
    page = _question_page(qtbot, asking)
    qtbot.waitUntil(lambda: page.photo_view.message() == "Couldn't load this photo")
    assert not page.photo_view.has_photo()
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    page.stop_button.click()
    assert done == [{_keys(scan)[0]: "yes"}]


def test_a_late_photo_never_shows_on_another_question(qtbot, asking, monkeypatch):
    root, scan, _ = asking
    release = threading.Event()
    real = face_question_page.open_image

    def slow_for_a(path, max_px=None):
        if Path(path).name == "a.jpg":
            assert release.wait(10)
        return real(path, max_px)

    monkeypatch.setattr(face_question_page, "open_image", slow_for_a)
    page = _question_page(qtbot, asking)
    assert page.photo_view.message() == "Loading…"
    qtbot.keyClick(page, Qt.Key.Key_Y)  # on to b.jpg before a.jpg has loaded
    qtbot.waitUntil(page.photo_view.has_photo)
    assert page.photo_view.image_size() == (300, 200)
    release.set()
    QThreadPool.globalInstance().waitForDone()
    qtbot.wait(20)
    assert page.photo_view.image_size() == (300, 200)  # still b.jpg


# ----- main window ----------------------------------------------------------

@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def settings_path(tmp_path):
    return tmp_path / "config" / "settings.json"


@pytest.fixture
def models_present(monkeypatch):
    calls = []

    def ensure(parent, keys):
        calls.append((parent, list(keys)))
        return True

    monkeypatch.setattr(main_window, "ensure_models", ensure)
    return calls


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


@pytest.fixture
def make_window(qtbot, settings_path, errors, store):
    windows = []

    def _make(engine: FakeEngine | None = None) -> MainWindow:
        w = MainWindow(Settings(), settings_path, people_store=store)
        w.engine_factory = lambda use_gpu: engine or FakeEngine()
        qtbot.addWidget(w)
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


def _to_setup(w: MainWindow, photos: Path) -> None:
    w.folder_edit.setText(str(photos))
    w.mode_buttons["face"].setChecked(True)
    w.start_button.click()


def test_face_start_with_models_present_shows_setup(make_window, photos, store,
                                                   monkeypatch):
    from photosort.ai import models

    asked = []
    monkeypatch.setattr(models, "missing", lambda keys: asked.append(list(keys)) or [])
    _remember(store, "Atanu", 1)
    w = make_window()
    _to_setup(w, photos)
    assert asked == [["face_detection", "face_recognition"]]
    assert w.current_page() is w.face_setup_page
    assert _rows(w.face_setup_page) == [("Atanu", True)]
    w.face_setup_page.back_button.click()
    assert w.current_page() is w.home


def test_face_start_with_models_missing_stays_home(make_window, photos, monkeypatch,
                                                  settings_path):
    calls = []

    def ensure(parent, keys):
        calls.append((parent, list(keys)))
        return False

    monkeypatch.setattr(main_window, "ensure_models", ensure)
    w = make_window()
    _to_setup(w, photos)
    assert calls == [(w, ["face_detection", "face_recognition"])]
    assert w.current_page() is w.home
    assert w._task is None
    assert not settings_path.exists()  # nothing else happened


def test_face_sort_runs_and_plans(qtbot, make_window, photos, store, make_jpeg,
                                  models_present, errors):
    _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_jpeg(photos / name)
    engine = FakeEngine({"a.jpg": [_face(_unit(1))],
                         "b.jpg": [_face(_unit(1)), _face(_unit(2), 110)],
                         "c.jpg": [_face(_unit(9))]})
    engine.hold["b.jpg"] = threading.Event()
    gpu = []
    w = make_window()
    w.engine_factory = lambda use_gpu: gpu.append(use_gpu) or engine
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    assert w.current_page() is w.progress_page
    qtbot.waitUntil(lambda: w.progress_label.text() == "Looking for faces… 1 of 3")
    engine.hold["b.jpg"].set()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert gpu == [w.settings.use_gpu]
    assert w.summary_label.text().splitlines() == [
        "2 photos → 1 person folders, 1 together folders",
        "1 photos stay where they are",
    ]
    assert sorted((m.src, m.dst) for m in w._plan.moves) == [
        ("a.jpg", "Atanu/a.jpg"), ("b.jpg", "Atanu & Zoe/b.jpg")]
    assert (photos / ".photosort" / "cache.db").is_file()  # the analysis cache was used
    assert errors == []

    w.back_button.click()
    assert w.current_page() is w.face_setup_page


def test_face_sort_with_only_ticked_people(qtbot, make_window, photos, store, make_jpeg,
                                           models_present, settings_path):
    _remember(store, "Atanu", 1)
    zoe = _remember(store, "Zoe", 2)
    make_jpeg(photos / "b.jpg")
    engine = FakeEngine({"b.jpg": [_face(_unit(1)), _face(_unit(2), 110)]})
    w = make_window(engine)
    _to_setup(w, photos)
    _item(w.face_setup_page, "Atanu").setCheckState(Qt.CheckState.Unchecked)
    assert load_settings(settings_path).face_people == [zoe.id]
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert [(m.src, m.dst) for m in w._plan.moves] == [("b.jpg", "Zoe/b.jpg")]


def _one_unsure(store, photos, make_jpeg):
    """a.jpg with a face rather like Atanu's (similarity about 0.39)."""
    atanu = _remember(store, "Atanu", 1)
    make_jpeg(photos / "a.jpg", size=(200, 200))
    unsure = _towards(atanu.embeddings[0], _unit(7), 0.7)
    return atanu, FakeEngine({"a.jpg": [_face(unsure)]})


def test_unsure_match_is_asked_before_planning(qtbot, make_window, photos, store,
                                                make_jpeg, models_present, errors):
    atanu, engine = _one_unsure(store, photos, make_jpeg)
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    page = w.face_question_page
    assert page.heading_label.text().startswith("Is this Atanu? ")
    assert page.counter_label.text() == "1 of 1"
    assert w._plan is None  # nothing planned before the answers
    page.yes_button.click()
    assert w.current_page() is w.summary_page
    assert [(m.src, m.dst) for m in w._plan.moves] == [("a.jpg", "Atanu/a.jpg")]
    assert errors == []


def test_stop_asking_leaves_unanswered_photos(qtbot, make_window, photos, store,
                                              make_jpeg, models_present):
    _, engine = _one_unsure(store, photos, make_jpeg)
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    w.face_question_page.stop_button.click()
    assert w.current_page() is w.summary_page
    assert w._plan.moves == []
    assert not w.go_button.isEnabled()


def test_back_from_summary_returns_to_the_questions(qtbot, make_window, photos, store,
                                                    make_jpeg, models_present):
    _, engine = _one_unsure(store, photos, make_jpeg)
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    page = w.face_question_page
    page.yes_button.click()
    w.back_button.click()
    assert w.current_page() is page
    assert w._plan is None
    assert page.counter_label.text() == "1 of 1"
    assert page.answer_label.text() == "Your answer: Yes"  # kept
    page.stop_button.click()
    assert [(m.src, m.dst) for m in w._plan.moves] == [("a.jpg", "Atanu/a.jpg")]


def test_cancel_questions_returns_to_setup(qtbot, make_window, photos, store, make_jpeg,
                                           models_present):
    _, engine = _one_unsure(store, photos, make_jpeg)
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    w.face_question_page.cancel_button.click()
    assert w.current_page() is w.face_setup_page
    assert w._plan is None


def test_planning_error_after_questions_goes_home(qtbot, make_window, photos, store,
                                                  make_jpeg, models_present, errors,
                                                  monkeypatch):
    _, engine = _one_unsure(store, photos, make_jpeg)

    def unplugged(*args, **kwargs):
        raise OSError("the drive is gone")

    monkeypatch.setattr(main_window, "plan_faces", unplugged)
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    w.face_question_page.yes_button.click()
    assert w.current_page() is w.home
    assert errors == ["Could not plan the moves: the drive is gone"]
    assert w._plan is None


def test_window_lets_go_of_the_naming_engine(qtbot, make_window, photos, store, make_jpeg,
                                             tmp_path, models_present, monkeypatch):
    _remember(store, "Atanu", 1)
    me = make_jpeg(tmp_path / "faces" / "zoe.jpg", size=(200, 200))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(me), ""))
    _name_in_dialog(monkeypatch, qtbot, "Zoe")
    made = []

    def factory(use_gpu):
        engine = FakeEngine({"zoe.jpg": [_face(_unit(2))]})
        made.append(weakref.ref(engine))
        return engine

    w = make_window()
    w.engine_factory = factory
    _to_setup(w, photos)
    w.face_setup_page.add_button.click()
    assert [p.name for p in store.people()] == ["Atanu", "Zoe"]
    QThreadPool.globalInstance().waitForDone()
    qtbot.wait(10)  # the closed dialog is deleted
    gc.collect()
    assert len(made) == 1 and made[0]() is None


def test_cancel_face_scan_returns_to_setup(qtbot, make_window, photos, store, make_jpeg,
                                           models_present):
    _remember(store, "Atanu", 1)
    make_jpeg(photos / "a.jpg")
    engine = FakeEngine({"a.jpg": [_face(_unit(1))]})
    engine.hold["a.jpg"] = threading.Event()
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: engine.calls == ["a.jpg"])
    w.cancel_button.click()
    engine.hold["a.jpg"].set()
    qtbot.waitUntil(lambda: w.current_page() is w.face_setup_page)
    assert w._plan is None


def test_face_scan_error_goes_home(qtbot, make_window, photos, store, make_jpeg,
                                   models_present, errors):
    _remember(store, "Atanu", 1)
    for n in range(5):  # the first five photos all fail: the models are at fault
        make_jpeg(photos / f"{n}.jpg")

    class Broken:
        def analyse(self, path):
            raise RuntimeError("model trouble")

    w = make_window()
    w.engine_factory = lambda use_gpu: Broken()
    _to_setup(w, photos)
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    assert errors == ["Could not look for faces: model trouble"]


def _name_in_dialog(monkeypatch, qtbot, *names: str, opened: list | None = None):
    """FaceNamingDialog.exec types names into its rows and presses Save."""

    def fake_exec(self):
        if opened is not None:
            opened.append(self)
        qtbot.waitUntil(lambda: len(self.rows) == len(names))
        for row, name in zip(self.rows, names):
            if name:
                row.name_edit.setText(name)
            else:
                row.skip_check.setChecked(True)
        self.save_button.click()
        return self.result()

    monkeypatch.setattr(FaceNamingDialog, "exec", fake_exec)


def test_naming_with_nobody_remembered_starts_sorting(qtbot, make_window, photos, store,
                                                      make_jpeg, tmp_path, models_present,
                                                      monkeypatch, settings_path):
    me = make_jpeg(tmp_path / "faces" / "me.jpg", size=(200, 200))
    make_jpeg(photos / "a.jpg")
    engine = FakeEngine({"me.jpg": [_face(_unit(1))], "a.jpg": [_face(_unit(1))]})
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(me), ""))
    opened = []
    _name_in_dialog(monkeypatch, qtbot, "Atanu", opened=opened)
    w = make_window(engine)
    _to_setup(w, photos)
    assert w.current_page() is w.face_setup_page
    w.face_setup_page.add_button.click()
    assert opened[0].parent() is w
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    (atanu,) = store.people()
    assert atanu.name == "Atanu"
    assert load_settings(settings_path).face_people == [atanu.id]
    assert [(m.src, m.dst) for m in w._plan.moves] == [("a.jpg", "Atanu/a.jpg")]


def test_naming_with_people_listed_waits_for_start(qtbot, make_window, photos, store,
                                                   make_jpeg, tmp_path, models_present,
                                                   monkeypatch, settings_path):
    atanu = _remember(store, "Atanu", 1)
    me = make_jpeg(tmp_path / "faces" / "zoe.jpg", size=(200, 200))
    engine = FakeEngine({"zoe.jpg": [_face(_unit(2))]})
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(me), ""))
    _name_in_dialog(monkeypatch, qtbot, "Zoe")
    w = make_window(engine)
    _to_setup(w, photos)
    _item(w.face_setup_page, "Atanu").setCheckState(Qt.CheckState.Unchecked)
    w.face_setup_page.add_button.click()
    assert w.current_page() is w.face_setup_page
    assert _rows(w.face_setup_page) == [("Atanu", False), ("Zoe", True)]
    zoe = store.people()[1]
    assert load_settings(settings_path).face_people == [zoe.id]
    assert atanu.id not in load_settings(settings_path).face_people


def test_add_photos_to_a_person(qtbot, make_window, photos, store, make_jpeg, tmp_path,
                                models_present, monkeypatch):
    _remember(store, "Atanu", 1)
    more = make_jpeg(tmp_path / "faces" / "more.jpg", size=(200, 200))
    engine = FakeEngine({"more.jpg": [_face(_unit(1))]})
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(more), ""))
    opened = []

    def fake_exec(self):
        opened.append(self)
        qtbot.waitUntil(lambda: len(self.rows) == 1)
        self.save_button.click()  # the name is filled in already
        return self.result()

    monkeypatch.setattr(FaceNamingDialog, "exec", fake_exec)
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.people_list.setCurrentRow(0)
    w.face_setup_page.more_button.click()
    assert len(opened) == 1
    assert len(store.people()[0].embeddings) == 2
    assert w.current_page() is w.face_setup_page  # no sort started


def test_settings_gets_the_windows_people_store(make_window, monkeypatch, store):
    opened = []
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: opened.append(self))
    w = make_window()
    w.settings_button.click()
    assert opened[0].people_store is store is w.people_store


def test_naming_cancelled_after_a_partial_save_does_not_start(qtbot, make_window, photos,
                                                              store, make_jpeg, tmp_path,
                                                              models_present, monkeypatch):
    me = make_jpeg(tmp_path / "faces" / "two.jpg", size=(200, 200))
    engine = _two_faces(me)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(me), ""))

    def fake_exec(self):
        qtbot.waitUntil(lambda: len(self.rows) == 2)
        self.rows[0].name_edit.setText("Atanu")
        self.rows[1].name_edit.setText("Duplicates")  # refused
        self.save_button.click()
        self.reject()
        return self.result()

    monkeypatch.setattr(FaceNamingDialog, "exec", fake_exec)
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.add_button.click()
    assert [p.name for p in store.people()] == ["Atanu"]
    assert w.current_page() is w.face_setup_page
    assert _rows(w.face_setup_page) == [("Atanu", True)]
    assert w._task is None
