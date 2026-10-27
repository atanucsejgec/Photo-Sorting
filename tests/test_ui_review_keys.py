"""The review screens by keyboard: Duplicates / Repeated shots, Face questions,
Bad photos, Content / Notes review and Events.

Each flow is driven from its review page to the summary with keys only (no
.click()), and the plan checked against the keys pressed. Then Esc's leave
check (Review Focus 2), the focus through thumbnails and a reflow (Review
Focus 1), and each screen's strip.
"""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QEvent, Qt, QThreadPool, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QPushButton

from photosort.groups import Candidate, Group
from photosort.modes._clip_scan import ClipScan
from photosort.modes.content import Verdict
from photosort.modes.events import Event
from photosort.modes.face_mode import FaceScan, Question
from photosort.people import PeopleStore
from photosort.settings import Settings
from photosort.ui import dialogs, shortcuts
from photosort.ui.fullscreen_viewer import FullscreenViewer
from photosort.ui.flows import bad as bad_flow
from photosort.ui.flows import clip as clip_flow
from photosort.ui.flows import events as events_flow
from photosort.ui.flows import face as face_flow
from photosort.ui.flows import groups as groups_flow
from photosort.ui.main_window import MainWindow
from photosort.ui.photo_preview import PhotoPreview

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def warned(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(dialogs, "warn", lambda parent, title, text: shown.append(text))
    return shown


@pytest.fixture
def asked(monkeypatch):
    """dialogs.ask answers asked.answer; asked.calls records (title, text, yes, no, default)."""

    class Asked:
        answer = False
        calls: list[tuple[str, str, str, str, str]] = []

    record = Asked()
    record.calls = []

    def ask(parent, title, text, yes, no, default="no"):
        record.calls.append((title, text, yes, no, default))
        return record.answer

    monkeypatch.setattr(dialogs, "ask", ask)
    return record


@pytest.fixture
def make_window(qtbot, tmp_path, warned):
    windows = []

    def _make(size=(1280, 800)) -> MainWindow:
        w = MainWindow(Settings(), tmp_path / "config" / "settings.json",
                       people_store=PeopleStore(tmp_path / "people"))
        qtbot.addWidget(w)
        w.resize(*size)
        w.show()
        qtbot.waitExposed(w)
        w.activateWindow()
        windows.append(w)
        return w

    yield _make
    for w in windows:
        w.close()
    QThreadPool.globalInstance().waitForDone()


@pytest.fixture
def root(tmp_path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    return folder


def _jpeg(path: Path, size=(64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "gray").save(path, "JPEG")
    return path


def _focused():
    return QApplication.focusWidget()


def _key(qtbot, key, modifier=Qt.KeyboardModifier.NoModifier) -> None:
    """Press key where the focus is, as the owner would."""
    qtbot.keyClick(_focused(), key, modifier)


def _start(qtbot, w: MainWindow, root: Path, mode: str) -> None:
    """Set the window up as a sort of root by mode, the way the window does."""
    w.begin_sort()
    w.root, w.mode = root, mode


def _moves(w: MainWindow) -> set[tuple[str, str]]:
    assert w.current_page() is w.summary_page
    return {(m.src, m.dst) for m in w._plan.moves} if w._plan is not None else set()


def _activate(qtbot, window) -> None:
    """Make window the active one, as a window manager does when a dialog
    opens or closes (offscreen, nothing does)."""
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window)


def _close_preview(qtbot, preview) -> None:
    """Esc in the preview: it closes and lets itself go."""
    _activate(qtbot, preview)
    with qtbot.waitSignal(preview.destroyed):
        qtbot.keyClick(preview, Qt.Key.Key_Escape)


def _on_card(qtbot, page, n: int = 0):
    card = page.navigator.cards[n]
    qtbot.waitUntil(lambda: _focused() is card)
    return card


# ----- Duplicates and Repeated shots -------------------------------------------

def _candidate(root: Path, rel: str, sharpness: float) -> Candidate:
    path = _jpeg(root / rel)
    return Candidate(rel, "photo", 64, 48, path.stat().st_size, None, sharpness, "")


def _group(root: Path, prefix: str, kind="exact") -> Group:
    a = _candidate(root, f"{prefix}a.jpg", 100.0)
    b = _candidate(root, f"{prefix}b.jpg", 50.0)
    c = _candidate(root, f"{prefix}c.jpg", 90.0)
    return Group(kind, [a, b, c], {a.rel})


def test_duplicates_by_keyboard(qtbot, make_window, root, asked):
    w = make_window()
    _start(qtbot, w, root, "duplicates")
    groups_flow._on_found(w, [_group(root, "1"), _group(root, "2"), _group(root, "3")])
    page = w.review_page
    assert w.current_page() is page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Right)
    assert _focused() is page.cards[1]
    _key(qtbot, Qt.Key.Key_Space)  # keep 1b too
    _key(qtbot, Qt.Key.Key_Right, CTRL)  # confirm: on to group 2
    assert page.heading_label.text().startswith("Group 2 of 3")
    assert _focused() is page.cards[0]
    _key(qtbot, Qt.Key.Key_Escape)  # back a group, the answer kept
    assert page.heading_label.text().startswith("Group 1 of 3")
    assert [c.keep_check.isChecked() for c in page.cards] == [True, True, False]
    _key(qtbot, Qt.Key.Key_Right, CTRL)
    _key(qtbot, Qt.Key.Key_K)  # group 2: keep all
    assert page.heading_label.text().startswith("Group 3 of 3")
    _key(qtbot, Qt.Key.Key_1)  # not for duplicates: nothing
    assert page.heading_label.text().startswith("Group 3 of 3")
    _key(qtbot, Qt.Key.Key_N)  # group 3: skip, the last: the summary
    assert _moves(w) == {("1c.jpg", "Duplicates/1c.jpg")}
    assert asked.calls == []


def test_repeated_keep_one_and_stop_by_keyboard(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "repeated")
    groups_flow._on_found(w, [_group(root, "1", "repeated"), _group(root, "2", "repeated"),
                              _group(root, "3", "repeated")])
    page = w.review_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Space)  # untick 1a ...
    _key(qtbot, Qt.Key.Key_1)  # ... but 1 keeps the sharpest, 1a, only
    assert page.heading_label.text().startswith("Group 2 of 3")
    _key(qtbot, Qt.Key.Key_Return, CTRL)  # stop reviewing: the summary, group 2 unanswered
    assert _moves(w) == {("1b.jpg", "Repeated Shots/1b.jpg"),
                         ("1c.jpg", "Repeated Shots/1c.jpg")}


