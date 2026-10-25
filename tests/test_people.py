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


@pytest.mark.parametrize("name", ["Duplicates", "repeated shots", "Bad Photos.",
                                  ".photosort", ".Secret", "  .x"])
def test_names_of_skipped_folders_refused(tmp_path, name):
    with pytest.raises(PeopleError, match=r"^Photo Sorter uses that name for its own "
                                          r"folder\. Pick another name\.$"):
        PeopleStore(tmp_path).add(name, _unit(1), _thumb())
    assert PeopleStore(tmp_path).people() == []


@pytest.mark.parametrize("name", ["x" * 51, "অ" * 41])  # 51 characters; 123 bytes
def test_too_long_name_refused(tmp_path, name):
    with pytest.raises(PeopleError, match=r"^That name is too long\.$"):
        PeopleStore(tmp_path).add(name, _unit(1), _thumb())
    store = PeopleStore(tmp_path)
    assert store.add("x" * 50, _unit(1), _thumb()).name == "x" * 50
    assert store.add("অ" * 40, _unit(2), _thumb()).name == "অ" * 40  # 120 bytes


def test_too_long_name_on_disk_kept_raw(tmp_path):
    odd_id = "fedcba9876543210"
    np.save(tmp_path / f"{odd_id}.npy", _unit(2)[None])
    raw = [{"id": odd_id, "name": "x" * 51, "thumb": f"{odd_id}.jpg"}]
    (tmp_path / "people.json").write_text(json.dumps({"people": raw}))
    store = PeopleStore(tmp_path)
    assert store.people() == []
    store.add("Bob", _unit(3), _thumb())
    assert _listed(tmp_path)[0] == raw[0]


def test_skipped_folder_name_on_disk_kept_raw(tmp_path):
    good_id, odd_id = "0123456789abcdef", "fedcba9876543210"
    np.save(tmp_path / f"{good_id}.npy", _unit(1)[None])
    np.save(tmp_path / f"{odd_id}.npy", _unit(2)[None])
    raw = [{"id": good_id, "name": "Amy", "thumb": f"{good_id}.jpg"},
           {"id": odd_id, "name": "Duplicates", "thumb": f"{odd_id}.jpg"}]
    (tmp_path / "people.json").write_text(json.dumps({"people": raw}))
    store = PeopleStore(tmp_path)
    assert [p.name for p in store.people()] == ["Amy"]
    store.add("Bob", _unit(3), _thumb())
    assert _listed(tmp_path)[1] == raw[1]  # written back as it was


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


def _listed(folder):
    return json.loads((folder / "people.json").read_text())["people"]


def test_unreadable_samples_never_forget_the_person(tmp_path):
    store = PeopleStore(tmp_path)
    amy = store.add("Amy", _unit(1), _thumb())
    bob = store.add("Bob", _unit(2), _thumb())
    good = (tmp_path / f"{amy.id}.npy").read_bytes()
    (tmp_path / f"{amy.id}.npy").write_bytes(b"held open, half read")

    fresh = PeopleStore(tmp_path)
    assert [p.id for p in fresh.people()] == [bob.id]  # not offered
    assert fresh.best_match(_unit(1), [amy.id]) is None
    fresh.add_samples(bob.id, _unit(3)[None])
    fresh.add("Cat", _unit(4), _thumb())
    with pytest.raises(PeopleError, match="already exists"):
        fresh.add("amy", _unit(5), _thumb())
    with pytest.raises(PeopleError):
        fresh.delete(amy.id)
    assert [p["name"] for p in _listed(tmp_path)] == ["Amy", "Bob", "Cat"]
    assert (tmp_path / f"{amy.id}.jpg").is_file()

    (tmp_path / f"{amy.id}.npy").write_bytes(good)  # the fault passes
    back = PeopleStore(tmp_path).people()
    assert [p.name for p in back] == ["Amy", "Bob", "Cat"]
    assert np.array_equal(back[0].embeddings[0], _unit(1))


@pytest.mark.parametrize("bad", [
    np.zeros(511, np.float32),
    np.zeros((0, 512), np.float32),
    np.zeros((2, 511), np.float32),
    np.zeros((1, 1, 512), np.float32),
    np.zeros(512, np.int64),
])
def test_bad_sample_shapes_refused(tmp_path, bad):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError):
        store.add("Amy", bad, _thumb())
    amy = store.add("Amy", _unit(1), _thumb())
    with pytest.raises(PeopleError):
        store.add_samples(amy.id, bad)
    assert store.people()[0].embeddings.shape == (1, 512)


def test_samples_of_wrong_shape_on_disk_kept_but_not_used(tmp_path):
    store = PeopleStore(tmp_path)
    amy = store.add("Amy", _unit(1), _thumb())
    np.save(tmp_path / f"{amy.id}.npy", np.zeros((2, 10), np.float32))
    fresh = PeopleStore(tmp_path)
    assert fresh.people() == []
    fresh.add("Bob", _unit(2), _thumb())
    assert [p["id"] for p in _listed(tmp_path)][0] == amy.id
    with pytest.raises(PeopleError, match="already exists"):
        fresh.add("AMY", _unit(3), _thumb())


