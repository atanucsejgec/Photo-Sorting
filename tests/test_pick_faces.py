import threading
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

from photosort.cache import AnalysisCache
from photosort.executor import execute
from photosort.faces import FACES_KIND, Face
from photosort.hashing import sha256_file
from photosort.imageinfo import UnreadableImage
from photosort.journal import RunJournal, load_runs
from photosort.modes.pick_faces import (
    FoundFace,
    PersonGroup,
    PickFolder,
    check_folder_name,
    group_people,
    known_names,
    photo_folders,
    plan_pick,
    scan_faces,
)
from photosort.people import Person
from photosort.plan import Move
from photosort.thresholds import FACE_ENGINE_FAILS, PICK_FACES_JOIN
from photosort.undo import undo_run

_RNG_SEED = 20261119


def _unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def _person_face(rng, axis: int, noise: float = 0.03, score: float = 0.9,
                 box=(10.0, 20.0, 50.0, 70.0)) -> Face:
    """A face of the person on axis: that unit vector plus a little noise
    (two such faces are about 0.7 alike; faces of two people about 0)."""
    v = np.zeros(512)
    v[axis] = 1.0
    v += rng.normal(0.0, noise, 512)
    return Face(box, np.zeros((5, 2), np.float32), score, _unit(v), (800, 600))


def _found(rel: str, index: int, face: Face) -> FoundFace:
    return FoundFace(rel, index, face)


def _photos_of(groups: list[PersonGroup]) -> list[set[str]]:
    return [set(g.photos) for g in groups]


# --- grouping ---------------------------------------------------------------


def test_group_people_joins_same_person_and_splits_different():
    rng = np.random.default_rng(_RNG_SEED)
    faces = []
    for n in range(6):  # interleaved, so order alone can't group them
        faces.append(_found(f"a{n}.jpg", 0, _person_face(rng, 0)))
        faces.append(_found(f"b{n}.jpg", 0, _person_face(rng, 1)))
        faces.append(_found(f"c{n}.jpg", 0, _person_face(rng, 2)))
    groups = group_people(faces)
    assert len(groups) == 3
    found = sorted(_photos_of(groups), key=lambda s: sorted(s))
    assert found == [{f"{p}{n}.jpg" for n in range(6)} for p in "abc"]
    assert sorted(g.id for g in groups) == [0, 1, 2]
    assert sorted(len(g.faces) for g in groups) == [6, 6, 6]


def test_group_people_threshold_keeps_unlike_faces_apart():
    # Two people about 0.3 alike (below the join): never one group.
    base = np.zeros(512)
    base[0] = 1.0
    other = np.zeros(512)
    other[0], other[1] = 0.3, np.sqrt(1 - 0.09)
    faces = [_found("x.jpg", 0, Face((0, 0, 9, 9), np.zeros((5, 2), np.float32), 0.9,
                                       _unit(base), (10, 10))),
             _found("y.jpg", 0, Face((0, 0, 9, 9), np.zeros((5, 2), np.float32), 0.9,
                                       _unit(other), (10, 10)))]
    assert PICK_FACES_JOIN == 0.45
    assert len(group_people(faces)) == 2
    assert len(group_people(faces, join=0.25)) == 1


def test_two_faces_in_one_photo_never_same_group():
    rng = np.random.default_rng(_RNG_SEED)
    faces = [_found(f"p{n}.jpg", 0, _person_face(rng, 0)) for n in range(4)]
    # A mirror shot: the same person twice in one photo, almost identical.
    twin = _person_face(rng, 0, noise=0.001)
    faces += [_found("mirror.jpg", 0, twin), _found("mirror.jpg", 1, twin)]
    groups = group_people(faces)
    for g in groups:
        rels = [f.rel for f in g.faces]
        assert len(rels) == len(set(rels)), rels
    big = groups[0]
    assert big.photos == frozenset({"p0.jpg", "p1.jpg", "p2.jpg", "p3.jpg", "mirror.jpg"})
    assert sum(len(g.faces) for g in groups) == 6