def test_a_group_with_nothing_kept_cannot_be_confirmed(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "duplicates")
    groups_flow._on_found(w, [_group(root, "1"), _group(root, "2")])
    page = w.review_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Space)  # nothing kept
    assert "Ctrl+→" not in w.strip.text()
    _key(qtbot, Qt.Key.Key_Right, CTRL)
    assert page.heading_label.text().startswith("Group 1 of 2")
    _key(qtbot, Qt.Key.Key_Space)
    assert "Ctrl+→ confirm" in w.strip.text()


def test_enter_shows_the_file_big(qtbot, make_window, root, monkeypatch):
    w = make_window()
    _start(qtbot, w, root, "duplicates")
    groups_flow._on_found(w, [_group(root, "1")])
    page = w.review_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Return)  # a duplicate: the preview
    preview = page.preview
    assert isinstance(preview, PhotoPreview) and preview.isVisible()
    assert preview.windowTitle() == "1b.jpg"
    _close_preview(qtbot, preview)
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is page.cards[1])

    started = []

    def fake_exec(viewer):
        started.append(viewer.current_index)
        return 0

    monkeypatch.setattr(FullscreenViewer, "exec", fake_exec)
    w.mode = "repeated"
    groups_flow._on_found(w, [_group(root, "2", "repeated")])
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Return)  # repeated shots: full screen, from the focused one
    assert started == [2]


# ----- Face questions -------------------------------------------------------------

def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _face_sort(w: MainWindow, root: Path):
    _jpeg(root / "a.jpg", (200, 200))
    _jpeg(root / "b.jpg", (300, 200))
    atanu = w.people_store.add("Atanu", _unit(1), Image.new("RGB", (40, 40), "blue"))
    zoe = w.people_store.add("Zoe", _unit(2), Image.new("RGB", (40, 40), "green"))
    questions = [
        Question("a.jpg", atanu.id, "Atanu", (20.0, 40.0, 80.0, 120.0), (200, 200), 0.40, 63),
        Question("b.jpg", atanu.id, "Atanu", (10.0, 10.0, 50.0, 50.0), (300, 200), 0.35, 50),
        Question("b.jpg", zoe.id, "Zoe", (150.0, 10.0, 200.0, 60.0), (300, 200), 0.32, 38),
    ]
    return [atanu, zoe], FaceScan({}, questions, photos=2, unreadable=0)


