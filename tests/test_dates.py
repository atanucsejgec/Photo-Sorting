from datetime import date, datetime, timezone

import pytest

from photosort.dates import DateResult, date_from_filename, find_date

TODAY = date(2026, 10, 24)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("IMG20250220181303.jpg", date(2025, 2, 20)),
        ("IMG20250220181303~2.jpg", date(2025, 2, 20)),
        ("IMG20250226120057_BURST001_COVER.jpg", date(2025, 2, 26)),
        ("IMG-20250315-WA0036.jpg", date(2025, 3, 15)),
        ("VID20250301101010.mp4", date(2025, 3, 1)),
        ("Screenshot_2025-02-20-18-13-03-123_com.whatsapp.jpg", date(2025, 2, 20)),
        ("PXL_20250220_181303123.jpg", date(2025, 2, 20)),
    ],
)
def test_filename_patterns(name, expected):
    assert date_from_filename(name, today=TODAY) == expected


def test_filename_ms_epoch():
    name = "1752070136514-9e348cb0-dad6-4a6d-bce0-81f92d2a193f.jpg"
    assert date_from_filename(name) == datetime.fromtimestamp(1752070136.514).date()


@pytest.mark.parametrize(
    "name",
    [
        "holiday.jpg",
        "IMG_20251399_000000.jpg",
        "20991231_120000.jpg",
        "abcd-20250230.jpg",
        "9999999999999-x.jpg",
    ],
)
def test_filename_rejects(name):
    assert date_from_filename(name, today=TODAY) is None


def test_metadata_photo(tmp_path, make_jpeg):
    p = make_jpeg(tmp_path / "x.jpg", "2025:02:20 18:13:03")
    assert find_date(p) == DateResult(date(2025, 2, 20), "metadata")


def test_metadata_wins_over_filename(tmp_path, make_jpeg):
    p = make_jpeg(tmp_path / "IMG20240101000000.jpg", "2025:02:20 18:13:03")
    assert find_date(p) == DateResult(date(2025, 2, 20), "metadata")


def test_metadata_video(tmp_path, make_mp4):
    p = make_mp4(tmp_path / "v.mp4", "2025-03-01T10:10:10Z")
    expected = datetime(2025, 3, 1, 10, 10, 10, tzinfo=timezone.utc).astimezone().date()
    assert find_date(p) == DateResult(expected, "metadata")


def test_junk_metadata_falls_back(tmp_path, make_jpeg, make_mp4):
    p = make_jpeg(tmp_path / "IMG-20250315-WA0036.jpg", "0000:00:00 00:00:00")
    assert find_date(p) == DateResult(date(2025, 3, 15), "filename")

    p = make_jpeg(tmp_path / "x.jpg", "2099:01:01 00:00:00")
    assert find_date(p) == DateResult(None, "none")

    p = make_mp4(tmp_path / "v.mp4", "1904-01-01T00:00:00Z")
    assert find_date(p) == DateResult(None, "none")


def test_broken_files_dont_raise(tmp_path):
    empty = tmp_path / "IMG20250220181303.jpg"
    empty.write_bytes(b"")
    assert find_date(empty) == DateResult(date(2025, 2, 20), "filename")

    text = tmp_path / "note.jpg"
    text.write_text("this is not a photo")
    assert find_date(text) == DateResult(None, "none")