def test_groups_sorted_by_photo_count():
    rng = np.random.default_rng(_RNG_SEED)
    faces = [_found(f"c{n}.jpg", 0, _person_face(rng, 2)) for n in range(2)]
    faces += [_found(f"a{n}.jpg", 0, _person_face(rng, 0)) for n in range(5)]
    faces += [_found("z.jpg", 0, _person_face(rng, 5))]
    faces += [_found(f"b{n}.jpg", 0, _person_face(rng, 1)) for n in range(2)]
    faces += [_found("y.jpg", 0, _person_face(rng, 6))]
    groups = group_people(faces)
    assert [len(g.photos) for g in groups] == [5, 2, 2, 1, 1]
    # Equal counts: by the best face's photo.
    assert [g.best.rel[0] for g in groups] == ["a", "b", "c", "y", "z"]
    assert [g.id for g in groups] == [0, 1, 2, 3, 4]


def test_person_group_best_and_photos():
    rng = np.random.default_rng(_RNG_SEED)
    small_sure = _person_face(rng, 0, score=0.95, box=(0.0, 0.0, 10.0, 10.0))
    big_sure = _person_face(rng, 0, score=0.95, box=(0.0, 0.0, 40.0, 40.0))
    less_sure = _person_face(rng, 0, score=0.6, box=(0.0, 0.0, 99.0, 99.0))
    group = PersonGroup(0, (_found("a.jpg", 0, small_sure), _found("b.jpg", 1, big_sure),
                            _found("c.jpg", 0, less_sure)))
    assert group.best.rel == "b.jpg"
    assert group.photos == frozenset({"a.jpg", "b.jpg", "c.jpg"})


def test_group_people_empty():
    assert group_people([]) == []


def test_group_people_memory_bounded():
    rng = np.random.default_rng(_RNG_SEED)
    n = 30_000
    vectors = rng.normal(size=(n, 512)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    landmarks = np.zeros((5, 2), np.float32)
    faces = [FoundFace(f"{i // 2}.jpg", i % 2,
                       Face((0.0, 0.0, 9.0, 9.0), landmarks, 0.9, vectors[i], (10, 10)))
             for i in range(n)]
    del vectors
    tracemalloc.start()
    try:
        started = time.monotonic()
        groups = group_people(faces)
        took = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sum(len(g.faces) for g in groups) == n
    assert peak < 400 * 1024 * 1024, f"peak {peak / 2**20:.0f} MB"
    assert took < 60, f"took {took:.1f} s"


def test_group_people_memory_bounded_clustered():
    # 300 people with 100 faces each: every face has far more than
    # _NEIGHBOURS candidates, so only its top _NEIGHBOURS are kept each
    # round and the merges take several rounds.
    rng = np.random.default_rng(_RNG_SEED)
    people, each = 300, 100
    centres = rng.normal(size=(people, 512)).astype(np.float32)
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    landmarks = np.zeros((5, 2), np.float32)
    faces = []
    for k in range(each):
        noise = rng.normal(0.0, 0.03, size=(people, 512)).astype(np.float32)
        vectors = centres + noise
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        # Photo k has face p of person p: a person's faces are all in different photos.
        faces += [FoundFace(f"{k}.jpg", p,
                            Face((0.0, 0.0, 9.0, 9.0), landmarks, 0.9, vectors[p], (10, 10)))
                  for p in range(people)]
    del centres, noise, vectors
    tracemalloc.start()
    try:
        started = time.monotonic()
        groups = group_people(faces)
        took = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sum(len(g.faces) for g in groups) == people * each
    assert len(groups) == people
    assert {len(g.photos) for g in groups} == {each}
    assert peak < 400 * 1024 * 1024, f"peak {peak / 2**20:.0f} MB"
    assert took < 60, f"took {took:.1f} s"


class _CancelAfter:
    """A cancel event that is set from its checks-th is_set() on."""

    def __init__(self, checks: int) -> None:
        self.checks = checks
        self.asked = 0

    def is_set(self) -> bool:
        self.asked += 1
        return self.asked >= self.checks


def test_group_people_cancel_between_rounds():
    rng = np.random.default_rng(_RNG_SEED)
    faces = [_found(f"{p}{n}.jpg", 0, _person_face(rng, axis))
             for n in range(70) for p, axis in (("a", 0), ("b", 1))]
    assert group_people(faces, cancel=_CancelAfter(1)) is None  # before the first round
    later = _CancelAfter(2)
    assert group_people(faces, cancel=later) is None  # after the first round
    assert later.asked == 2
    unset = threading.Event()
    assert [len(g.photos) for g in group_people(faces, cancel=unset)] == [70, 70]


def test_group_people_more_than_twenty_alike_faces():
    # 70 faces of each person: more candidates per face than are kept.
    rng = np.random.default_rng(_RNG_SEED)
    faces = [_found(f"{p}{n}.jpg", 0, _person_face(rng, axis))
             for n in range(70) for p, axis in (("a", 0), ("b", 1), ("c", 2))]
    groups = group_people(faces)
    assert [len(g.photos) for g in groups] == [70, 70, 70]
    assert sorted(g.best.rel[0] for g in groups) == ["a", "b", "c"]


def test_group_people_takes_the_embedding_size_from_the_faces():
    small = [_found(f"{n}.jpg", 0, Face((0.0, 0.0, 9.0, 9.0), np.zeros((5, 2), np.float32), 0.9,
                                       _unit(np.array([1.0, 0.1 * n, 0.0, 0.0])), (10, 10)))
             for n in range(3)]
    assert [len(g.photos) for g in group_people(small)] == [3]


# --- scanning ---------------------------------------------------------------


class FakeEngine:
    """analyse() for the tests: faces by file name; records the calls."""

    def __init__(self, faces: dict[str, list[Face] | Exception]) -> None:
        self.faces = faces
        self.calls: list[str] = []

    def __call__(self, path: Path) -> list[Face]:
        self.calls.append(path.name)
        found = self.faces.get(path.name, [])
        if isinstance(found, Exception):
            raise found
        return found


def touch(root: Path, *rels: str, data: bytes | None = None) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if data is not None else b"photo bytes of " + rel.encode())