def test_face_questions_by_keyboard(qtbot, make_window, root, asked):
    w = make_window()
    _start(qtbot, w, root, "face")
    people, scan = _face_sort(w, root)
    face_flow._on_scan(w, people, scan)
    page = w.face_question_page
    assert w.current_page() is page
    qtbot.waitUntil(lambda: _focused() is page.photo_view)
    _key(qtbot, Qt.Key.Key_N)  # a.jpg: not Atanu ...
    assert page.counter_label.text() == "2 of 3"
    _key(qtbot, Qt.Key.Key_Escape)  # ... back: changed to yes
    assert page.counter_label.text() == "1 of 3"
    assert page.answer_label.text() == "Your answer: No"
    _key(qtbot, Qt.Key.Key_Y)
    _key(qtbot, Qt.Key.Key_Space)  # b.jpg skipped: Zoe in it is not asked
    assert _moves(w) == {("a.jpg", "Atanu/a.jpg")}
    assert asked.calls == []


def test_face_stop_asking_by_keyboard(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "face")
    people, scan = _face_sort(w, root)
    face_flow._on_scan(w, people, scan)
    qtbot.waitUntil(lambda: _focused() is w.face_question_page.photo_view)
    _key(qtbot, Qt.Key.Key_Y)
    _key(qtbot, Qt.Key.Key_Return, CTRL)  # stop asking: b.jpg's questions unanswered
    assert _moves(w) == {("a.jpg", "Atanu/a.jpg")}


# ----- Bad photos ---------------------------------------------------------------

def _bad(root: Path, count: int) -> list[Candidate]:
    out = []
    for n in range(count):
        path = _jpeg(root / f"bad{n}.jpg")
        out.append(Candidate(path.name, "photo", 64, 48, path.stat().st_size, None, 0.0,
                             "blurry"))
    return out


def test_bad_photos_by_keyboard(qtbot, make_window, root, asked):
    w = make_window()
    _start(qtbot, w, root, "bad")
    bad_flow._on_found(w, _bad(root, 6))
    page = w.bad_page
    assert w.current_page() is page
    _on_card(qtbot, page, 0)
    columns = page.columns()
    assert columns >= 2
    _key(qtbot, Qt.Key.Key_Space)  # untick bad0
    _key(qtbot, Qt.Key.Key_Down)
    assert _focused() is page.cards[columns]
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Space)  # untick the one right of below
    _key(qtbot, Qt.Key.Key_U)  # all unticked ...
    assert page.ticked_label.text() == "0 will be set aside"
    _key(qtbot, Qt.Key.Key_A)  # ... and ticked again
    _key(qtbot, Qt.Key.Key_Space)  # untick it again
    _key(qtbot, Qt.Key.Key_Up)
    _key(qtbot, Qt.Key.Key_Left)
    _key(qtbot, Qt.Key.Key_Space)  # untick bad0 again
    assert page.ticked_label.text() == "4 will be set aside"
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    kept = {"bad0.jpg", f"bad{columns + 1}.jpg"}
    assert _moves(w) == {(f"bad{n}.jpg", f"Bad Photos/bad{n}.jpg") for n in range(6)
                         if f"bad{n}.jpg" not in kept}
    assert asked.calls == []


def test_bad_photos_enter_views_big(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "bad")
    bad_flow._on_found(w, _bad(root, 3))
    page = w.bad_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Return)
    preview = page.preview
    assert isinstance(preview, PhotoPreview) and preview.windowTitle() == "bad1.jpg"
    _close_preview(qtbot, preview)
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is page.cards[1])
    assert page.cards[1].move_check.isChecked()  # Enter ticks nothing


def test_back_from_the_summary_keeps_the_place(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "bad")
    bad_flow._on_found(w, _bad(root, 4))
    page = w.bad_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    assert w.current_page() is w.summary_page
    w.back_button.click()  # the summary's own keys are Task 5's
    _on_card(qtbot, page, 2)


# ----- Content and Notes & Pages -------------------------------------------------

def _clip_sort(root: Path) -> clip_flow._ClipSort:
    for rel in ("a.jpg", "b.jpg", "c.jpg"):
        _jpeg(root / rel)
    choices = ["Food", "Pets", "Vehicles"]
    unsure = [Verdict("a.jpg", "Food", 0.6, (("Food", 0.6), ("Pets", 0.2))),
              Verdict("b.jpg", "Pets", 0.5, (("Pets", 0.5), ("Food", 0.3))),
              Verdict("c.jpg", "Vehicles", 0.5, (("Vehicles", 0.5), ("Food", 0.3)))]
    scan = ClipScan({rel: np.zeros(512, np.float32) for rel in ("a.jpg", "b.jpg", "c.jpg")},
                    photos=3, unreadable=0)
    return clip_flow._ClipSort(choices, scan, {}, unsure)


