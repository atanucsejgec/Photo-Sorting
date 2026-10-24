from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QImage

from photosort.groups import Candidate, Group
from photosort.ui.review_page import CandidateCard, ReviewPage


@pytest.fixture
def make_page(qtbot):
    pages = []

    def _make() -> ReviewPage:
        page = ReviewPage()
        qtbot.addWidget(page)
        pages.append(page)
        return page

    yield _make
    QThreadPool.globalInstance().waitForDone()


def _photo(root: Path, make_pattern_jpeg, rel: str, seed: int, sharpness: float,
           taken: datetime | None = None, size=(800, 600), note: str = "") -> Candidate:
    path = make_pattern_jpeg(root / rel, seed, size=size)
    return Candidate(rel, "photo", size[0], size[1], path.stat().st_size, taken,
                     sharpness, note)


def _group(root, make_pattern_jpeg, prefix: str, seed: int, kind="exact") -> Group:
    a = _photo(root, make_pattern_jpeg, f"{prefix}a.jpg", seed, 100.0)
    b = _photo(root, make_pattern_jpeg, f"{prefix}b.jpg", seed, 50.0)
    c = _photo(root, make_pattern_jpeg, f"{prefix}c.jpg", seed, 90.0)
    return Group(kind, [a, b, c], {a.rel})


def _ticked(page: ReviewPage) -> set[str]:
    return {card.candidate.rel for card in page.cards if card.keep_check.isChecked()}


def _finished(qtbot, page):
    results = []
    page.finished.connect(results.append)
    return results


def test_card_details(qtbot, tmp_path, make_pattern_jpeg):
    path = make_pattern_jpeg(tmp_path / "sub/IMG1.jpg", 1, exif_dt="2025:02:20 18:13:25")
    c = Candidate("sub/IMG1.jpg", "photo", 800, 600, 4_123_456,
                  datetime(2025, 2, 20, 18, 13, 25), 120.0, "Exact copy")
    other = Candidate("x.jpg", "photo", 800, 600, 10, None, 60.0)
    group = Group("exact", [c, other], {c.rel})
    card = CandidateCard(tmp_path, c, group)
    qtbot.addWidget(card)
    assert path.is_file()
    assert card.details_label.text().splitlines() == [
        "IMG1.jpg",
        "800 × 600",
        "4.1 MB",
        "20 Feb 2025, 18:13:25",
        "Sharpness: best",
        "Exact copy",
        "sub",
    ]
    assert card.keep_check.text() == "Keep"

    plain = CandidateCard(tmp_path, other, group)
    qtbot.addWidget(plain)
    lines = plain.details_label.text().splitlines()
    assert "no date inside" in lines
    assert "Sharpness: blurry" in lines
    assert lines[-1] == "(top folder)"


def test_date_line_has_no_leading_zero(qtbot, tmp_path):
    c = Candidate("a.jpg", "photo", 10, 10, 1, datetime(2025, 3, 5, 7, 8, 9), None)
    card = CandidateCard(tmp_path, c, Group("exact", [c], {c.rel}))
    qtbot.addWidget(card)
    assert "5 Mar 2025, 07:08:09" in card.details_label.text().splitlines()


def test_video_card_placeholder(qtbot, tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"not really a video")
    c = Candidate("clip.mp4", "video", None, None, 18, None, None, "Exact copy")
    card = CandidateCard(tmp_path, c, Group("exact", [c], {c.rel}))
    qtbot.addWidget(card)
    assert card.thumb_label.text() == "Video"
    lines = card.details_label.text().splitlines()
    assert lines[0] == "clip.mp4"
    assert not any("×" in line or line.startswith("Sharpness") for line in lines)


def test_thumbnails_load_off_the_gui_thread(qtbot, tmp_path, make_page, make_pattern_jpeg):
    (tmp_path / "broken.jpg").write_bytes(b"not a jpeg")
    good = _photo(tmp_path, make_pattern_jpeg, "good.jpg", 1, 10.0)
    broken = Candidate("broken.jpg", "photo", None, None, 10, None, None)
    page = make_page()
    page.load(tmp_path, "duplicates", [Group("exact", [good, broken], {good.rel})])
    good_card, broken_card = page.cards
    assert good_card.thumb_label.text() == "Loading…"
    qtbot.waitUntil(lambda: not good_card.thumb_label.pixmap().isNull())
    qtbot.waitUntil(lambda: broken_card.thumb_label.text() == "Can't show")
    assert good_card.thumb_label.text() == ""