def test_scan_faces_photos_only_and_cached(tmp_path):
    rng = np.random.default_rng(_RNG_SEED)
    one, two, three = (_person_face(rng, i) for i in range(3))
    touch(tmp_path, "a.jpg", "b.jpg", "c.mp4", "sub/d.jpg", "bad.jpg", "none.jpg")
    engine = FakeEngine({"a.jpg": [one, two], "b.jpg": [three], "d.jpg": [one],
                         "bad.jpg": UnreadableImage("no")})
    with AnalysisCache() as cache:  # the central one (a tmp data dir in tests)
        seen = []
        found = scan_faces(tmp_path, False, engine, cache,
                           progress=lambda i, t: seen.append((i, t)))
        assert [(f.rel, f.index) for f in found] == [("a.jpg", 0), ("a.jpg", 1), ("b.jpg", 0)]
        assert found[1].face is two
        assert "c.mp4" not in engine.calls  # videos are skipped
        assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
        assert cache.get(FACES_KIND, tmp_path / "a.jpg") is not None

        engine2 = FakeEngine({})
        again = scan_faces(tmp_path, True, engine2, cache)
        # Only the photos not cached are looked at again (the unreadable one and the new one).
        assert sorted(engine2.calls) == ["bad.jpg", "d.jpg"]
        assert [(f.rel, f.index) for f in again] == [("a.jpg", 0), ("a.jpg", 1), ("b.jpg", 0)]
        assert np.array_equal(again[0].face.embedding, one.embedding)


def test_scan_faces_cancel_and_engine_failure(tmp_path):
    touch(tmp_path, *(f"{n}.jpg" for n in range(FACE_ENGINE_FAILS + 1)))
    cancel = threading.Event()
    cancel.set()
    assert scan_faces(tmp_path, False, FakeEngine({}), None, cancel=cancel) is None
    broken = FakeEngine({f"{n}.jpg": RuntimeError("model broken")
                         for n in range(FACE_ENGINE_FAILS + 1)})
    with pytest.raises(RuntimeError, match="model broken"):
        scan_faces(tmp_path, False, broken, None)