def _picker_view(page, n: int):
    return page.cards[n].choice_combo.view()


def test_content_review_by_keyboard(qtbot, make_window, root, asked):
    w = make_window()
    _start(qtbot, w, root, "content")
    w.flow_state = _clip_sort(root)
    clip_flow._ask(w, {})
    page = w.category_review_page
    assert w.current_page() is page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Return)  # the picker opens, on Food
    view = _picker_view(page, 0)
    qtbot.waitUntil(lambda: view.isVisible() and _focused() is view)
    assert w.strip.text() == "↑↓ choose · Enter set · Esc close"
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Return)  # Pets
    qtbot.waitUntil(lambda: not view.isVisible())
    assert page.cards[0].answer() == "Pets"
    qtbot.waitUntil(lambda: _focused() is page.cards[0])
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Return)  # b.jpg's picker ...
    view = _picker_view(page, 1)
    qtbot.waitUntil(lambda: view.isVisible() and _focused() is view)
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Escape)  # ... closed: nothing set, and no leave check
    qtbot.waitUntil(lambda: not view.isVisible())
    assert page.cards[1].answer() == "Pets"
    assert asked.calls == []
    qtbot.waitUntil(lambda: _focused() is page.cards[1])
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_L)  # c.jpg left where it is
    assert page.cards[2].answer() is None
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    assert _moves(w) == {("a.jpg", "Pets/a.jpg"), ("b.jpg", "Pets/b.jpg")}


def test_content_leave_all_by_keyboard(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "content")
    w.flow_state = _clip_sort(root)
    clip_flow._ask(w, {})
    page = w.category_review_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_L, SHIFT)
    assert [card.answer() for card in page.cards] == [None, None, None]
    _key(qtbot, Qt.Key.Key_Space)  # view big
    assert isinstance(page.preview, PhotoPreview)
    assert page.preview.windowTitle() == "a.jpg"
    _close_preview(qtbot, page.preview)
    _activate(qtbot, w)
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    assert _moves(w) == set()


def test_the_picker_shows_every_choice(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "content")
    sort = _clip_sort(root)
    sort.choices = [f"Category {n}" for n in range(12)]
    w.flow_state = sort
    clip_flow._ask(w, {})
    page = w.category_review_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Return)
    combo = page.cards[0].choice_combo
    view = combo.view()
    qtbot.waitUntil(view.isVisible)
    last = view.visualRect(combo.model().index(combo.count() - 1, 0))
    assert 0 <= last.top() and last.bottom() < view.viewport().height()  # in full
    assert not view.verticalScrollBar().isVisible()
    _key(qtbot, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not view.isVisible())


# ----- Events -----------------------------------------------------------------------

GOA = datetime(2025, 1, 10, 9, 0)
PARTY = datetime(2025, 2, 20, 18, 0)
LATE = datetime(2025, 3, 1, 12, 0)


def _event(root: Path, prefix: str, *times: datetime) -> Event:
    files = tuple(f"{prefix}{n:02d}.jpg" for n in range(len(times)))
    for rel in files:
        _jpeg(root / rel)
    return Event(files, times)


def _events(root: Path) -> list[Event]:
    return [_event(root, "goa", GOA, GOA + timedelta(hours=2)),
            _event(root, "party", PARTY, PARTY + timedelta(hours=1)),
            _event(root, "late", LATE, LATE + timedelta(hours=1))]


def test_events_by_keyboard(qtbot, make_window, root, asked):
    w = make_window()
    _start(qtbot, w, root, "events")
    events_flow._on_found(w, (_events(root), 0))
    page = w.events_page
    assert w.current_page() is page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Down)
    assert _focused() is page.rows[1]
    _key(qtbot, Qt.Key.Key_F2)  # rename the party
    assert _focused() is page.rows[1].name_edit
    assert w.strip.text() == ("Enter done · Esc stop editing · Ctrl+Enter continue · "
                              "Tab/Shift+Tab area")
    qtbot.keyClicks(_focused(), "Party ms")  # M and S type, here
    _key(qtbot, Qt.Key.Key_Escape)  # stop editing: the name stays, and no leave check
    assert _focused() is page.rows[1]
    assert asked.calls == []
    _key(qtbot, Qt.Key.Key_Return)  # Enter renames too
    assert _focused() is page.rows[1].name_edit
    qtbot.keyClicks(_focused(), "Party")  # the old name was selected: replaced
    _key(qtbot, Qt.Key.Key_Return)  # done
    assert _focused() is page.rows[1]
    _key(qtbot, Qt.Key.Key_M)  # merge the party with the late one
    assert len(page.rows) == 2
    assert _focused() is page.rows[1]
    assert page.rows[1].name_edit.text() == "Party"
    _key(qtbot, Qt.Key.Key_S)  # split it again ...
    assert len(page.rows) == 3
    assert _focused() is page.rows[1]
    _key(qtbot, Qt.Key.Key_M)  # ... and merge again
    _key(qtbot, Qt.Key.Key_Up)
    _key(qtbot, Qt.Key.Key_Space)  # skip Goa
    assert page.rows[0].skip_check.isChecked()
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    assert _moves(w) == {(rel, f"Party/{rel}") for rel in
                         ("party00.jpg", "party01.jpg", "late00.jpg", "late01.jpg")}
    assert asked.calls == []


