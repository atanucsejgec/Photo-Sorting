import gc
import threading
import weakref
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDropEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from photosort.faces import Face
from photosort.modes.face_mode import FaceScan, Question
from photosort.people import PeopleStore
from photosort.settings import Settings, load_settings
from photosort.ui import dialogs, face_question_page, main_window
from photosort.ui.face_naming_dialog import FaceNamingDialog
from photosort.ui.face_question_page import FaceQuestionPage
from photosort.ui.face_setup_page import FaceSetupPage
from photosort.ui.flows import face as face_flow
from photosort.ui.main_window import MainWindow
from photosort.ui.people_page import PeoplePage

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
    """dialogs.ask answers with asked.answer; the texts go in asked.texts, the
    defaults in asked.defaults."""

    class Asked:
        answer = True
        texts: list[str] = []
        defaults: list[str] = []

    record = Asked()
    record.texts = []
    record.defaults = []

    def ask(parent, title, text, yes, no, default="no"):
        record.texts.append(text)
        record.defaults.append(default)
        return record.answer

    monkeypatch.setattr(dialogs, "ask", ask)
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


def _make_page(qtbot) -> FaceSetupPage:
    page = FaceSetupPage()
    qtbot.addWidget(page)
    return page


def _tick(page: FaceSetupPage, *names: str) -> None:
    for name in names:
        _item(page, name).setCheckState(Qt.CheckState.Checked)


def test_setup_page_lists_people_unticked(qtbot, store):
    zoe = _remember(store, "Zoe", 2)
    atanu = _remember(store, "Atanu", 1)
    page = _make_page(qtbot)
    page.load(store)
    assert _rows(page) == [("Atanu", False), ("Zoe", False)]
    assert not _item(page, "Atanu").icon().isNull()  # the remembered face picture
    assert page.add_button.text() == "Add person…"
    assert page.start_button.text() == "Start sorting"
    assert not page.start_button.isEnabled()  # nobody ticked yet
    started = []
    page.start.connect(started.append)
    _tick(page, "Zoe", "Atanu")
    assert page.start_button.isEnabled()
    page.start_button.click()
    assert started == [[atanu.id, zoe.id]]


def test_setup_page_comes_up_unticked_every_time(qtbot, store):
    _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    page = _make_page(qtbot)
    page.load(store)
    _tick(page, "Atanu")
    assert page.start_button.isEnabled()
    page.load(store)  # opened again
    assert _rows(page) == [("Atanu", False), ("Zoe", False)]
    assert not page.start_button.isEnabled()
    _tick(page, "Zoe")
    _item(page, "Zoe").setCheckState(Qt.CheckState.Unchecked)
    assert not page.start_button.isEnabled()


def test_setup_page_ticks_the_people_asked_for(qtbot, store):
    _remember(store, "Atanu", 1)
    zoe = _remember(store, "Zoe", 2)
    page = _make_page(qtbot)
    page.load(store, ticked=[zoe.id, "f" * 32])  # someone not remembered is no row
    assert _rows(page) == [("Atanu", False), ("Zoe", True)]
    assert page.start_button.isEnabled()


def test_setup_page_buttons_name_the_selected_person(qtbot, store):
    atanu = _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    page = _make_page(qtbot)
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


def test_setup_page_buttons_show_an_ampersand_in_a_name(qtbot, store):
    _remember(store, "Ben&Jo", 1)  # " & " is refused; "&" alone is not
    page = _make_page(qtbot)
    page.load(store)
    page.people_list.setCurrentItem(_item(page, "Ben&Jo"))
    for button in (page.more_button, page.delete_button):
        assert "Ben&Jo" in button.text().replace("&&", "&")
        assert QKeySequence.mnemonic(button.text()).isEmpty()


def test_setup_page_with_nobody_remembered(qtbot, store):
    page = _make_page(qtbot)
    page.load(store)
    assert page.people_list.count() == 0
    assert not page.start_button.isEnabled()
    assert not page.more_button.isEnabled()
    assert not page.delete_button.isEnabled()
    assert page.add_button.isEnabled()


