"""The Content and Notes & Pages screens: the review page, the category editor,
and the main window's flow with a fake CLIP engine (never the real model)."""

import math
import threading
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog

from photosort.ai.models import CLIP_MODELS
from photosort.modes import MODE_SPECS, content, notes
from photosort.modes.content import Verdict
from photosort.people import PeopleStore
from photosort.settings import DEFAULT_CONTENT_CATEGORIES, Settings
from photosort.ui import main_window
from photosort.ui.category_editor import CategoryEditor, CategoryNameDialog
from photosort.ui.category_review_page import LEAVE, CategoryReviewPage
from photosort.ui.main_window import MainWindow

CATEGORIES = list(DEFAULT_CONTENT_CATEGORIES)


# ----- the review page ---------------------------------------------------------

@pytest.fixture
def make_page(qtbot):
    pages = []

    def _make() -> CategoryReviewPage:
        page = CategoryReviewPage()
        qtbot.addWidget(page)
        pages.append(page)
        return page

    yield _make
    for page in pages:
        page.stop_thumbnails()
    QThreadPool.globalInstance().waitForDone()


def _verdicts(root: Path, make_jpeg) -> list[Verdict]:
    make_jpeg(root / "a.jpg", size=(64, 48))
    make_jpeg(root / "sub" / "b.jpg", size=(64, 48))
    return [
        Verdict("a.jpg", "Food", 0.634, (("Food", 0.634), ("Vehicles", 0.2))),
        Verdict("sub/b.jpg", "Vehicles", 0.5, (("Vehicles", 0.5), ("Food", 0.3))),
    ]


def _combo_texts(page) -> list[str]:
    return [card.choice_combo.currentText() for card in page.cards]


def test_review_cards_and_presets(qtbot, make_page, tmp_path, make_jpeg):
    page = make_page()
    page.load(tmp_path, _verdicts(tmp_path, make_jpeg), ["Food", "Vehicles", "Pets"])
    assert page.heading_label.text() == "2 photos to check"
    a, b = page.cards
    assert a.name_label.text() == "a.jpg"
    assert b.name_label.text() == "b.jpg"
    assert b.name_label.toolTip() == "sub/b.jpg"
    assert a.guess_label.text() == "Food? 63% sure"  # the best's share, never a sum
    assert b.guess_label.text() == "Vehicles? 50% sure"
    assert [a.choice_combo.itemText(i) for i in range(a.choice_combo.count())] == [
        "Food", "Vehicles", "Pets", LEAVE]
    assert LEAVE == "Leave it where it is"
    assert _combo_texts(page) == ["Food", "Vehicles"]
    assert page.leave_all_button.text() == "Leave all where they are"
    assert page.back_button.text() == "Back"
    assert page.continue_button.text() == "Continue"
    # the thumbnails arrive from pool threads
    qtbot.waitUntil(lambda: all(not c.thumb_label.pixmap().isNull() for c in page.cards))


def test_review_title(make_page, tmp_path, make_jpeg):
    page = make_page()
    verdicts = _verdicts(tmp_path, make_jpeg)
    page.load(tmp_path, verdicts, ["Food"], title="2 photos might be notes or pages")
    assert page.heading_label.text() == "2 photos might be notes or pages"
    page.load(tmp_path, verdicts[:1], ["Food"])
    assert page.heading_label.text() == "1 photo to check"


def test_review_presets_previous_answers(make_page, tmp_path, make_jpeg):
    page = make_page()
    page.load(tmp_path, _verdicts(tmp_path, make_jpeg), ["Food", "Vehicles", "Pets"],
              {"a.jpg": "Pets", "sub/b.jpg": None})
    assert _combo_texts(page) == ["Pets", LEAVE]


def test_review_leave_all(make_page, tmp_path, make_jpeg):
    page = make_page()
    page.load(tmp_path, _verdicts(tmp_path, make_jpeg), ["Food", "Vehicles"])
    page.leave_all_button.click()
    assert _combo_texts(page) == [LEAVE, LEAVE]


def test_review_continue_emits_the_answers(make_page, tmp_path, make_jpeg):
    page = make_page()
    page.load(tmp_path, _verdicts(tmp_path, make_jpeg), ["Food", "Vehicles", "Pets"])
    results = []
    page.finished.connect(results.append)
    page.cards[0].choice_combo.setCurrentText("Pets")
    page.cards[1].choice_combo.setCurrentText(LEAVE)
    page.continue_button.click()
    assert results == [{"a.jpg": "Pets", "sub/b.jpg": None}]