def test_events_m_and_s_only_where_they_work(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "events")
    one_moment = Event(("x.jpg", "y.jpg"), (GOA, GOA))
    for rel in one_moment.files:
        _jpeg(root / rel)
    events_flow._on_found(w, ([_event(root, "goa", GOA, GOA + timedelta(hours=2)),
                               one_moment], 0))
    page = w.events_page
    _on_card(qtbot, page, 0)
    assert w.strip.text() == ("↑↓ move · Space skip · F2/Enter rename · M merge · S split · "
                              "Ctrl+Enter continue · Esc back · Tab/Shift+Tab area")
    _key(qtbot, Qt.Key.Key_Down)  # the last row, all at one moment: no merge, no split
    assert w.strip.text() == ("↑↓ move · Space skip · F2/Enter rename · Ctrl+Enter continue · "
                              "Esc back · Tab/Shift+Tab area")
    _key(qtbot, Qt.Key.Key_M)
    _key(qtbot, Qt.Key.Key_S)
    assert len(page.rows) == 2


# ----- Esc: leave this review? (Review Focus 2) -----------------------------------------

def _review_on_first_item(qtbot, w, root, screen: str):
    """Open screen's review on its first item; return the page."""
    if screen == "duplicates":
        _start(qtbot, w, root, "duplicates")
        groups_flow._on_found(w, [_group(root, "1"), _group(root, "2")])
        page = w.review_page
    elif screen == "face":
        _start(qtbot, w, root, "face")
        people, scan = _face_sort(w, root)
        face_flow._on_scan(w, people, scan)
        page = w.face_question_page
    elif screen == "bad":
        _start(qtbot, w, root, "bad")
        bad_flow._on_found(w, _bad(root, 3))
        page = w.bad_page
    elif screen == "content":
        _start(qtbot, w, root, "content")
        w.flow_state = _clip_sort(root)
        clip_flow._ask(w, {})
        page = w.category_review_page
    else:
        _start(qtbot, w, root, "events")
        events_flow._on_found(w, (_events(root), 0))
        page = w.events_page
    assert w.current_page() is page
    qtbot.waitUntil(lambda: _focused() is not None and page.isAncestorOf(_focused())
                    or _focused() is page)
    return page


SCREENS = ["duplicates", "face", "bad", "content", "events"]


def _in_next_dialog(qtbot, seen: list, *keys):
    """Once the next modal dialog shows, note its focused button and strip,
    then press keys in it."""

    def press():
        dialog = QApplication.activeModalWidget()
        if dialog is None or dialog.focusWidget() is None:
            QTimer.singleShot(10, press)
            return
        if QApplication.focusWidget() is not dialog.focusWidget():
            dialog.activateWindow()  # as a window manager would (see _activate)
            QTimer.singleShot(10, press)
            return
        seen.append((dialog.windowTitle(), dialog.focusWidget().text()))
        for key in keys:
            qtbot.keyClick(dialog.focusWidget(), key)

    QTimer.singleShot(0, press)