def _drop_events(path: Path) -> tuple[QDropEvent, QDropEvent, QMimeData]:
    """(drag enter, drop, their data) for dragging path in; keep the data
    alive while the events are used. The enter event is a QDropEvent too: it
    has all the handler uses, without QDragEnterEvent's deprecated constructor."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    events = []
    for _ in range(2):
        event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        event.setAccepted(False)
        events.append(event)
    return events[0], events[1], mime


def test_setup_page_takes_a_dropped_photo(qtbot, store, photo, tmp_path):
    page = _make_page(qtbot)
    page.load(store)
    assert page.acceptDrops()
    dropped = []
    page.photo_dropped.connect(dropped.append)
    enter, drop, _mime = _drop_events(photo)
    page.dragEnterEvent(enter)
    assert enter.isAccepted()
    page.dropEvent(drop)
    assert dropped == [photo]

    notes = tmp_path / "notes.txt"
    notes.write_text("not a photo")
    for path in (notes, tmp_path / "gone.jpg"):
        enter, drop, _mime = _drop_events(path)
        page.dragEnterEvent(enter)
        assert not enter.isAccepted()
        page.dropEvent(drop)
    assert dropped == [photo]


def test_remembered_people_list_takes_no_drops(qtbot, store):
    page = FaceSetupPage(delete_only=True)
    qtbot.addWidget(page)
    assert not page.acceptDrops()


def test_forget_asks_first_then_removes(qtbot, store, asked):
    _remember(store, "Atanu", 1)
    _remember(store, "Bob", 3)
    _remember(store, "Zoe", 2)
    page = _make_page(qtbot)
    page.load(store)
    _tick(page, "Atanu", "Zoe")
    page.people_list.setCurrentItem(_item(page, "Zoe"))

    asked.answer = False
    page.delete_button.click()
    assert len(asked.texts) == 1 and "Zoe" in asked.texts[0]
    assert asked.defaults == ["no"]  # Enter alone forgets nobody
    assert [p.name for p in store.people()] == ["Atanu", "Bob", "Zoe"]

    asked.answer = True
    page.delete_button.click()
    assert [p.name for p in store.people()] == ["Atanu", "Bob"]
    assert _rows(page) == [("Atanu", True), ("Bob", False)]  # the other ticks kept


def test_the_people_page_is_delete_only(qtbot, store, asked):
    _remember(store, "Atanu", 1)
    page = PeoplePage(store)
    qtbot.addWidget(page)
    page.load()
    setup = page.list_page
    assert page.people_list.count() == 1
    item = page.people_list.item(0)
    assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    for button in (setup.add_button, setup.more_button, setup.start_button, setup.back_button):
        assert button.isHidden()
    page.people_list.setCurrentRow(0)
    page.delete_button.click()
    assert store.people() == []
    assert page.people_list.count() == 0


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


def _three_faces(photo) -> FakeEngine:
    return FakeEngine({photo.name: [_face(_unit(1), 10), _face(_unit(2), 70),
                                    _face(_unit(3), 130)]})


def test_naming_dialog_blank_faces_are_skipped(qtbot, store, photo):
    d = _make_dialog(qtbot, photo, _three_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 3)
    d.rows[1].name_edit.setText("Zoe")
    d.rows[2].name_edit.setText(" . ")  # no name to save either
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert [r.error_label.text() for r in d.rows] == ["", "", ""]
    (zoe,) = store.people()
    assert zoe.name == "Zoe" and np.array_equal(zoe.embeddings[0], _unit(2))
    assert [p.name for p in d.added()] == ["Zoe"]


def test_naming_dialog_adds_photos_from_a_group_photo(qtbot, store, photo):
    atanu = _remember(store, "Atanu", 1)
    d = _make_dialog(qtbot, photo, _three_faces(photo), store, name="Atanu")
    qtbot.waitUntil(lambda: len(d.rows) == 3)
    assert d.windowTitle() == "Add photos to Atanu"
    assert [r.name() for r in d.rows] == ["", "", ""]  # which face is theirs? ask
    d.rows[2].name_edit.setText("Atanu")
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    (person,) = store.people()
    assert person.id == atanu.id and len(person.embeddings) == 2
    assert np.array_equal(person.embeddings[1], _unit(3))


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


def test_naming_dialog_matches_a_name_typed_another_way(qtbot, store, photo):
    joy = _remember(store, "\u099c\u09af\u09bc", 2)  # জয়, as য plus the nukta
    d = _make_dialog(qtbot, photo, _two_faces(photo), store)
    qtbot.waitUntil(lambda: len(d.rows) == 2)
    d.rows[0].name_edit.setText("\u099c\u09df")  # জয়, য় as one character
    d.rows[1].skip_check.setChecked(True)
    d.save_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    (person,) = store.people()
    assert person.id == joy.id and len(person.embeddings) == 2


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
    """The page, shown, its photo focused, so its keys work."""
    root, scan, people = asking
    page = FaceQuestionPage()
    qtbot.addWidget(page)
    page.resize(900, 600)
    page.load(root, scan, people)
    page.show()
    qtbot.waitExposed(page)
    page.activateWindow()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is page.photo_view)
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
    qtbot.keyClick(page, Qt.Key.Key_Space)
    a, b_atanu, b_zoe = _keys(scan)
    assert done == [{a: "yes", b_atanu: "no", b_zoe: "skip"}]


def test_buttons_answer_too(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    page.yes_button.click()
    page.no_button.click()
    page.skip_button.click()
    a, b_atanu, b_zoe = _keys(scan)
    assert done == [{a: "yes", b_atanu: "no", b_zoe: "skip"}]


def test_skip_passes_over_the_photos_other_questions(qtbot, asking, make_jpeg):
    root, scan, people = asking
    make_jpeg(root / "c.jpg", size=(200, 200))
    scan.questions.append(Question("c.jpg", people[1].id, "Zoe", (20.0, 20.0, 80.0, 80.0),
                                   (200, 200), 0.40, 63))
    a, b_atanu, b_zoe, c = _keys(scan)
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    qtbot.keyClick(page, Qt.Key.Key_Space)  # b.jpg stays put: Zoe in it needn't be asked
    assert page.counter_label.text() == "4 of 4"
    assert page.photo_view.toolTip() == "c.jpg"
    page.back_button.click()  # back onto the question passed over
    assert page.counter_label.text() == "3 of 4"
    assert page.answer_label.text() == ""
    page.back_button.click()
    assert page.answer_label.text() == "Your answer: Skip"
    qtbot.keyClick(page, Qt.Key.Key_N)  # not skipped after all: Zoe is asked
    assert page.counter_label.text() == "3 of 4"
    qtbot.keyClick(page, Qt.Key.Key_Y)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    assert done == [{a: "yes", b_atanu: "no", b_zoe: "yes", c: "yes"}]


def test_skip_on_the_last_photo_finishes(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_N)
    qtbot.keyClick(page, Qt.Key.Key_Space)
    a, b_atanu, _ = _keys(scan)
    assert done == [{a: "no", b_atanu: "skip"}]


def test_keys_with_a_modifier_do_nothing(qtbot, asking):
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    for key, modifier in ((Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier),
                          (Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier),
                          (Qt.Key.Key_N, Qt.KeyboardModifier.AltModifier),
                          (Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier),
                          (Qt.Key.Key_Escape, Qt.KeyboardModifier.ControlModifier)):
        qtbot.keyClick(page, key, modifier)
        assert page.counter_label.text() == "2 of 3"
        assert page.answer_label.text() == ""
    qtbot.keyClick(page, Qt.Key.Key_Escape)
    assert page.counter_label.text() == "1 of 3"  # Esc: back a question
    assert done == []


def test_previous_lets_an_answer_be_changed(qtbot, asking):
    root, scan, _ = asking
    page = _question_page(qtbot, asking)
    done = []
    page.finished.connect(done.append)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    qtbot.keyClick(page, Qt.Key.Key_Y)
    qtbot.keyClick(page, Qt.Key.Key_Escape)
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
    for key in (Qt.Key.Key_Y, Qt.Key.Key_N, Qt.Key.Key_Space):
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
    w.mode_buttons["face"].click()


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
    assert _rows(w.face_setup_page) == [("Atanu", False)]
    assert not w.face_setup_page.start_button.isEnabled()
    w.face_setup_page.back_button.click()
    assert w.current_page() is w.home


def test_ticks_saved_by_an_older_version_are_ignored(qtbot, photos, store, settings_path,
                                                   models_present):
    atanu = _remember(store, "Atanu", 1)
    w = MainWindow(Settings(face_people=[atanu.id]), settings_path, people_store=store)
    qtbot.addWidget(w)
    _to_setup(w, photos)
    assert _rows(w.face_setup_page) == [("Atanu", False)]
    assert not w.face_setup_page.start_button.isEnabled()
    w.close()


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
    _tick(w.face_setup_page, "Atanu", "Zoe")
    w.face_setup_page.start_button.click()
    assert w.current_page() is w.progress_page
    qtbot.waitUntil(lambda: w.progress_label.text() == "Looking for faces… 1 of 3")
    engine.hold["b.jpg"].set()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert gpu == [w.settings.use_gpu]
    # Two ticked: only their group photo is sorted; Atanu alone in a.jpg stays.
    assert w.summary_label.text().splitlines() == [
        "1 photos → 0 person folders, 1 together folders",
        "2 photos stay where they are",
    ]
    assert [(m.src, m.dst) for m in w._plan.moves] == [("b.jpg", "Atanu & Zoe/b.jpg")]
    assert (photos / ".photosort" / "cache.db").is_file()  # the analysis cache was used
    assert errors == []

    w.back_button.click()
    assert w.current_page() is w.face_setup_page
    assert _rows(w.face_setup_page) == [("Atanu", True), ("Zoe", True)]  # back, not anew


def test_face_sort_with_one_ticked_person(qtbot, make_window, photos, store, make_jpeg,
                                          models_present, settings_path):
    _remember(store, "Atanu", 1)
    _remember(store, "Zoe", 2)
    make_jpeg(photos / "a.jpg")
    make_jpeg(photos / "b.jpg")
    engine = FakeEngine({"a.jpg": [_face(_unit(2))],
                         "b.jpg": [_face(_unit(1)), _face(_unit(2), 110)]})
    w = make_window(engine)
    _to_setup(w, photos)
    _tick(w.face_setup_page, "Zoe")
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert sorted((m.src, m.dst) for m in w._plan.moves) == [
        ("a.jpg", "Zoe/a.jpg"), ("b.jpg", "Zoe/b.jpg")]
    assert load_settings(settings_path).face_people == []  # the ticks are not remembered


def test_face_sort_asks_only_about_possible_group_photos(qtbot, make_window, photos,
                                                         store, make_jpeg, models_present):
    atanu = _remember(store, "Atanu", 1)
    zoe = _remember(store, "Zoe", 2)
    make_jpeg(photos / "a.jpg", size=(200, 200))
    make_jpeg(photos / "b.jpg", size=(200, 200))
    rather_zoe = _towards(zoe.embeddings[0], _unit(7), 0.7)
    engine = FakeEngine({"a.jpg": [_face(rather_zoe)],  # Zoe alone could be no group
                         "b.jpg": [_face(atanu.embeddings[0]), _face(rather_zoe, 110)]})
    w = make_window(engine)
    _to_setup(w, photos)
    _tick(w.face_setup_page, "Atanu", "Zoe")
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    page = w.face_question_page
    assert page.heading_label.text().startswith("Is this Zoe? ")
    assert page.counter_label.text() == "1 of 1"
    page.no_button.click()
    assert w.current_page() is w.summary_page
    assert w._plan.moves == []  # Atanu alone in b.jpg: it stays
    w.back_button.click()
    page.yes_button.click()
    assert [(m.src, m.dst) for m in w._plan.moves] == [("b.jpg", "Atanu & Zoe/b.jpg")]


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
    _tick(w.face_setup_page, "Atanu")
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
    atanu, engine = _one_unsure(store, photos, make_jpeg)
    make_jpeg(photos / "b.jpg")
    engine.faces["b.jpg"] = [_face(atanu.embeddings[0])]  # surely Atanu
    w = make_window(engine)
    _to_setup(w, photos)
    _tick(w.face_setup_page, "Atanu")
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    w.face_question_page.stop_button.click()
    assert w.current_page() is w.summary_page
    assert [(m.src, m.dst) for m in w._plan.moves] == [("b.jpg", "Atanu/b.jpg")]
    assert w.summary_label.text().splitlines()[1] == "1 photos stay where they are"
    assert w.go_button.isEnabled()


def test_back_from_summary_returns_to_the_questions(qtbot, make_window, photos, store,
                                                    make_jpeg, models_present):
    _, engine = _one_unsure(store, photos, make_jpeg)
    w = make_window(engine)
    _to_setup(w, photos)
    _tick(w.face_setup_page, "Atanu")
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
    _tick(w.face_setup_page, "Atanu")
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.face_question_page)
    w.face_question_page.cancel_button.click()
    assert w.current_page() is w.face_setup_page
    assert _rows(w.face_setup_page) == [("Atanu", True)]
    assert w._plan is None


def test_planning_error_after_questions_goes_home(qtbot, make_window, photos, store,
                                                  make_jpeg, models_present, errors,
                                                  monkeypatch):
    _, engine = _one_unsure(store, photos, make_jpeg)

    def unplugged(*args, **kwargs):
        raise OSError("the drive is gone")

    monkeypatch.setattr(face_flow, "plan_faces", unplugged)
    w = make_window(engine)
    _to_setup(w, photos)
    _tick(w.face_setup_page, "Atanu")
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
    _tick(w.face_setup_page, "Atanu")
    w.face_setup_page.start_button.click()
    qtbot.waitUntil(lambda: engine.calls == ["a.jpg"])
    w.cancel_button.click()
    engine.hold["a.jpg"].set()
    qtbot.waitUntil(lambda: w.current_page() is w.face_setup_page)
    assert _rows(w.face_setup_page) == [("Atanu", True)]
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
    _tick(w.face_setup_page, "Atanu")
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
    assert _rows(w.face_setup_page) == [("Atanu", True)]  # just named: ticked
    assert [(m.src, m.dst) for m in w._plan.moves] == [("a.jpg", "Atanu/a.jpg")]


def test_first_naming_of_several_people_waits_for_start(qtbot, make_window, photos, store,
                                                       make_jpeg, tmp_path, models_present,
                                                       monkeypatch):
    # Two or more ticked sorts group photos only: that is the owner's to start.
    me = make_jpeg(tmp_path / "faces" / "two.jpg", size=(200, 200))
    make_jpeg(photos / "a.jpg")
    engine = _two_faces(me)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(me), ""))
    _name_in_dialog(monkeypatch, qtbot, "Atanu", "Zoe")
    w = make_window(engine)
    _to_setup(w, photos)
    w.face_setup_page.add_button.click()
    assert [p.name for p in store.people()] == ["Atanu", "Zoe"]
    assert w.current_page() is w.face_setup_page
    assert _rows(w.face_setup_page) == [("Atanu", True), ("Zoe", True)]
    assert w.face_setup_page.start_button.isEnabled()
    assert w._task is None
    assert engine.calls == ["two.jpg"]  # no photo looked at for a sort


def test_photo_dropped_on_setup_names_its_faces(qtbot, make_window, photos, store,
                                               make_jpeg, tmp_path, models_present,
                                               monkeypatch):
    me = make_jpeg(tmp_path / "faces" / "me.jpg", size=(200, 200))
    make_jpeg(photos / "a.jpg")
    engine = FakeEngine({"me.jpg": [_face(_unit(1))], "a.jpg": [_face(_unit(1))]})

    def no_picker(*args, **kwargs):
        raise AssertionError("the dropped photo is used: no file picker")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", no_picker)
    opened = []
    _name_in_dialog(monkeypatch, qtbot, "Atanu", opened=opened)
    w = make_window(engine)
    _to_setup(w, photos)
    _, drop, _mime = _drop_events(me)
    w.face_setup_page.dropEvent(drop)
    assert opened[0].parent() is w
    assert engine.calls[0] == "me.jpg"
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)  # as with Add person…
    assert [p.name for p in store.people()] == ["Atanu"]


def test_naming_with_people_listed_waits_for_start(qtbot, make_window, photos, store,
                                                   make_jpeg, tmp_path, models_present,
                                                   monkeypatch, settings_path):
    _remember(store, "Atanu", 1)
    _remember(store, "Bob", 3)
    me = make_jpeg(tmp_path / "faces" / "zoe.jpg", size=(200, 200))
    engine = FakeEngine({"zoe.jpg": [_face(_unit(2))]})
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(me), ""))
    _name_in_dialog(monkeypatch, qtbot, "Zoe")
    w = make_window(engine)
    _to_setup(w, photos)
    assert _rows(w.face_setup_page) == [("Atanu", False), ("Bob", False)]
    _tick(w.face_setup_page, "Bob")
    w.face_setup_page.add_button.click()
    assert w.current_page() is w.face_setup_page
    # The new person comes back ticked; the ticks made before are kept.
    assert _rows(w.face_setup_page) == [("Atanu", False), ("Bob", True), ("Zoe", True)]
    assert w.face_setup_page.start_button.isEnabled()
    assert load_settings(settings_path).face_people == []


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
    _remember(store, "Bob", 3)
    w = make_window(engine)
    _to_setup(w, photos)
    _tick(w.face_setup_page, "Bob")
    w.face_setup_page.people_list.setCurrentItem(_item(w.face_setup_page, "Atanu"))
    w.face_setup_page.more_button.click()
    assert len(opened) == 1
    assert len(store.people()[0].embeddings) == 2
    assert w.current_page() is w.face_setup_page  # no sort started
    assert _rows(w.face_setup_page) == [("Atanu", False), ("Bob", True)]  # ticks unchanged


def test_the_people_page_shows_the_windows_store(make_window, store):
    _remember(store, "Atanu", 1)
    w = make_window()
    assert w.people_page.store is store is w.people_store
    w.sidebar.items["people"].click()
    assert w.current_page() is w.people_page
    assert w.people_page.people_list.count() == 1


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