def test_review_back_cancels(make_page, tmp_path, make_jpeg):
    page = make_page()
    page.load(tmp_path, _verdicts(tmp_path, make_jpeg), ["Food", "Vehicles"])
    cancelled, finished = [], []
    page.cancelled.connect(lambda: cancelled.append(True))
    page.finished.connect(finished.append)
    page.back_button.click()
    assert cancelled == [True]
    assert finished == []


def test_review_old_thumbnails_are_ignored(qtbot, make_page, tmp_path, make_jpeg):
    page = make_page()
    verdicts = _verdicts(tmp_path, make_jpeg)
    page.load(tmp_path, verdicts, ["Food", "Vehicles"])
    page.load(tmp_path, verdicts[:1], ["Food", "Vehicles"])
    old = page._generation - 1
    page._thumbnail_ready((old, 0, QImage(8, 8, QImage.Format.Format_RGB888)))
    assert page.cards[0].thumb_label.pixmap().isNull()  # not the old one's
    qtbot.waitUntil(lambda: not page.cards[0].thumb_label.pixmap().isNull())


def test_review_double_click_shows_the_photo_large(qtbot, make_page, tmp_path, make_jpeg):
    page = make_page()
    page.load(tmp_path, _verdicts(tmp_path, make_jpeg), ["Food", "Vehicles"])
    page.cards[1].thumb_label.double_clicked.emit()
    preview = page.preview
    assert preview is not None and preview.isVisible()
    assert preview.windowTitle() == "b.jpg"
    qtbot.waitUntil(lambda: preview.photo is not None)
    assert (preview.photo.width(), preview.photo.height()) == (64, 48)
    preview.reject()
    assert page.preview is None


# ----- the category editor -----------------------------------------------------

@pytest.fixture
def editor(qtbot):
    e = CategoryEditor(["Food", "Pets", "Cars"])
    qtbot.addWidget(e)
    return e


def _answer_with(monkeypatch, *texts):
    """Make each name dialog that opens type the next of texts and press OK."""
    asked = []
    answers = list(texts)

    def exec_(dialog):
        asked.append((dialog.windowTitle(), dialog.name_edit.text()))
        dialog.name_edit.setText(answers.pop(0))
        dialog.ok_button.click()  # does nothing while the name is refused
        return dialog.result()

    monkeypatch.setattr(CategoryNameDialog, "exec", exec_)
    return asked


def _names(e: CategoryEditor) -> list[str]:
    return [e.list_widget.item(i).text() for i in range(e.list_widget.count())]


def test_editor_buttons(editor):
    assert editor.categories() == ["Food", "Pets", "Cars"]
    assert _names(editor) == ["Food", "Pets", "Cars"]
    assert [b.text() for b in (editor.add_button, editor.rename_button, editor.remove_button,
                               editor.up_button, editor.down_button, editor.reset_button)] == [
        "Add…", "Rename…", "Remove", "Move up", "Move down", "Reset to defaults"]


@pytest.mark.parametrize("name, others, problem", [
    ("", ["Food"], "Type a name."),
    ("   ", ["Food"], "Type a name."),
    ("?", ["Food"], "Use at least one letter or number."),
    ("food", ["Food"], "There's already a category called Food."),
    ("Bad Photos", ["Food"], "Photo Sorter uses that name for its own folder. Pick another name."),
    (".hidden", [], "Photo Sorter uses that name for its own folder. Pick another name."),
    ("x" * 51, [], "That name is too long."),
])
def test_name_dialog_refuses(qtbot, name, others, problem):
    d = CategoryNameDialog("Add a category", "", others)
    qtbot.addWidget(d)
    d.name_edit.setText(name)
    assert d.message_label.text() == problem
    assert not d.ok_button.isEnabled()
    d.ok_button.click()
    assert d.result() != QDialog.DialogCode.Accepted


def test_name_dialog_accepts_a_good_name(qtbot):
    d = CategoryNameDialog("Add a category", "", ["Food"])
    qtbot.addWidget(d)
    d.name_edit.setText("  Meals ")
    assert d.message_label.text() == ""
    assert d.ok_button.isEnabled()
    assert d.name() == "Meals"
    d.name_edit.setText("x" * 50)
    assert d.ok_button.isEnabled()


def test_editor_add(editor, monkeypatch):
    asked = _answer_with(monkeypatch, "Meals")
    editor.add_button.click()
    assert asked == [("Add a category", "")]
    assert editor.categories() == ["Food", "Pets", "Cars", "Meals"]
    assert editor.list_widget.currentRow() == 3