@pytest.mark.real_dialogs
@pytest.mark.parametrize("screen", SCREENS)
def test_esc_on_the_first_item_asks_once_with_stay_focused(qtbot, make_window, root,
                                                          monkeypatch, screen):
    real_ask = dialogs.ask
    asks = []
    monkeypatch.setattr(dialogs, "ask", lambda *a, **k: asks.append(a[1:3]) or real_ask(*a, **k))
    w = make_window()
    page = _review_on_first_item(qtbot, w, root, screen)
    focus = _focused()

    seen: list = []
    _in_next_dialog(qtbot, seen, Qt.Key.Key_Escape)  # a second Esc: closes it
    _key(qtbot, Qt.Key.Key_Escape)
    assert seen == [("Leave this review?", "Stay")]
    assert asks == [("Leave this review?", "Your answers so far will be lost.")]  # once
    assert w.current_page() is page
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is focus)  # back where it was

    _in_next_dialog(qtbot, seen, Qt.Key.Key_Return)  # Enter: Stay
    _key(qtbot, Qt.Key.Key_Escape)
    assert len(asks) == 2
    assert w.current_page() is page
    assert w.sidebar.is_locked()
    _activate(qtbot, w)
    qtbot.waitUntil(lambda: _focused() is focus)

    _in_next_dialog(qtbot, seen, Qt.Key.Key_Right, Qt.Key.Key_Return)  # Leave
    _key(qtbot, Qt.Key.Key_Escape)
    assert len(asks) == 3
    assert w.current_page() is w.home
    assert w._plan is None and w.flow_state is None and w.back_to is None
    assert not w.sidebar.is_locked()


def _esc(w, held: bool) -> None:
    """An Esc press through the shortcut map; held: an auto-repeat of one."""
    QApplication.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                        Qt.KeyboardModifier.NoModifier, "", held))


@pytest.mark.parametrize("screen", SCREENS)
def test_a_held_esc_asks_once(qtbot, make_window, root, screen, asked):
    """The leave check's Esc does not repeat while held: one dialog, not a
    stream of them."""
    w = make_window()
    page = _review_on_first_item(qtbot, w, root, screen)
    for _ in range(3):
        _esc(w, held=True)  # the repeats of a held Esc
    assert asked.calls == []
    assert w.current_page() is page
    _esc(w, held=False)  # a fresh press asks
    assert [call[0] for call in asked.calls] == ["Leave this review?"]


def test_esc_on_a_later_group_steps_back_without_asking(qtbot, make_window, root, asked):
    w = make_window()
    _start(qtbot, w, root, "duplicates")
    groups_flow._on_found(w, [_group(root, "1"), _group(root, "2")])
    _on_card(qtbot, w.review_page, 0)
    _key(qtbot, Qt.Key.Key_N)
    _key(qtbot, Qt.Key.Key_Escape)
    assert w.review_page.heading_label.text().startswith("Group 1 of 2")
    assert asked.calls == []
    _key(qtbot, Qt.Key.Key_Escape)  # the first: now it asks; Stay
    assert [call[0] for call in asked.calls] == ["Leave this review?"]
    assert asked.calls[0][2:] == ("Leave", "Stay", "no")
    assert w.current_page() is w.review_page


# ----- focus through thumbnails and a reflow (Review Focus 1) ------------------------------

def _grid_screen(qtbot, w, root, screen: str):
    if screen == "bad":
        _start(qtbot, w, root, "bad")
        bad_flow._on_found(w, _bad(root, 9))
        return w.bad_page
    _start(qtbot, w, root, "content")
    sort = _clip_sort(root)
    for n in range(6):
        rel = f"more{n}.jpg"
        _jpeg(root / rel)
        sort.unsure.append(Verdict(rel, "Food", 0.4, (("Food", 0.4),)))
        sort.scan.embeddings[rel] = np.zeros(512, np.float32)
    w.flow_state = sort
    clip_flow._ask(w, {})
    return w.category_review_page


@pytest.mark.parametrize("screen", ["bad", "content"])
def test_focus_survives_thumbnails_and_a_reflow(qtbot, make_window, root, screen):
    w = make_window(size=(1600, 900))
    page = _grid_screen(qtbot, w, root, screen)
    _on_card(qtbot, page, 0)
    qtbot.waitUntil(lambda: page.columns() >= 4)
    wide = page.columns()
    page.navigator.focus(5)
    card = page.cards[5]
    qtbot.waitUntil(lambda: all(not c.thumb_label.pixmap().isNull() for c in page.cards))
    assert _focused() is card
    w.resize(1000, 800)
    qtbot.waitUntil(lambda: page.columns() < wide)
    narrow = page.columns()
    assert _focused() is card  # the same card
    _key(qtbot, Qt.Key.Key_Down)
    assert _focused() is page.cards[5 + narrow]  # by the new column count
    _key(qtbot, Qt.Key.Key_Up)
    assert _focused() is card


