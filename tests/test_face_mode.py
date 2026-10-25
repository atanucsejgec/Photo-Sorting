import threading
from pathlib import Path

import numpy as np
import pytest

from photosort.cache import AnalysisCache
from photosort.faces import FACES_KIND, Face
from photosort.imageinfo import UnreadableImage
from photosort.modes import MODE_SPECS, MODES
from photosort.modes.face_mode import (
    Question,
    confidence_percent,
    find_face_matches,
    plan_faces,
    together_folder,
)
from photosort.naming import safe_name
from photosort.people import Person, person_scores
from photosort.plan import Move
from photosort.thresholds import FACE_FOLDER_MAX

# Each person's one sample is a basis vector; a face's similarity to them is
# exactly its weight on that axis (dot product with a unit basis vector).
ATANU = Person("a" * 32, "Atanu", np.eye(512, dtype=np.float32)[[0]], Path("a.jpg"))
ZOE = Person("b" * 32, "Zoe", np.eye(512, dtype=np.float32)[[1]], Path("b.jpg"))
LOWER = Person("c" * 32, "atanu", np.eye(512, dtype=np.float32)[[0]], Path("c.jpg"))
STRANGER_AXIS = 2
_SPARE = 511  # takes up the rest of each face's length


def face(sims: dict[int, float], box=(10.0, 20.0, 50.0, 70.0), size=(800, 600)) -> Face:
    """A face whose similarity to the person on axis d is sims[d]."""
    v = np.zeros(512, np.float64)
    for axis, s in sims.items():
        v[axis] = s
    v[_SPARE] = np.sqrt(max(0.0, 1.0 - float(np.sum(v**2))))
    return Face(box, np.zeros((5, 2), np.float32), 0.9, v.astype(np.float32), size)


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


def touch(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really a photo: " + rel.encode())


def sort(root, faces, people=(ATANU, ZOE), answers=None, subfolders=False):
    scan = find_face_matches(root, subfolders, list(people), FakeEngine(faces))
    return scan, plan_faces(root, scan, list(people), answers or {})


def test_single_person_sure_goes_to_name_folder(tmp_path):
    touch(tmp_path, "x.jpg")
    scan, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9})]})
    assert scan.sure == {"x.jpg": {ATANU.id}}
    assert scan.questions == []
    assert (scan.photos, scan.unreadable) == (1, 0)
    assert plan.mode == "face"
    assert plan.moves == [Move("x.jpg", "Atanu/x.jpg")]
    assert plan.summary == ["1 photos → 1 person folders, 0 together folders",
                            "0 photos stay where they are"]
    assert plan.unchanged == 0


def test_two_people_go_to_together_folder_sorted(tmp_path):
    zoe = Person("d" * 32, "Zoe", ZOE.embeddings, ZOE.thumb)
    atanu = Person("e" * 32, "atanu", ATANU.embeddings, ATANU.thumb)
    touch(tmp_path, "x.jpg")
    _, plan = sort(tmp_path, {"x.jpg": [face({1: 0.8}), face({0: 0.7})]}, people=(zoe, atanu))
    assert plan.moves == [Move("x.jpg", "atanu & Zoe/x.jpg")]
    assert plan.summary[0] == "1 photos → 0 person folders, 1 together folders"


def test_together_folder_sorts_case_insensitively():
    assert together_folder(["Zoe", "atanu", "Bob"]) == "atanu & Bob & Zoe"
    assert together_folder(["Zoe"]) == "Zoe"


def test_together_folder_capped():
    names = [f"{letter}{'x' * 18}" for letter in "LKJIHGFEDCBA"]  # 12 names of 19
    folder = together_folder(names)
    sorted_names = sorted(names)
    assert folder == " & ".join(sorted_names[:4]) + " & 8 more"
    assert len(folder) <= FACE_FOLDER_MAX
    assert safe_name(folder) == folder