def test_entries_not_ours_are_kept_raw_and_never_used(tmp_path):
    good_id, other_id = "0123456789abcdef", "fedcba9876543210"
    np.save(tmp_path / f"{good_id}.npy", _unit(1)[None])
    np.save(tmp_path / f"{other_id}.npy", _unit(2)[None])
    outside = tmp_path / "outside.npy"
    np.save(outside, _unit(3)[None])
    raw = [
        {"id": good_id, "name": "Amy", "thumb": f"{good_id}.jpg"},
        {"id": other_id, "name": "amy", "thumb": f"{other_id}.jpg"},  # repeats Amy
        {"id": other_id, "name": "Bad/Name", "thumb": f"{other_id}.jpg"},  # not clean
        {"id": other_id, "name": "Dot.", "thumb": f"{other_id}.jpg"},  # not clean
        {"id": "../outside", "name": "Eve", "thumb": "../outside.jpg"},  # not an id of ours
        {"id": "ABCDEF0123456789", "name": "Fay", "thumb": "ABCDEF0123456789.jpg"},
        {"id": other_id, "name": "Gus", "thumb": "people.json"},  # not its thumbnail
        "not even an entry",
    ]
    (tmp_path / "people.json").write_text(json.dumps({"people": raw}))
    store = PeopleStore(tmp_path)
    assert [(p.id, p.name) for p in store.people()] == [(good_id, "Amy")]
    assert store.best_match(_unit(3), ["../outside"]) is None
    with pytest.raises(PeopleError):
        store.delete("../outside")
    with pytest.raises(PeopleError, match="already exists"):
        store.add("eve", _unit(4), _thumb())  # a kept entry's name stays taken
    store.add("Hal", _unit(5), _thumb())
    assert _listed(tmp_path)[1:len(raw)] == raw[1:]
    assert outside.is_file()


def test_repeated_id_is_kept_raw_so_delete_spares_the_files(tmp_path):
    shared = "0123456789abcdef"
    np.save(tmp_path / f"{shared}.npy", _unit(1)[None])
    _thumb().save(tmp_path / f"{shared}.jpg")
    raw = [
        {"id": shared, "name": "Amy", "thumb": f"{shared}.jpg"},
        {"id": shared, "name": "Bob", "thumb": f"{shared}.jpg"},  # the same files
    ]
    (tmp_path / "people.json").write_text(json.dumps({"people": raw}))
    store = PeopleStore(tmp_path)
    assert [p.name for p in store.people()] == ["Amy"]
    assert store.best_match(_unit(1), [shared])[0] == shared
    store.delete(shared)  # Amy's entry goes; Bob's stays, raw
    assert _listed(tmp_path) == raw[1:]


JOY_COMPOSED = "জয়"  # জয়, typed with য় as one character
JOY_SPLIT = "জয়"  # জয়, typed as য plus the nukta sign


def test_names_typed_two_ways_are_one_person(tmp_path):
    store = PeopleStore(tmp_path)
    joy = store.add(JOY_COMPOSED, _unit(1), _thumb())
    with pytest.raises(PeopleError, match="already exists"):
        store.add(JOY_SPLIT, _unit(2), _thumb())
    assert [p.id for p in PeopleStore(tmp_path).people()] == [joy.id]


def test_names_are_saved_normalised(tmp_path):
    store = PeopleStore(tmp_path)
    assert store.add(JOY_COMPOSED, _unit(1), _thumb()).name == JOY_SPLIT  # NFC
    assert store.people()[0].name == JOY_SPLIT


def test_names_on_disk_are_normalised_when_loaded(tmp_path):
    first, second = "0123456789abcdef", "fedcba9876543210"
    np.save(tmp_path / f"{first}.npy", _unit(1)[None])
    np.save(tmp_path / f"{second}.npy", _unit(2)[None])
    raw = [{"id": first, "name": JOY_COMPOSED, "thumb": f"{first}.jpg"},
           {"id": second, "name": JOY_SPLIT.upper(), "thumb": f"{second}.jpg"}]
    (tmp_path / "people.json").write_text(json.dumps({"people": raw}))
    store = PeopleStore(tmp_path)
    (joy,) = store.people()  # the second is the same name: kept raw, not used
    assert (joy.id, joy.name) == (first, JOY_SPLIT)
    with pytest.raises(PeopleError, match="already exists"):
        store.add(JOY_COMPOSED, _unit(3), _thumb())
    store.add("Bob", _unit(4), _thumb())
    assert _listed(tmp_path)[1] == raw[1]


@pytest.mark.parametrize("name", ["Atanu & Zoe", "A & B & C", " Mum & Dad "])
def test_names_with_an_ampersand_between_spaces_refused(tmp_path, name):
    with pytest.raises(PeopleError, match=r'^A name can\'t contain " & "\. Photo Sorter '
                                          r"uses it for photos with several people\.$"):
        PeopleStore(tmp_path).add(name, _unit(1), _thumb())
    assert PeopleStore(tmp_path).people() == []
    assert PeopleStore(tmp_path).add("Tom&Jerry", _unit(1), _thumb()).name == "Tom&Jerry"


def test_name_with_an_ampersand_on_disk_kept_raw(tmp_path):
    odd_id = "fedcba9876543210"
    np.save(tmp_path / f"{odd_id}.npy", _unit(2)[None])
    raw = [{"id": odd_id, "name": "Atanu & Zoe", "thumb": f"{odd_id}.jpg"}]
    (tmp_path / "people.json").write_text(json.dumps({"people": raw}))
    store = PeopleStore(tmp_path)
    assert store.people() == []
    store.add("Bob", _unit(3), _thumb())
    assert _listed(tmp_path)[0] == raw[0]