def test_the_bad_grid_fits_a_small_window(qtbot, make_window, root):
    w = make_window(size=(1024, 600))
    _start(qtbot, w, root, "bad")
    bad_flow._on_found(w, _bad(root, 7))
    page = w.bad_page
    card = _on_card(qtbot, page, 0)
    assert page.columns() >= 2
    strip_top = w.strip.mapTo(w, w.strip.rect().topLeft()).y()
    assert page.continue_button.mapTo(w, page.continue_button.rect().bottomLeft()).y() < strip_top
    assert w.strip.mapTo(w, w.strip.rect().bottomLeft()).y() <= w.height()
    _key(qtbot, Qt.Key.Key_Down)
    _key(qtbot, Qt.Key.Key_Down)  # below the fold: scrolled into view
    focused = _focused()
    assert focused is not card
    top_left = focused.mapTo(page.scroll.viewport(), focused.rect().topLeft())
    bottom = focused.mapTo(page.scroll.viewport(), focused.rect().bottomLeft())
    assert top_left.y() >= 0 and bottom.y() <= page.scroll.viewport().height()


# ----- the strips ---------------------------------------------------------------------

def _live_records(widget) -> list[str]:
    return [f"{label} {word}" for label, word in shortcuts._live(widget)]


def _strip_is_its_records(w, page, holder) -> None:
    """The strip lists exactly holder's live keys, then the page's."""
    expected = []
    for entry in _live_records(holder) + _live_records(page):
        if entry.split(" ")[0] not in [e.split(" ")[0] for e in expected]:
            expected.append(entry)
    assert w.strip.text() == " · ".join(expected)


TAB = "Tab/Shift+Tab area"  # every review screen: the cards, then the buttons
STRIPS = {
    "duplicates": "←→ move · Space keep · Enter view big · K keep all · N skip · "
                  f"Ctrl+→ confirm · Esc back · Ctrl+Enter stop · {TAB}",
    "face": f"Y yes · N no · Space skip · Esc back · Ctrl+Enter stop · {TAB}",
    "bad": "←↑↓→ move · Space tick · Enter view big · A tick all · U untick all · "
           f"Ctrl+Enter continue · Esc back · {TAB}",
    "content": "←↑↓→ move · Enter pick · L leave this one · Space view big · "
               f"Shift+L leave all · Ctrl+Enter continue · Esc back · {TAB}",
    "events": "↑↓ move · Space skip · F2/Enter rename · M merge · S split · "
              f"Ctrl+Enter continue · Esc back · {TAB}",
}


@pytest.mark.parametrize("screen", SCREENS)
def test_each_screens_strip_is_its_keys(qtbot, make_window, root, screen):
    w = make_window()
    page = _review_on_first_item(qtbot, w, root, screen)
    assert w.strip.text() == STRIPS[screen]
    holder = page.photo_view if screen == "face" else page.scroll
    _strip_is_its_records(w, page, holder)
    buttons = [b for b in page.findChildren(QPushButton)
               if b.focusPolicy() & Qt.FocusPolicy.TabFocus and b.isVisible()
               and b.isEnabled()]
    if buttons:  # on a button, the page's own keys only
        buttons[0].setFocus()
        qtbot.waitUntil(lambda: _focused() is buttons[0])
        _strip_is_its_records(w, page, page)


def test_the_repeated_strip_has_keep_sharpest(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "repeated")
    groups_flow._on_found(w, [_group(root, "1", "repeated")])
    _on_card(qtbot, w.review_page, 0)
    assert w.strip.text() == ("←→ move · Space keep · Enter view big · K keep all · "
                              "1 keep sharpest · N skip · Ctrl+→ confirm · Esc back · "
                              "Ctrl+Enter stop · Tab/Shift+Tab area")


def test_content_back_from_the_summary_keeps_the_place(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "content")
    w.flow_state = _clip_sort(root)
    clip_flow._ask(w, {})
    page = w.category_review_page
    _on_card(qtbot, page, 0)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_Right)
    _key(qtbot, Qt.Key.Key_L)
    _key(qtbot, Qt.Key.Key_Return, CTRL)
    assert w.current_page() is w.summary_page
    w.back_button.click()  # the summary's own keys are Task 5's
    assert w.current_page() is page
    _on_card(qtbot, page, 2)  # the card it was on
    assert page.cards[2].answer() is None  # its answer kept


# ----- the smallest window (1024 x 600 at 125 %) -------------------------------------

def _load_every_review(qtbot, w, root) -> None:
    for screen in SCREENS:
        _review_on_first_item(qtbot, w, root / screen, screen)


@pytest.fixture
def every_screen_root(root):
    for screen in SCREENS:
        (root / screen).mkdir()
    return root