def test_long_name_cut_short():
    long = "A" * 98 + ". " + "b" * 50  # the cut at 100 leaves a trailing ". "
    assert len(long) == 150
    assert together_folder([long]) == "A" * 98
    with_zoe = together_folder([long, "Zoe"])  # even one name and "& 1 more" is too long
    assert with_zoe == "A" * 91 + " & 1 more"
    assert len(with_zoe) == FACE_FOLDER_MAX


def test_stranger_ignored(tmp_path):
    touch(tmp_path, "x.jpg")
    scan, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9}), face({STRANGER_AXIS: 0.95}),
                                           face({1: 0.2})]})
    assert scan.sure == {"x.jpg": {ATANU.id}}
    assert scan.questions == []
    assert plan.moves == [Move("x.jpg", "Atanu/x.jpg")]


def test_no_named_people_stays(tmp_path):
    touch(tmp_path, "x.jpg", "y.jpg")
    scan, plan = sort(tmp_path, {"x.jpg": [face({STRANGER_AXIS: 0.9})], "y.jpg": []})
    assert scan.sure == {}
    assert scan.questions == []
    assert plan.moves == []
    assert plan.summary == ["0 photos → 0 person folders, 0 together folders",
                            "2 photos stay where they are"]


def test_unsure_becomes_question_with_percent(tmp_path):
    touch(tmp_path, "x.jpg")
    unsure = face({1: 0.375}, box=(1.0, 2.0, 3.0, 4.0), size=(640, 480))
    scan, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9}), unsure]})
    assert scan.sure == {"x.jpg": {ATANU.id}}
    [q] = scan.questions
    assert q == Question("x.jpg", ZOE.id, "Zoe", (1.0, 2.0, 3.0, 4.0), (640, 480),
                         pytest.approx(0.375), 60)
    assert plan.moves == []  # unanswered: stays put for now


def test_confidence_percent():
    assert confidence_percent(0.30) == 30
    assert confidence_percent(0.375) == 60
    assert confidence_percent(0.45) == 90
    assert confidence_percent(0.0) == 1
    assert confidence_percent(1.0) == 99


def test_questions_ordered_by_rel_then_name(tmp_path):
    bob = Person("f" * 32, "bob", np.eye(512, dtype=np.float32)[[3]], Path("f.jpg"))
    touch(tmp_path, "b.jpg", "a.jpg")
    scan, _ = sort(tmp_path, {"b.jpg": [face({1: 0.4}), face({3: 0.35})],
                              "a.jpg": [face({0: 0.32})]}, people=(ZOE, ATANU, bob))
    assert [(q.rel, q.name) for q in scan.questions] == [
        ("a.jpg", "Atanu"), ("b.jpg", "bob"), ("b.jpg", "Zoe")]
    assert scan.sure == {}


def test_answers_yes_no_skip(tmp_path):
    touch(tmp_path, "yes.jpg", "no.jpg", "skip.jpg", "open.jpg", "both.jpg")
    faces = {
        "yes.jpg": [face({1: 0.4})],
        "no.jpg": [face({1: 0.4})],
        "skip.jpg": [face({0: 0.9}), face({1: 0.4})],
        "open.jpg": [face({0: 0.9}), face({1: 0.4})],
        "both.jpg": [face({0: 0.9}), face({1: 0.4})],
    }
    answers = {("yes.jpg", ZOE.id): "yes", ("no.jpg", ZOE.id): "no",
               ("skip.jpg", ZOE.id): "skip", ("both.jpg", ZOE.id): "yes"}
    scan, plan = sort(tmp_path, faces, answers=answers)
    assert len(scan.questions) == 5
    assert plan.moves == [Move("both.jpg", "Atanu & Zoe/both.jpg"),
                          Move("yes.jpg", "Zoe/yes.jpg")]
    assert plan.summary == ["2 photos → 1 person folders, 1 together folders",
                            "3 photos stay where they are"]


