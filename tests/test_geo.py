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


@pytest.mark.parametrize("found, place", [
    ({"country": "", "city": "Kolkata"}, None),
    ({"country": "  ", "city": "Kolkata"}, None),
    ({"city": "Kolkata"}, None),
    ({"country": "India"}, ("India", "")),
    ({"country": " India ", "city": None}, ("India", "")),
    ({"country": "India", "city": " Kolkata "}, ("India", "Kolkata")),
])
def test_what_the_lookup_gives(monkeypatch, loaded, found, place):
    monkeypatch.setattr(loaded, "get", lambda coordinate: found)
    assert geo.place_of(22.5726, 88.3639) == place


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
