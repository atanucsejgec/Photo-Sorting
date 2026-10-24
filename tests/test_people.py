import json

import numpy as np
import pytest
from PIL import Image

from photosort import people
from photosort.people import PeopleError, PeopleStore


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _towards(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """A unit vector between a (t=0) and b (t=1)."""
    v = (1 - t) * a + t * b
    return (v / np.linalg.norm(v)).astype(np.float32)


def _thumb(size=(400, 300)) -> Image.Image:
    return Image.new("RGB", size, "blue")


def test_add_reload_and_list_sorted(tmp_path):
    store = PeopleStore(tmp_path)
    zed = store.add("zed", _unit(1), _thumb())
    amy = store.add("Amy", _unit(2), _thumb())
    bob = store.add("bob", _unit(3), _thumb())
    assert [p.name for p in store.people()] == ["Amy", "bob", "zed"]

    reloaded = PeopleStore(tmp_path).people()
    assert [p.id for p in reloaded] == [amy.id, bob.id, zed.id]
    first = reloaded[0]
    assert first.name == "Amy"
    assert first.embeddings.shape == (1, 512) and first.embeddings.dtype == np.float32
    assert np.array_equal(first.embeddings[0], _unit(2))
    assert first.thumb == tmp_path / f"{amy.id}.jpg"
    assert (tmp_path / f"{amy.id}.npy").is_file()
    with Image.open(first.thumb) as img:
        assert img.format == "JPEG"
        assert max(img.size) == 160
    listing = json.loads((tmp_path / "people.json").read_text())
    assert {p["id"] for p in listing["people"]} == {amy.id, bob.id, zed.id}
    assert sorted(f.name for f in tmp_path.iterdir() if f.name.endswith(".tmp")) == []


def test_empty_store(tmp_path):
    store = PeopleStore(tmp_path / "not yet")
    assert store.people() == []
    assert store.best_match(_unit(1), []) is None


def test_default_folder_is_in_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(people.platformdirs, "user_data_dir", lambda app: str(tmp_path / app))
    store = PeopleStore()
    store.add("Amy", _unit(1), _thumb())
    assert (tmp_path / "photo-sorter" / "people" / "people.json").is_file()


def test_name_is_cleaned(tmp_path):
    store = PeopleStore(tmp_path)
    assert store.add("Atanu/B.", _unit(1), _thumb()).name == "Atanu_B"
    assert store.add("  Mum  ", _unit(2), _thumb()).name == "Mum"


@pytest.mark.parametrize("name", ["", "   ", "...", " . . "])
def test_empty_name_refused(tmp_path, name):
    with pytest.raises(PeopleError, match=r"^Type a name\.$"):
        PeopleStore(tmp_path).add(name, _unit(1), _thumb())


def test_duplicate_in_other_case_refused(tmp_path):
    store = PeopleStore(tmp_path)
    store.add("Atanu", _unit(1), _thumb())
    with pytest.raises(PeopleError, match=r"^A person called ATANU already exists\.$"):
        store.add("ATANU", _unit(2), _thumb())
    with pytest.raises(PeopleError, match="already exists"):
        PeopleStore(tmp_path).add("atanu.", _unit(2), _thumb())  # cleaned, then compared
    assert [p.name for p in store.people()] == ["Atanu"]


def test_add_samples_improves_best_match(tmp_path):
    store = PeopleStore(tmp_path)
    a, b = _unit(1), _unit(2)
    person = store.add("Amy", a, _thumb())
    query = _towards(a, b, 0.8)  # much closer to b than to a
    _, before = store.best_match(query, [person.id])
    store.add_samples(person.id, np.stack([b, _unit(3)]))
    match = store.best_match(query, [person.id])
    assert match[0] == person.id
    assert match[1] > before
    assert match[1] == pytest.approx(float(np.dot(query, b)), abs=1e-6)
    reloaded = PeopleStore(tmp_path).people()[0]
    assert reloaded.embeddings.shape == (3, 512)
    assert PeopleStore(tmp_path).best_match(query, [person.id]) == pytest.approx(match)


def test_add_samples_unknown_person(tmp_path):
    with pytest.raises(PeopleError):
        PeopleStore(tmp_path).add_samples("nobody", _unit(1)[None])


def test_delete_removes_files(tmp_path):
    store = PeopleStore(tmp_path)
    amy = store.add("Amy", _unit(1), _thumb())
    bob = store.add("Bob", _unit(2), _thumb())
    store.delete(amy.id)
    assert [p.id for p in store.people()] == [bob.id]
    assert [p.id for p in PeopleStore(tmp_path).people()] == [bob.id]
    assert not (tmp_path / f"{amy.id}.npy").exists()
    assert not (tmp_path / f"{amy.id}.jpg").exists()
    assert (tmp_path / f"{bob.id}.npy").exists()
    store.add("Amy", _unit(3), _thumb())  # the name is free again


def test_best_match_over_subset(tmp_path):
    store = PeopleStore(tmp_path)
    a, b, c = _unit(1), _unit(2), _unit(3)
    amy = store.add("Amy", a, _thumb())
    bob = store.add("Bob", b, _thumb())
    cat = store.add("Cat", c, _thumb())
    query = _towards(a, b, 0.4)  # closest to Amy, then Bob
    assert store.best_match(query, [amy.id, bob.id, cat.id])[0] == amy.id
    best_id, score = store.best_match(query, [bob.id, cat.id])
    assert best_id == bob.id
    assert score == pytest.approx(float(np.dot(query, b)), abs=1e-6)
    assert store.best_match(query, [cat.id])[0] == cat.id
    assert store.best_match(query, []) is None
    assert store.best_match(query, ["nobody"]) is None


def test_sees_changes_made_by_another_store(tmp_path):
    one, two = PeopleStore(tmp_path), PeopleStore(tmp_path)
    amy = one.add("Amy", _unit(1), _thumb())
    assert [p.id for p in two.people()] == [amy.id]
    one.add_samples(amy.id, _unit(2)[None])
    assert two.people()[0].embeddings.shape == (2, 512)
    one.delete(amy.id)
    assert two.people() == []


def test_unreadable_list_is_not_overwritten(tmp_path):
    (tmp_path / "people.json").write_text("{ not json")
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError):
        store.people()
    with pytest.raises(PeopleError):
        store.add("Amy", _unit(1), _thumb())
    assert (tmp_path / "people.json").read_text() == "{ not json"