def test_no_keeps_sure_persons(tmp_path):
    touch(tmp_path, "x.jpg")
    _, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9}), face({1: 0.4})]},
                   answers={("x.jpg", ZOE.id): "no"})
    assert plan.moves == [Move("x.jpg", "Atanu/x.jpg")]


def test_one_face_one_person(tmp_path):
    touch(tmp_path, "pair.jpg", "twice.jpg")
    faces = {
        # Both faces are most like Atanu; the lower one goes to Zoe instead.
        "pair.jpg": [face({0: 0.8, 1: 0.6}), face({0: 0.9})],
        # Atanu is taken by the sure face: the unsure one is not asked about.
        "twice.jpg": [face({0: 0.9}), face({0: 0.4})],
    }
    scan, plan = sort(tmp_path, faces)
    assert scan.sure == {"pair.jpg": {ATANU.id, ZOE.id}, "twice.jpg": {ATANU.id}}
    assert scan.questions == []
    assert plan.moves == [Move("pair.jpg", "Atanu & Zoe/pair.jpg"),
                          Move("twice.jpg", "Atanu/twice.jpg")]


def test_one_person_one_face(tmp_path):
    # One face like both people: it counts for the more similar one only.
    touch(tmp_path, "x.jpg")
    scan, _ = sort(tmp_path, {"x.jpg": [face({0: 0.6, 1: 0.7})]})
    assert scan.sure == {"x.jpg": {ZOE.id}}
    assert scan.questions == []


def test_person_scores_take_best_sample():
    two = Person("9" * 32, "Two", np.eye(512, dtype=np.float32)[[0, 1]], Path("t.jpg"))
    scores = person_scores(face({0: 0.3, 1: 0.6}).embedding, [two, ATANU])
    assert scores == {two.id: pytest.approx(0.6), ATANU.id: pytest.approx(0.3)}


def test_videos_skipped(tmp_path):
    touch(tmp_path, "clip.mp4", "x.jpg")
    engine = FakeEngine({"x.jpg": [face({0: 0.9})], "clip.mp4": [face({0: 0.9})]})
    scan = find_face_matches(tmp_path, False, [ATANU], engine)
    assert engine.calls == ["x.jpg"]
    assert scan.photos == 1
    assert scan.sure == {"x.jpg": {ATANU.id}}


def test_unreadable_counted(tmp_path):
    touch(tmp_path, "bad.jpg", "x.jpg")
    scan, plan = sort(tmp_path, {"bad.jpg": UnreadableImage("bad"),
                                 "x.jpg": [face({0: 0.9})]})
    assert (scan.photos, scan.unreadable) == (2, 1)
    assert plan.moves == [Move("x.jpg", "Atanu/x.jpg")]
    assert plan.summary == ["1 photos → 1 person folders, 0 together folders",
                            "0 photos stay where they are",
                            "1 photos couldn't be read"]


def test_already_in_person_folder_unchanged(tmp_path):
    touch(tmp_path, "Atanu/x.jpg", "atanu & zoe/y.jpg", "Old/Atanu/z.jpg")
    faces = {"x.jpg": [face({0: 0.9})], "y.jpg": [face({0: 0.9}), face({1: 0.9})],
             "z.jpg": [face({0: 0.9})]}
    _, plan = sort(tmp_path, faces, subfolders=True)
    # Only a photo directly inside its folder (at the root) is in place.
    assert plan.moves == [Move("Old/Atanu/z.jpg", "Atanu/z.jpg")]
    assert plan.unchanged == 2
    assert plan.summary == ["1 photos → 1 person folders, 0 together folders",
                            "2 photos stay where they are"]


def test_move_clashes_get_numbered(tmp_path):
    touch(tmp_path, "Atanu/x.jpg", "x.jpg")
    _, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9})]})  # Atanu/x.jpg not scanned
    assert plan.moves == [Move("x.jpg", "Atanu/x (1).jpg")]


def test_existing_folder_case_reused(tmp_path):
    (tmp_path / "atanu").mkdir()
    touch(tmp_path, "x.jpg")
    _, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9})]})
    assert plan.moves == [Move("x.jpg", "atanu/x.jpg")]