def test_every_page_fits_the_smallest_window(qtbot, make_window, every_screen_root):
    w = make_window(size=(819, 480))
    qtbot.waitUntil(lambda: (w.width(), w.height()) == (819, 480))
    _load_every_review(qtbot, w, every_screen_root)
    _start(qtbot, w, every_screen_root, "repeated")  # its button row is the longest
    groups_flow._on_found(w, [_group(every_screen_root, "r", "repeated")])
    for n in range(w.stack.count()):
        page = w.stack.widget(n)
        w.stack.setCurrentWidget(page)
        qtbot.wait(10)
        room, layout = w.stack.size(), page.layout()
        width = page.minimumSizeHint().width()
        height = (layout.totalMinimumHeightForWidth(room.width()) if layout.hasHeightForWidth()
                  else page.minimumSizeHint().height())  # wrapped labels: at this width
        name = type(page).__name__ if type(page).__name__ != "QWidget" else n
        assert width <= room.width(), (name, width, room)
        assert height <= room.height(), (name, height, room)
        for button in page.findChildren(QPushButton):
            if button.isVisible():  # none crushed below its text
                assert button.width() >= button.minimumSizeHint().width(), (name, button.text())


def test_event_rows_fit_a_narrow_window(qtbot, make_window, root):
    w = make_window(size=(819, 480))
    _start(qtbot, w, root, "events")
    events_flow._on_found(w, (_events(root), 0))
    page = w.events_page
    _on_card(qtbot, page, 0)
    qtbot.wait(20)
    assert not page.scroll.horizontalScrollBar().isVisible()  # no sideways scrolling
    row = page.rows[0]
    for widget in (row.split_button, row.skip_check, row.name_edit):
        right = widget.mapTo(page.scroll.viewport(), widget.rect().topRight()).x()
        assert right <= page.scroll.viewport().width()
    assert sum(not label.isHidden() for label in row.thumb_labels) >= 1


# The parts of a card the owner reads and answers with: in view on a focused
# card, whatever the window's height.
_ANSWER_PARTS = {
    "duplicates": ("details_label", "keep_check"),
    "bad": ("tag_label", "name_label", "folder_label", "move_check"),
    "content": ("name_label", "guess_label", "choice_combo"),
}


def _inside(widget, view) -> bool:
    top = widget.mapTo(view, widget.rect().topLeft()).y()
    bottom = widget.mapTo(view, widget.rect().bottomLeft()).y()
    return top >= 0 and bottom <= view.height()


@pytest.mark.parametrize("screen", ["duplicates", "bad", "content"])
def test_the_focused_cards_answers_are_in_view_in_a_short_window(qtbot, make_window, root,
                                                                 screen):
    w = make_window(size=(819, 480))
    page = _review_on_first_item(qtbot, w, root, screen)
    view = page.scroll.viewport()
    _on_card(qtbot, page, 0)
    qtbot.wait(50)  # the cards' sizes settle, as long before the owner's first key
    for key, n in ((Qt.Key.Key_Right, 1), (Qt.Key.Key_Left, 0)):
        _key(qtbot, key)
        card = page.navigator.cards[n]
        assert _focused() is card
        for name in _ANSWER_PARTS[screen]:
            assert _inside(getattr(card, name), view), (screen, n, name)
        if screen != "duplicates":  # a grid card fits the view whole
            assert _inside(card, view), (screen, n, card.height(), view.height())
            assert card.thumb_label.width() >= 96  # still a thumbnail


def test_grid_columns_fill_the_width(qtbot, make_window, root):
    w = make_window()  # 1280 x 800
    _start(qtbot, w, root, "bad")
    bad_flow._on_found(w, _bad(root, 8))
    page = w.bad_page
    _on_card(qtbot, page, 0)
    assert page.columns() >= 4
    columns = page.columns()
    last = page.cards[columns - 1]
    right = last.mapTo(page.scroll.viewport(), last.rect().topRight()).x()
    assert right >= page.scroll.viewport().width() - 16  # no empty strip on the right
    widths = {card.width() for card in page.cards[:columns]}
    assert max(widths) - min(widths) <= 1  # evenly
    _key(qtbot, Qt.Key.Key_Down)
    assert _focused() is page.cards[columns]  # the arrows follow the columns shown


def test_cards_are_cards(qtbot, make_window, root):
    w = make_window()
    _start(qtbot, w, root, "bad")
    bad_flow._on_found(w, _bad(root, 2))
    card = _on_card(qtbot, w.bad_page, 0)
    assert card.objectName() == "card"
    assert card.graphicsEffect() is not None  # the focus glow