# --- known names ------------------------------------------------------------


def _axis_face(*weights: tuple[int, float]) -> Face:
    """A face whose embedding is the unit vector of the given (axis, weight)s."""
    v = np.zeros(512)
    for axis, weight in weights:
        v[axis] = weight
    return Face((10.0, 20.0, 50.0, 70.0), np.zeros((5, 2), np.float32), 0.9, _unit(v),
                (800, 600))


def _person(name: str, score_to_axis0: float) -> Person:
    """A person whose one sample is score_to_axis0 alike to the axis-0 unit vector."""
    v = np.zeros(512)
    v[0] = score_to_axis0
    v[1] = np.sqrt(1.0 - score_to_axis0 ** 2)
    return Person(name.lower(), name, _unit(v)[None, :], Path(f"{name}.jpg"))


def test_known_names_shows_a_remembered_person():
    groups = [PersonGroup(0, (_found("a.jpg", 0, _axis_face((0, 1.0))),
                              _found("b.jpg", 0, _axis_face((0, 1.0))))),
              PersonGroup(1, (_found("c.jpg", 0, _axis_face((7, 1.0))),))]
    people = [_person("Rahul", 0.5), _person("Other", 0.2)]
    assert known_names(groups, people) == {0: "Rahul"}


def test_known_names_below_sure_is_no_name():
    groups = [PersonGroup(0, (_found("a.jpg", 0, _axis_face((0, 1.0))),))]
    assert known_names(groups, [_person("Rahul", 0.40)]) == {}


def test_known_names_ignores_left_out_faces():
    # Ticked, the faces' mean is ~0.7 alike to Rahul; the axis-0 face left
    # out, only the axis-2 face is left, about 0 alike.
    groups = [PersonGroup(0, (_found("a.jpg", 0, _axis_face((0, 1.0))),
                              _found("b.jpg", 0, _axis_face((2, 1.0)))))]
    people = [_person("Rahul", 1.0)]
    assert known_names(groups, people) == {0: "Rahul"}
    assert known_names(groups, people, {("a.jpg", 0)}) == {}
    # Every face left out: no name either.
    assert known_names(groups, people, {("a.jpg", 0), ("b.jpg", 0)}) == {}


def test_known_names_no_people():
    groups = [PersonGroup(0, (_found("a.jpg", 0, _axis_face((0, 1.0))),))]
    assert known_names(groups, []) == {}


# --- folder names -----------------------------------------------------------


def test_folder_name_refusals(tmp_path):
    (tmp_path / "Notes.txt").write_bytes(b"n")
    (tmp_path / "Family").mkdir()
    assert check_folder_name(tmp_path, "Friend", []) is None
    assert check_folder_name(tmp_path, "family", []) is None  # an existing folder is joined
    assert check_folder_name(tmp_path, "  ", []) == "Type a name."
    assert check_folder_name(tmp_path, "...", []) == "Type a name."
    assert check_folder_name(tmp_path, "A & B", []) == (
        'A name can\'t contain " & ". Photo Sorter uses it for photos with several people.')
    own = "Photo Sorter uses that name for its own folder. Pick another name."
    assert check_folder_name(tmp_path, ".secret", []) == own
    assert check_folder_name(tmp_path, "DUPLICATES", []) == own
    assert check_folder_name(tmp_path, "bad photos", []) == own
    assert check_folder_name(tmp_path, "notes.TXT", []) == "A file named notes.TXT is already there"
    assert check_folder_name(tmp_path, "FRIEND", ["Friend"]) == (
        "There is already a folder called FRIEND in your list")
    assert check_folder_name(tmp_path, "x" * 51, []) == "That name is too long."


# --- planning ---------------------------------------------------------------