def test_editor_add_refused_keeps_the_list(editor, monkeypatch):
    _answer_with(monkeypatch, "FOOD")
    editor.add_button.click()
    assert editor.categories() == ["Food", "Pets", "Cars"]


def test_editor_refuses_a_31st_name(qtbot, monkeypatch):
    e = CategoryEditor([f"Category {n}" for n in range(30)])
    qtbot.addWidget(e)
    asked = _answer_with(monkeypatch, "One more")
    e.add_button.click()
    assert asked == []  # not even asked
    assert e.message_label.text() == "You can have up to 30 categories."
    assert len(e.categories()) == 30


def test_editor_rename(editor, monkeypatch):
    asked = _answer_with(monkeypatch, "pets & animals")
    editor.list_widget.setCurrentRow(1)
    editor.rename_button.click()
    assert asked == [("Rename the category", "Pets")]
    assert editor.categories() == ["Food", "pets & animals", "Cars"]
    _answer_with(monkeypatch, "PETS & ANIMALS")  # its own name in other case: fine
    editor.rename_button.click()
    assert editor.categories() == ["Food", "PETS & ANIMALS", "Cars"]
    _answer_with(monkeypatch, "cars")  # another's name: refused
    editor.rename_button.click()
    assert editor.categories() == ["Food", "PETS & ANIMALS", "Cars"]


def test_editor_remove_and_move(editor):
    editor.list_widget.setCurrentRow(0)
    assert not editor.up_button.isEnabled()
    editor.down_button.click()
    assert editor.categories() == ["Pets", "Food", "Cars"]
    assert editor.list_widget.currentRow() == 1
    editor.down_button.click()
    assert editor.categories() == ["Pets", "Cars", "Food"]
    assert not editor.down_button.isEnabled()
    editor.up_button.click()
    assert editor.categories() == ["Pets", "Food", "Cars"]
    editor.remove_button.click()
    assert editor.categories() == ["Pets", "Cars"]
    editor.remove_button.click()
    assert editor.categories() == ["Pets"]
    assert not editor.remove_button.isEnabled()  # one is always left
    editor.remove_button.click()
    assert editor.categories() == ["Pets"]


def test_editor_reset_to_defaults(editor):
    editor.list_widget.setCurrentRow(0)
    editor.remove_button.click()
    editor.reset_button.click()
    assert editor.categories() == CATEGORIES
    assert editor.remove_button.isEnabled()


def test_editor_message_clears(qtbot, monkeypatch):
    e = CategoryEditor([f"Category {n}" for n in range(30)])
    qtbot.addWidget(e)
    e.add_button.click()
    assert e.message_label.text()
    e.list_widget.setCurrentRow(0)
    e.remove_button.click()
    assert e.message_label.text() == ""


# ----- the main window ---------------------------------------------------------

def test_modes_are_run_by_the_window():
    for key in ("content", "notes"):
        assert MODE_SPECS[key].kind == key
        with pytest.raises(NotImplementedError):
            MODE_SPECS[key].prepare(Path("."), False)


_DIM = 512


class _Clip:
    """A CLIP engine that knows each prompt as one axis and each photo, by file
    name, as its similarity to some prompts (the rest of its length on an axis
    no prompt uses)."""

    def __init__(self, prompts: list[str], photos: dict[str, dict[str, float]]) -> None:
        assert len(prompts) < _DIM
        self.axis = {text: n for n, text in enumerate(prompts)}
        self.photos = photos
        self.texts_asked: list[str] = []

    def image_embedding(self, path: Path) -> np.ndarray:
        v = np.zeros(_DIM, np.float32)
        for text, similarity in self.photos.get(Path(path).name, {}).items():
            v[self.axis[text]] = similarity
        v[-1] = np.sqrt(1 - float(v @ v))
        return v

    def text_embeddings(self, texts: list[str]) -> np.ndarray:
        self.texts_asked += texts
        out = np.zeros((len(texts), _DIM), np.float32)
        for row, text in enumerate(texts):
            out[row, self.axis[text]] = 1
        return out


@pytest.fixture
def errors(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, text: shown.append(text))
    return shown


@pytest.fixture
def models_asked(monkeypatch):
    asked = []

    def ensure(parent, keys):
        asked.append(list(keys))
        return True

    monkeypatch.setattr(main_window, "ensure_models", ensure)
    return asked


