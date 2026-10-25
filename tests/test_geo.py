import logging

import pytest

from photosort import geo


@pytest.fixture
def loaded():
    """reverse_geocode, its data loaded (so a test can then replace its lookup)."""
    import reverse_geocode

    assert geo._geocoder() is reverse_geocode
    return reverse_geocode


@pytest.fixture
def fresh_geocoder():
    """A geocoder loaded afresh in the test, and again by later tests."""
    geo._geocoder.cache_clear()
    yield
    geo._geocoder.cache_clear()


def test_kolkata():
    assert geo.place_of(22.5726, 88.3639) == ("India", "Kolkata")


def test_paris():
    assert geo.place_of(48.8566, 2.3522) == ("France", "Paris")


def test_south_and_west():
    assert geo.place_of(-33.8688, 151.2093) == ("Australia", "Sydney")
    assert geo.place_of(40.7128, -74.0060) == ("United States", "New York City")


def test_a_failed_lookup_gives_none(monkeypatch, loaded):
    def fail(coordinate):
        raise ValueError("bad coordinate")

    monkeypatch.setattr(loaded, "get", fail)
    assert geo.place_of(22.5726, 88.3639) is None
    assert geo.place_of(48.8566, 2.3522) is None


NEAR = {"latitude": 22.56263, "longitude": 88.36304}  # Kolkata's own position


@pytest.mark.parametrize("found, place", [
    ({"country": "", "city": "Kolkata"}, None),
    ({"country": "  ", "city": "Kolkata"}, None),
    ({"city": "Kolkata"}, None),
    ({"country": "India"}, ("India", "")),
    ({"country": " India ", "city": None}, ("India", "")),
    ({"country": "India", "city": " Kolkata "}, ("India", "Kolkata")),
])
def test_what_the_lookup_gives(monkeypatch, loaded, found, place):
    monkeypatch.setattr(loaded, "get", lambda coordinate: {**found, **NEAR})
    assert geo.place_of(22.5726, 88.3639) == place


@pytest.mark.parametrize("position", [
    (35.0, -40.0),  # mid-Atlantic: the nearest city is in the Azores, 900 km away
    (-90.0, 0.0),  # the South Pole
])
def test_far_from_any_city_gives_none(position):
    assert geo.place_of(*position) is None


def test_the_city_must_be_within_the_cap(monkeypatch, loaded):
    # 1 degree of latitude is about 111 km.
    city = {"country": "India", "city": "Kolkata", "latitude": 22.0, "longitude": 88.0}
    monkeypatch.setattr(loaded, "get", lambda coordinate: city)
    assert geo.place_of(23.79, 88.0) == ("India", "Kolkata")  # 199 km
    assert geo.place_of(23.81, 88.0) is None  # 201 km
    assert geo.place_of(22.0, 89.0) == ("India", "Kolkata")  # 103 km east
    assert geo.place_of(22.0, -92.0) is None  # the far side of the earth


def test_a_lookup_without_the_city_position_gives_none(monkeypatch, loaded):
    monkeypatch.setattr(loaded, "get", lambda coordinate: {"country": "India", "city": "X"})
    assert geo.place_of(22.5726, 88.3639) is None


def test_never_downloads_missing_data(monkeypatch, fresh_geocoder):
    import reverse_geocode

    def no_network(*args, **kwargs):
        raise AssertionError("reverse_geocode tried to download")

    monkeypatch.setattr(reverse_geocode, "urlopen", no_network)
    monkeypatch.setattr(geo, "_DATA_FILES", ("no such file.gz",))
    assert geo.place_of(22.5726, 88.3639) is None


def test_a_geocoder_that_fails_to_load_is_not_retried(monkeypatch, fresh_geocoder):
    import reverse_geocode

    loads = []

    def fail(coordinate):
        loads.append(coordinate)
        raise OSError("corrupt data")

    monkeypatch.setattr(reverse_geocode, "get", fail)
    assert geo.place_of(22.5726, 88.3639) is None
    assert geo.place_of(48.8566, 2.3522) is None
    assert len(loads) == 1


def test_a_failed_load_is_logged_once(monkeypatch, fresh_geocoder, caplog):
    import reverse_geocode

    def fail(coordinate):
        raise OSError("corrupt data")

    monkeypatch.setattr(reverse_geocode, "get", fail)
    with caplog.at_level(logging.WARNING, logger="photosort"):
        geo.place_of(22.5726, 88.3639)
        geo.place_of(48.8566, 2.3522)
    records = [r for r in caplog.records if r.name == "photosort"]
    assert len(records) == 1
    assert "corrupt data" in records[0].getMessage()


def test_missing_data_is_logged(monkeypatch, fresh_geocoder, caplog):
    monkeypatch.setattr(geo, "_DATA_FILES", ("no such file.gz",))
    with caplog.at_level(logging.WARNING, logger="photosort"):
        assert geo.place_of(22.5726, 88.3639) is None
    assert "no such file.gz" in caplog.text
