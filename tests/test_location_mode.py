import os
import subprocess
import sys
import threading

import pytest
from mp4util import box, mp4, udta_text, write
from PIL import Image

from photosort import geo
from photosort.modes.location_mode import place_folder, prepare_location_plan
from photosort.naming import fits
from photosort.thresholds import FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES

GPS_IFD = 0x8825
KOLKATA = 22.5726, 88.3639
PARIS = 48.8566, 2.3522
NO_LOCATION = "1 files have no location and stay where they are"


def _dms(degrees):
    degrees = abs(degrees)
    minutes = (degrees - int(degrees)) * 60
    return float(int(degrees)), float(int(minutes)), round((minutes - int(minutes)) * 60, 4)


def jpeg(path, place=None):
    """A JPEG taken at place, (latitude, longitude), or with no GPS if None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if place is not None:
        lat, lon = place
        exif[GPS_IFD] = {1: "N" if lat >= 0 else "S", 2: _dms(lat),
                         3: "E" if lon >= 0 else "W", 4: _dms(lon)}
    Image.new("RGB", (8, 8), "red").save(path, "JPEG", exif=exif)
    return path


def _dsts(plan):
    return {m.src: m.dst for m in plan.moves}


# place_folder

@pytest.mark.parametrize("country, city, folder", [
    ("India", "Kolkata", "India/Kolkata"),
    ("India", "", "India"),
    ("India", "...", "India"),  # nothing usable in the city: the country alone
    ("", "Kolkata", ""),  # no country: no folder
    ("::", "Kolkata", ""),
    ("CON", "a/b", "_CON/a_b"),
    ("Bad Photos", ".photosort", "_Bad Photos/_.photosort"),  # never a folder the scan skips
    ("Côte d'Ivoire", "Abidjan.", "Côte d'Ivoire/Abidjan"),
    ("ভারত", "কলকাতা", "ভারত/কলকাতা"),
])
def test_place_folder(country, city, folder):
    assert place_folder(country, city) == folder


def test_place_folder_cuts_long_names():
    country, city = place_folder("C" * 150, "ক" * 150).split("/")
    assert country == "C" * FOLDER_NAME_MAX
    assert fits(city, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)
    assert len(city.encode()) <= FOLDER_NAME_MAX_BYTES < len(("ক" * 100).encode())


# the plan

def test_one_folder_per_country_and_city(tmp_path):
    jpeg(tmp_path / "a.jpg", KOLKATA)
    jpeg(tmp_path / "b.jpg", (22.57, 88.36))
    jpeg(tmp_path / "c.jpg", PARIS)
    plan = prepare_location_plan(tmp_path, False)
    assert plan.mode == "location"
    assert _dsts(plan) == {
        "a.jpg": "India/Kolkata/a.jpg",
        "b.jpg": "India/Kolkata/b.jpg",
        "c.jpg": "France/Paris/c.jpg",
    }
    assert plan.summary == ["3 files → 2 countries, 2 cities"]
    assert plan.unchanged == 0


def test_two_cities_in_one_country(tmp_path):
    jpeg(tmp_path / "a.jpg", KOLKATA)
    jpeg(tmp_path / "b.jpg", (28.6139, 77.2090))  # New Delhi
    plan = prepare_location_plan(tmp_path, False)
    assert plan.summary == ["2 files → 1 countries, 2 cities"]
    assert {dst.split("/")[0] for dst in _dsts(plan).values()} == {"India"}


def test_files_without_location_stay(tmp_path):
    jpeg(tmp_path / "a.jpg", KOLKATA)
    jpeg(tmp_path / "plain.jpg")
    write(tmp_path / "broken.jpg", b"not a jpeg")
    plan = prepare_location_plan(tmp_path, False)
    assert _dsts(plan) == {"a.jpg": "India/Kolkata/a.jpg"}
    assert plan.summary == [
        "1 files → 1 countries, 1 cities",
        "2 files have no location and stay where they are",
    ]


def test_one_file_without_location(tmp_path):
    jpeg(tmp_path / "plain.jpg")
    plan = prepare_location_plan(tmp_path, False)
    assert plan.moves == []
    assert plan.summary == [NO_LOCATION]


def test_a_failed_lookup_stays(tmp_path, monkeypatch):
    jpeg(tmp_path / "a.jpg", KOLKATA)
    monkeypatch.setattr(geo, "place_of", lambda lat, lon: None)
    plan = prepare_location_plan(tmp_path, False)
    assert plan.moves == []
    assert plan.summary == [NO_LOCATION]


def test_video_location(tmp_path):
    write(tmp_path / "v.mp4", mp4(box("udta", udta_text("©xyz", "+48.8566+002.3522/"))))
    plan = prepare_location_plan(tmp_path, False)
    assert _dsts(plan) == {"v.mp4": "France/Paris/v.mp4"}


def test_rerun_with_subfolders_is_unchanged(tmp_path):
    jpeg(tmp_path / "India" / "Kolkata" / "a.jpg", KOLKATA)
    jpeg(tmp_path / "France" / "Paris" / "c.jpg", PARIS)
    jpeg(tmp_path / "France" / "Paris" / "d.jpg", KOLKATA)  # in the wrong city
    plan = prepare_location_plan(tmp_path, True)
    assert _dsts(plan) == {"France/Paris/d.jpg": "India/Kolkata/d.jpg"}
    assert plan.unchanged == 2
    assert plan.summary == ["1 files → 1 countries, 1 cities", "2 already in place"]


def test_rerun_matches_folders_in_any_case(tmp_path):
    jpeg(tmp_path / "india" / "KOLKATA" / "a.jpg", KOLKATA)
    jpeg(tmp_path / "b.jpg", KOLKATA)
    plan = prepare_location_plan(tmp_path, True)
    assert _dsts(plan) == {"b.jpg": "india/KOLKATA/b.jpg"}  # the folder on disk is reused
    assert plan.summary == ["1 files → 1 countries, 1 cities", "1 already in place"]


def test_name_clash_gets_a_number(tmp_path):
    jpeg(tmp_path / "India" / "Kolkata" / "a.jpg", KOLKATA)
    jpeg(tmp_path / "a.jpg", KOLKATA)
    plan = prepare_location_plan(tmp_path, False)
    assert _dsts(plan) == {"a.jpg": "India/Kolkata/a (1).jpg"}


def test_windows_invalid_place_gives_a_valid_folder(tmp_path, monkeypatch):
    jpeg(tmp_path / "x.jpg", KOLKATA)
    monkeypatch.setattr(geo, "place_of", lambda lat, lon: ("CON", "a/b"))
    plan = prepare_location_plan(tmp_path, False)
    assert _dsts(plan) == {"x.jpg": "_CON/a_b/x.jpg"}
    assert plan.summary == ["1 files → 1 countries, 1 cities"]


def test_bengali_place(tmp_path, monkeypatch):
    jpeg(tmp_path / "x.jpg", KOLKATA)
    monkeypatch.setattr(geo, "place_of", lambda lat, lon: ("ভারত", "কলকাতা"))
    plan = prepare_location_plan(tmp_path, False)
    assert _dsts(plan) == {"x.jpg": "ভারত/কলকাতা/x.jpg"}


def test_a_place_with_no_city_goes_to_the_country(tmp_path, monkeypatch):
    jpeg(tmp_path / "x.jpg", KOLKATA)
    monkeypatch.setattr(geo, "place_of", lambda lat, lon: ("India", ""))
    plan = prepare_location_plan(tmp_path, False)
    assert _dsts(plan) == {"x.jpg": "India/x.jpg"}
    assert plan.summary == ["1 files → 1 countries, 0 cities"]


def test_an_unusable_country_stays(tmp_path, monkeypatch):
    jpeg(tmp_path / "x.jpg", KOLKATA)
    monkeypatch.setattr(geo, "place_of", lambda lat, lon: ("...", "Kolkata"))
    plan = prepare_location_plan(tmp_path, False)
    assert plan.moves == []
    assert plan.summary == [NO_LOCATION]


def test_subfolders_off_leaves_subfolders_alone(tmp_path):
    jpeg(tmp_path / "sub" / "a.jpg", KOLKATA)
    jpeg(tmp_path / "b.jpg", PARIS)
    plan = prepare_location_plan(tmp_path, False)
    assert _dsts(plan) == {"b.jpg": "France/Paris/b.jpg"}


def test_progress_and_cancel(tmp_path):
    for name in "abc":
        jpeg(tmp_path / f"{name}.jpg", KOLKATA)
    seen = []
    prepare_location_plan(tmp_path, False, progress=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 3), (2, 3), (3, 3)]

    cancel = threading.Event()
    calls = []

    def progress(i, n):
        calls.append(i)
        cancel.set()

    assert prepare_location_plan(tmp_path, False, progress=progress, cancel=cancel) is None
    assert calls == [1]


def test_location_loads_geocoder_lazily():
    code = ("import sys, photosort.ui.main_window, photosort.modes, photosort.geo, "
            "photosort.modes.location_mode; "
            "print('reverse_geocode' in sys.modules)")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=True, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert result.stdout.strip() == "False"