@pytest.fixture
def make_window(qtbot, tmp_path, errors):
    windows = []

    def _make(engine, settings: Settings | None = None) -> MainWindow:
        w = MainWindow(settings or Settings(), tmp_path / "config" / "settings.json",
                       people_store=PeopleStore(tmp_path / "people"))
        qtbot.addWidget(w)
        threads = []

        def factory(use_gpu: bool):
            threads.append(threading.current_thread())
            return engine

        w.clip_engine_factory = factory
        w.engine_threads = threads
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def _start(w, photos, mode):
    w.folder_edit.setText(str(photos))
    w.mode_buttons[mode].setChecked(True)
    w.start_button.click()


def _content_engine() -> _Clip:
    prompts = [text for parts in content.prompt_sets(CATEGORIES) for text in parts]
    # Buildings & City is the unit mean of its two parts' axes, so a photo
    # with s on each part has s * sqrt(2) on the category.
    building = 0.298 / math.sqrt(2)
    return _Clip(prompts + content.background_prompts(), {
        "food.jpg": {"a photo of food": 0.3},  # sure
        # unsure: about 55 % vehicles, 45 % buildings & city
        "street.jpg": {"a photo of vehicles": 0.3, "a photo of buildings": building,
                       "a photo of city": building},
        "wall.jpg": {"a photo of something else": 0.3},  # none of the categories
    })


def _make_content_photos(photos, make_jpeg):
    for name in ("food.jpg", "street.jpg", "wall.jpg"):
        make_jpeg(photos / name, size=(64, 48))


def test_content_end_to_end(qtbot, make_window, photos, make_jpeg, errors, models_asked):
    _make_content_photos(photos, make_jpeg)
    engine = _content_engine()
    w = make_window(engine)
    _start(w, photos, "content")
    assert models_asked == [CLIP_MODELS]
    assert w.current_page() is w.progress_page
    qtbot.waitUntil(lambda: w.current_page() is w.category_review_page)
    assert w.engine_threads and w.engine_threads[0] is not threading.main_thread()
    page = w.category_review_page
    assert page.heading_label.text() == "1 photo to check"
    [card] = page.cards
    assert card.verdict.rel == "street.jpg"
    assert card.guess_label.text() == "Vehicles? 55% sure"
    assert card.choice_combo.currentText() == "Vehicles"
    card.choice_combo.setCurrentText("Buildings & City")
    page.continue_button.click()

    assert w.current_page() is w.summary_page
    assert w.summary_label.text().splitlines() == [
        "2 photos → 2 category folders", "1 photos stay where they are"]
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert "Moved 2 files." in w.done_label.text()
    assert (photos / "Food" / "food.jpg").is_file()
    assert (photos / "Buildings & City" / "street.jpg").is_file()
    assert (photos / "wall.jpg").is_file()

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert sorted(p.name for p in photos.iterdir() if p.name != ".photosort") == [
        "food.jpg", "street.jpg", "wall.jpg"]
    assert errors == []


def test_content_uses_the_categories_in_settings(qtbot, make_window, photos, make_jpeg,
                                                 models_asked):
    _make_content_photos(photos, make_jpeg)
    engine = _content_engine()
    w = make_window(engine, Settings(content_categories=["Food", "Vehicles"]))
    _start(w, photos, "content")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert engine.texts_asked == [
        "a photo of food", "a photo of vehicles", "a photo of something else"]
    # street.jpg: vehicles beats buildings, which isn't a category any more
    assert (w.summary_label.text().splitlines()[0]) == "2 photos → 2 category folders"


def test_back_from_the_summary_keeps_the_answers(qtbot, make_window, photos, make_jpeg,
                                                 models_asked):
    _make_content_photos(photos, make_jpeg)
    w = make_window(_content_engine())
    _start(w, photos, "content")
    qtbot.waitUntil(lambda: w.current_page() is w.category_review_page)
    w.category_review_page.cards[0].choice_combo.setCurrentText(LEAVE)
    w.category_review_page.continue_button.click()
    assert w.summary_label.text().splitlines()[0] == "1 photos → 1 category folders"
    w.back_button.click()
    assert w.current_page() is w.category_review_page
    assert _combo_texts(w.category_review_page) == [LEAVE]
    w.category_review_page.continue_button.click()
    assert w.current_page() is w.summary_page
    assert w.go_button.isEnabled()