def test_photo_folders_in_folder_order():
    friend = PickFolder("Friend", frozenset({("a.jpg", 0), ("b.jpg", 1)}), False)
    family = PickFolder("Family", frozenset({("b.jpg", 0), ("c.jpg", 0)}), True)
    assert photo_folders([friend, family]) == {
        "a.jpg": ["Friend"], "b.jpg": ["Friend", "Family"], "c.jpg": ["Family"]}


def test_plan_pick_single_and_conflict_choices(tmp_path):
    touch(tmp_path, "a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "sub/f.jpg", "Friend/g.jpg")
    touch(tmp_path, "Friend/a.jpg")  # a name clash in the target
    friend = PickFolder("Friend", frozenset({("a.jpg", 0), ("b.jpg", 0), ("c.jpg", 0),
                                             ("d.jpg", 0), ("sub/f.jpg", 0),
                                             ("Friend/g.jpg", 0)}), False)
    family = PickFolder("Family", frozenset({("b.jpg", 1), ("c.jpg", 1), ("d.jpg", 1),
                                             ("e.jpg", 0)}), False)
    plan = plan_pick(tmp_path, [friend, family], {"b.jpg": "Family", "c.jpg": None})
    assert plan.mode == "pick_faces"
    assert sorted(plan.moves, key=lambda m: m.src) == [
        Move("a.jpg", "Friend/a (1).jpg"),
        Move("b.jpg", "Family/b.jpg"),
        Move("e.jpg", "Family/e.jpg"),
        Move("sub/f.jpg", "Friend/f.jpg"),
    ]
    assert plan.unchanged == 1  # Friend/g.jpg is already there
    assert plan.summary == ["2 photos → Friend", "2 photos → Family",
                            "2 left where they are", "1 already in place"]


def test_plan_pick_joins_existing_folder_any_case(tmp_path):
    touch(tmp_path, "a.jpg", "fRiEnD/old.jpg")
    friend = PickFolder("Friend", frozenset({("a.jpg", 0)}), False)
    plan = plan_pick(tmp_path, [friend], {})
    assert plan.moves == [Move("a.jpg", "fRiEnD/a.jpg")]
    assert plan.summary == ["1 photos → fRiEnD"]


def test_plan_pick_no_line_for_a_folder_with_no_photos(tmp_path):
    touch(tmp_path, "a.jpg", "Family/b.jpg")
    friend = PickFolder("Friend", frozenset({("a.jpg", 0)}), False)
    family = PickFolder("Family", frozenset({("Family/b.jpg", 0)}), False)
    empty = PickFolder("Nobody", frozenset(), False)
    plan = plan_pick(tmp_path, [friend, family, empty], {})
    assert plan.summary == ["1 photos → Friend", "1 already in place"]


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in root.rglob("*") if p.is_file() and ".photosort" not in p.parts}


def test_pick_undo_restores(tmp_path):
    for n, rel in enumerate(("a.jpg", "b.jpg", "sub/c.jpg", "Friend/a.jpg")):
        touch(tmp_path, rel, data=bytes([n]) * (100 + n))
    before = _hashes(tmp_path)
    friend = PickFolder("Friend", frozenset({("a.jpg", 0), ("b.jpg", 0)}), False)
    family = PickFolder("Family", frozenset({("b.jpg", 1), ("sub/c.jpg", 0)}), False)
    plan = plan_pick(tmp_path, [friend, family], {"b.jpg": "Family"})
    journal = RunJournal.create(tmp_path, plan.mode, True, len(plan.moves))
    result = execute(tmp_path, plan, journal)
    assert result.moved == 3 and not result.skipped
    after = _hashes(tmp_path)
    assert after["Friend/a (1).jpg"] == before["a.jpg"]
    assert after["Family/b.jpg"] == before["b.jpg"]
    assert after["Family/c.jpg"] == before["sub/c.jpg"]
    assert [r.run_id for r in load_runs(tmp_path)] == [journal.run_id]  # central
    undone = undo_run(tmp_path, journal.run_id)
    assert undone.failed == []
    assert _hashes(tmp_path) == before
    assert not (tmp_path / "Family").exists()
    assert not (tmp_path / ".photosort").exists()  # nothing written into the folder