def test_late_thumbnail_for_an_old_group_is_ignored(qtbot, tmp_path, make_page,
                                                    make_pattern_jpeg):
    groups = [_group(tmp_path, make_pattern_jpeg, "1", 1),
              _group(tmp_path, make_pattern_jpeg, "2", 2)]
    page = make_page()
    page.load(tmp_path, "duplicates", groups)
    page.skip_button.click()  # before group 1's thumbnails arrive
    QThreadPool.globalInstance().waitForDone()
    qtbot.waitUntil(lambda: all(not c.thumb_label.pixmap().isNull() for c in page.cards))
    page._thumbnail_ready((-1, 0, QImage(4, 4, QImage.Format.Format_RGB888)))  # stale
    assert [c.candidate.rel for c in page.cards] == ["2a.jpg", "2b.jpg", "2c.jpg"]


def test_heading_subtitle_and_pre_ticks(tmp_path, make_page, make_pattern_jpeg):
    groups = [_group(tmp_path, make_pattern_jpeg, "1", 1, "resized")]
    page = make_page()
    page.load(tmp_path, "duplicates", groups)
    assert page.heading_label.text() == "Group 1 of 1 · Same photo, resized or re-saved"
    assert page.subtitle_label.text() == (
        "Tick the ones to keep. The others go to Duplicates/ (nothing is deleted)."
    )
    assert _ticked(page) == {"1a.jpg"}
    assert page.keep_all_button.text() == "Keep all (not duplicates)"
    assert page.keep_one_button.isHidden()
    assert page.fullscreen_button.isHidden()
    assert not page.back_button.isEnabled()

    page.load(tmp_path, "repeated", [_group(tmp_path, make_pattern_jpeg, "r", 3, "repeated")])
    assert page.heading_label.text() == "Group 1 of 1 · Repeated shots"
    assert page.subtitle_label.text() == (
        "Tick the ones to keep. The others go to Repeated Shots/ (nothing is deleted)."
    )
    assert page.keep_all_button.text() == "Keep all"
    assert not page.keep_one_button.isHidden()
    assert not page.fullscreen_button.isHidden()


def test_subtitle_names_the_folder_as_spelt_on_disk(tmp_path, make_page, make_pattern_jpeg):
    (tmp_path / "duplicates").mkdir()
    page = make_page()
    page.load(tmp_path, "duplicates", [_group(tmp_path, make_pattern_jpeg, "1", 1)])
    assert page.subtitle_label.text() == (
        "Tick the ones to keep. The others go to duplicates/ (nothing is deleted)."
    )


def test_confirm_disabled_when_nothing_ticked(qtbot, tmp_path, make_page, make_pattern_jpeg):
    page = make_page()
    page.load(tmp_path, "duplicates", [_group(tmp_path, make_pattern_jpeg, "1", 1)])
    results = _finished(qtbot, page)
    assert page.confirm_button.isEnabled()
    for card in page.cards:
        card.keep_check.setChecked(False)
    assert not page.confirm_button.isEnabled()
    page.confirm_button.click()
    assert results == []
    page.cards[1].keep_check.setChecked(True)
    assert page.confirm_button.isEnabled()


def test_confirm_flow_emits_keep(qtbot, tmp_path, make_page, make_pattern_jpeg):
    groups = [_group(tmp_path, make_pattern_jpeg, "1", 1),
              _group(tmp_path, make_pattern_jpeg, "2", 2)]
    page = make_page()
    page.load(tmp_path, "duplicates", groups)
    results = _finished(qtbot, page)
    page.confirm_button.click()
    assert page.heading_label.text() == "Group 2 of 2 · Exact copies"
    assert page.back_button.isEnabled()
    page.skip_button.click()
    assert results == [{0: {"1a.jpg"}}]