def test_unknown_person_ignored_when_planning(tmp_path):
    touch(tmp_path, "x.jpg")
    scan = find_face_matches(tmp_path, False, [ATANU, ZOE],
                             FakeEngine({"x.jpg": [face({0: 0.9}), face({1: 0.9})]}))
    plan = plan_faces(tmp_path, scan, [ZOE], {})  # Atanu forgotten meanwhile
    assert plan.moves == [Move("x.jpg", "Zoe/x.jpg")]


@pytest.mark.parametrize("name", ["Duplicates", "repeated shots", "BAD PHOTOS",
                                  ".photosort", ".hidden"])
def test_names_of_skipped_folders_never_planned(tmp_path, name):
    # PeopleStore refuses such names; a Person made some other way is dropped.
    odd = Person("d" * 32, name, ZOE.embeddings, ZOE.thumb)
    touch(tmp_path, "both.jpg", "alone.jpg")
    _, plan = sort(tmp_path, {"both.jpg": [face({0: 0.9}), face({1: 0.9})],
                              "alone.jpg": [face({1: 0.9})]}, people=(ATANU, odd))
    assert plan.moves == [Move("both.jpg", "Atanu/both.jpg")]
    assert plan.summary[1] == "1 photos stay where they are"


def test_cancel_returns_none(tmp_path):
    touch(tmp_path, "a.jpg", "b.jpg")
    cancel = threading.Event()
    cancel.set()
    engine = FakeEngine({})
    assert find_face_matches(tmp_path, False, [ATANU], engine, cancel=cancel) is None
    assert engine.calls == []

    cancel = threading.Event()
    engine = FakeEngine({})
    result = find_face_matches(tmp_path, False, [ATANU], engine,
                               progress=lambda i, total: cancel.set(), cancel=cancel)
    assert result is None
    assert engine.calls == ["a.jpg"]


def test_progress_called_per_photo(tmp_path):
    touch(tmp_path, "a.jpg", "b.png", "clip.mp4", "notes.txt")
    calls = []
    find_face_matches(tmp_path, False, [ATANU], FakeEngine({}),
                      progress=lambda i, total: calls.append((i, total)))
    assert calls == [(1, 2), (2, 2)]


def test_cache_used_on_second_scan(tmp_path):
    touch(tmp_path, "x.jpg", "y.jpg", "bad.jpg")
    faces = {"x.jpg": [face({0: 0.9})], "y.jpg": [face({1: 0.375})],
             "bad.jpg": UnreadableImage("bad")}
    with AnalysisCache(tmp_path) as cache:
        first = find_face_matches(tmp_path, False, [ATANU, ZOE], FakeEngine(faces),
                                  cache=cache)
    engine = FakeEngine(faces)
    with AnalysisCache(tmp_path) as cache:
        second = find_face_matches(tmp_path, False, [ATANU, ZOE], engine, cache=cache)
    assert engine.calls == ["bad.jpg"]  # unreadable photos are not cached
    assert second.sure == first.sure == {"x.jpg": {ATANU.id}}
    assert second.questions == first.questions
    assert second.unreadable == 1


def test_unusable_cache_entry_analysed_again(tmp_path):
    touch(tmp_path, "x.jpg")
    engine = FakeEngine({"x.jpg": [face({0: 0.9})]})
    with AnalysisCache(tmp_path) as cache:
        cache.put(FACES_KIND, tmp_path / "x.jpg", b"garbage")
        scan = find_face_matches(tmp_path, False, [ATANU], engine, cache=cache)
        assert cache.get(FACES_KIND, tmp_path / "x.jpg") != b"garbage"  # replaced
    assert engine.calls == ["x.jpg"]
    assert scan.sure == {"x.jpg": {ATANU.id}}


def test_face_mode_registered_but_not_yet_offered():
    assert ("face", "Face", False) in MODES
    assert MODE_SPECS["face"].kind == "face"