def test_changed_answer_after_back_is_planned(qtbot, make_window, photos, make_jpeg,
                                              models_asked):
    _make_content_photos(photos, make_jpeg)
    w = make_window(_content_engine())
    _start(w, photos, "content")
    qtbot.waitUntil(lambda: w.current_page() is w.category_review_page)
    w.category_review_page.continue_button.click()  # as suggested
    assert ("street.jpg", "Vehicles/street.jpg") in [(m.src, m.dst) for m in w._plan.moves]
    w.back_button.click()
    w.category_review_page.cards[0].choice_combo.setCurrentText("Buildings & City")
    w.category_review_page.continue_button.click()
    assert w.current_page() is w.summary_page
    assert [(m.src, m.dst) for m in w._plan.moves] == [
        ("food.jpg", "Food/food.jpg"), ("street.jpg", "Buildings & City/street.jpg")]

    w.back_button.click()
    w.category_review_page.back_button.click()
    assert w.current_page() is w.home
    _start(w, photos, "content")  # a new sort starts from the suggestions
    qtbot.waitUntil(lambda: w.current_page() is w.category_review_page)
    assert _combo_texts(w.category_review_page) == ["Vehicles"]


def test_cancel_after_the_photos_never_opens_the_text_model(qtbot, make_window, photos,
                                                            make_jpeg, models_asked,
                                                            monkeypatch):
    _make_content_photos(photos, make_jpeg)
    engine = _content_engine()
    w = make_window(engine)
    real_embed = main_window.embed_photos

    def embed_then_cancel(*args, cancel=None, **kwargs):
        scan = real_embed(*args, cancel=cancel, **kwargs)
        cancel.set()  # Cancel pressed just after the last photo
        return scan

    monkeypatch.setattr(main_window, "embed_photos", embed_then_cancel)
    _start(w, photos, "content")
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    QThreadPool.globalInstance().waitForDone()
    assert engine.texts_asked == []


def test_sort_by_clip_judges_a_category_by_the_mean_of_its_parts(tmp_path):
    (tmp_path / "cat.jpg").write_bytes(b"not really a photo")
    # Only on "animals": 0.3 / sqrt(2) on Pets & Animals, a logit 21.2 against
    # Food's 21.2 - ln(0.55 / 0.45): Pets & Animals 55 %, Food 45 %.
    food = 0.3 / math.sqrt(2) - math.log(0.55 / 0.45) / 100
    engine = _Clip(["a photo of pets", "a photo of animals", "a photo of food",
                    "a photo of something else"],
                   {"cat.jpg": {"a photo of animals": 0.3, "a photo of food": food}})
    choices = ["Pets & Animals", "Food"]
    result = main_window._sort_by_clip(tmp_path, False, "content", choices,
                                       lambda use_gpu: engine, False)
    assert engine.texts_asked == ["a photo of pets", "a photo of animals", "a photo of food",
                                  "a photo of something else"]
    assert result.sure == {}
    [verdict] = result.unsure
    assert (verdict.rel, verdict.best) == ("cat.jpg", "Pets & Animals")
    assert verdict.prob == pytest.approx(0.55, abs=1e-4)
    assert [name for name, _ in verdict.ranked] == ["Pets & Animals", "Food"]


def test_review_back_goes_home(qtbot, make_window, photos, make_jpeg, models_asked):
    _make_content_photos(photos, make_jpeg)
    w = make_window(_content_engine())
    _start(w, photos, "content")
    qtbot.waitUntil(lambda: w.current_page() is w.category_review_page)
    w.category_review_page.back_button.click()
    assert w.current_page() is w.home


def _notes_engine() -> _Clip:
    return _Clip(notes.PAPER_PROMPTS + notes.OTHER_PROMPTS, {
        "receipt.jpg": {"a photo of a shop receipt": 0.3},  # sure
        "people.jpg": {"a photo of people": 0.3},  # not paper
    })


def test_notes_end_to_end(qtbot, make_window, photos, make_jpeg, errors, models_asked):
    for name in ("receipt.jpg", "people.jpg"):
        make_jpeg(photos / name, size=(64, 48))
    w = make_window(_notes_engine())
    shown = []
    w.stack.currentChanged.connect(lambda _: shown.append(w.current_page()))
    _start(w, photos, "notes")
    assert models_asked == [CLIP_MODELS]
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert w.category_review_page not in shown  # nothing unsure: no review
    assert w.summary_label.text().splitlines() == [
        "1 photos → Notes & Pages", "1 photos stay where they are"]

    w.back_button.click()  # no review to go back to
    assert w.current_page() is w.home
    _start(w, photos, "notes")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    w.go_button.click()
    qtbot.waitUntil(lambda: w.current_page() is w.done_page)
    assert (photos / "Notes & Pages" / "Receipts" / "receipt.jpg").is_file()
    assert (photos / "people.jpg").is_file()

    w.undo_button.click()
    qtbot.waitUntil(lambda: "Undo complete" in w.undo_label.text())
    assert (photos / "receipt.jpg").is_file()
    assert not (photos / "Notes & Pages").exists()
    assert errors == []