def test_keep_all_moves_nothing(qtbot, tmp_path, make_page, make_pattern_jpeg):
    groups = [_group(tmp_path, make_pattern_jpeg, "1", 1)]
    page = make_page()
    page.load(tmp_path, "duplicates", groups)
    results = _finished(qtbot, page)
    page.keep_all_button.click()
    assert results == [{0: {"1a.jpg", "1b.jpg", "1c.jpg"}}]


def test_previous_group_keeps_answers(qtbot, tmp_path, make_page, make_pattern_jpeg):
    groups = [_group(tmp_path, make_pattern_jpeg, "1", 1),
              _group(tmp_path, make_pattern_jpeg, "2", 2)]
    page = make_page()
    page.load(tmp_path, "duplicates", groups)
    results = _finished(qtbot, page)
    page.cards[2].keep_check.setChecked(True)
    page.confirm_button.click()
    page.back_button.click()
    assert page.heading_label.text().startswith("Group 1 of 2")
    assert _ticked(page) == {"1a.jpg", "1c.jpg"}  # the earlier answer, still editable
    page.cards[0].keep_check.setChecked(False)
    page.confirm_button.click()
    assert _ticked(page) == {"2a.jpg"}  # group 2 was never answered: the suggestion
    page.confirm_button.click()
    assert results == [{0: {"1c.jpg"}, 1: {"2a.jpg"}}]


def test_keep_one_sharpest_repeated_mode(qtbot, tmp_path, make_page, make_pattern_jpeg):
    group = _group(tmp_path, make_pattern_jpeg, "r", 3, "repeated")
    group.suggested_keep = {"rb.jpg"}  # not the sharpest
    page = make_page()
    page.load(tmp_path, "repeated", [group])
    results = _finished(qtbot, page)
    page.cards[2].keep_check.setChecked(True)
    page.keep_one_button.click()
    assert results == [{0: {"ra.jpg"}}]


def test_stop_reviewing_emits_confirmed_so_far(qtbot, tmp_path, make_page, make_pattern_jpeg):
    groups = [_group(tmp_path, make_pattern_jpeg, "1", 1),
              _group(tmp_path, make_pattern_jpeg, "2", 2)]
    page = make_page()
    page.load(tmp_path, "duplicates", groups)
    results = _finished(qtbot, page)
    page.stop_button.click()
    assert results == [{}]
    page.load(tmp_path, "duplicates", groups)
    page.confirm_button.click()
    page.stop_button.click()
    assert results[-1] == {0: {"1a.jpg"}}


def test_cancel_emits_cancelled(qtbot, tmp_path, make_page, make_pattern_jpeg):
    page = make_page()
    page.load(tmp_path, "duplicates", [_group(tmp_path, make_pattern_jpeg, "1", 1)])
    results = _finished(qtbot, page)
    with qtbot.waitSignal(page.cancelled, timeout=1000):
        page.cancel_button.click()
    assert results == []


ENGLISH_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class _GermanDatetime(datetime):
    """A datetime that formats %b as a German locale would."""

    def strftime(self, fmt):
        german = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                  "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"][self.month - 1]
        return super().strftime(fmt.replace("%b", german))


def _foreign_locale(locale) -> bool:
    """Switch LC_TIME to an installed non-English locale; False if there is none."""
    for name in ("de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8", "nl_NL.UTF-8", "bn_IN.UTF-8"):
        try:
            locale.setlocale(locale.LC_TIME, name)
            return True
        except locale.Error:
            continue
    return False


def test_card_month_is_english_whatever_the_locale():
    import locale

    from photosort.ui.review_page import _taken_text

    for month in range(1, 13):  # a datetime that formats like a German locale
        taken = _GermanDatetime(2025, month, 20, 18, 13, 25)
        assert _taken_text(taken) == f"20 {ENGLISH_MONTHS[month - 1]} 2025, 18:13:25"

    old = locale.setlocale(locale.LC_TIME)
    try:
        if _foreign_locale(locale):  # and a real one, when one is installed
            for month in range(1, 13):
                text = _taken_text(datetime(2025, month, 20, 18, 13, 25))
                assert text == f"20 {ENGLISH_MONTHS[month - 1]} 2025, 18:13:25"
    finally:
        locale.setlocale(locale.LC_TIME, old)