def test_notes_review_offers_the_kinds(qtbot, make_window, photos, make_jpeg, models_asked):
    make_jpeg(photos / "page.jpg", size=(64, 48))
    # paper about 0.63 (unsure): a book page, then a printed document
    engine = _Clip(notes.PAPER_PROMPTS + notes.OTHER_PROMPTS, {"page.jpg": {
        "a photo of a page from a book": 0.3, "a photo of a printed document": 0.296,
        "a photo of an object": 0.3}})
    w = make_window(engine)
    _start(w, photos, "notes")
    qtbot.waitUntil(lambda: w.current_page() is w.category_review_page)
    assert w.category_review_page.heading_label.text() == "1 photo might be notes or pages"
    [card] = w.category_review_page.cards
    combo = card.choice_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [*notes.KINDS, LEAVE]
    assert combo.currentText() == "Book Pages"
    assert card.guess_label.text() == "Book Pages? 63% sure"  # the paper probability
    combo.setCurrentText("Printed Documents")
    w.category_review_page.continue_button.click()
    plan = w._plan
    assert [(m.src, m.dst) for m in plan.moves] == [
        ("page.jpg", "Notes & Pages/Printed Documents/page.jpg")]


def test_models_missing_goes_home(qtbot, make_window, photos, make_jpeg, monkeypatch):
    make_jpeg(photos / "a.jpg")
    monkeypatch.setattr(main_window, "ensure_models", lambda parent, keys: False)
    w = make_window(_content_engine())
    _start(w, photos, "content")
    assert w.current_page() is w.home
    QThreadPool.globalInstance().waitForDone()
    assert w.engine_threads == []
    assert w._task is None
    assert sorted(p.name for p in photos.iterdir()) == ["a.jpg"]  # no cache made


def test_cancel_while_looking_goes_home(qtbot, make_window, photos, make_jpeg, models_asked):
    make_jpeg(photos / "a.jpg")
    started, release = threading.Event(), threading.Event()

    class Slow(_Clip):
        def image_embedding(self, path):
            started.set()
            release.wait(10)
            return super().image_embedding(path)

    w = make_window(Slow(["a photo of food"], {}))
    _start(w, photos, "content")
    assert started.wait(5)
    assert w.progress_label.text().startswith("Looking at photos…")
    w.cancel_button.click()
    release.set()
    qtbot.waitUntil(lambda: w.current_page() is w.home)


def test_progress_counts_the_photos(qtbot, make_window, photos, make_jpeg, models_asked):
    make_jpeg(photos / "a.jpg")
    w = make_window(_content_engine())
    texts = []
    w.progress_label.setText = texts.append
    _start(w, photos, "content")
    qtbot.waitUntil(lambda: w.current_page() is w.summary_page)
    assert texts[0] == "Looking at photos…"
    assert "Looking at photos… 1 of 1" in texts


def test_engine_error_goes_home(qtbot, make_window, photos, make_jpeg, errors, models_asked):
    make_jpeg(photos / "a.jpg")
    w = make_window(None)

    def broken(use_gpu):
        raise RuntimeError("no model")

    w.clip_engine_factory = broken
    _start(w, photos, "notes")
    qtbot.waitUntil(lambda: w.current_page() is w.home)
    assert errors == ["Could not look at the photos: no model"]


def test_planning_error_goes_home(qtbot, make_window, photos, make_jpeg, errors,
                                  models_asked, monkeypatch):
    def unplugged(*args, **kwargs):
        raise OSError("drive gone")

    monkeypatch.setattr(content, "plan_content", unplugged)
    _make_content_photos(photos, make_jpeg)
    w = make_window(_content_engine())
    _start(w, photos, "content")
    qtbot.waitUntil(lambda: w.current_page() is w.category_review_page)
    w.category_review_page.continue_button.click()
    assert w.current_page() is w.home
    assert errors == ["Could not plan the moves: drive gone"]
    w.back_button.click()  # the summary's Back has nothing to go back to
    assert w.current_page() is w.home
