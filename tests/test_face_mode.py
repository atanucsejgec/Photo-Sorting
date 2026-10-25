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
from photosort.thresholds import FACE_FOLDER_MAX, FACE_FOLDER_MAX_BYTES

# Each person's one sample is a basis vector; a face's similarity to them is
# exactly its weight on that axis (dot product with a unit basis vector).
ATANU = Person("a" * 32, "Atanu", np.eye(512, dtype=np.float32)[[0]], Path("a.jpg"))
ZOE = Person("b" * 32, "Zoe", np.eye(512, dtype=np.float32)[[1]], Path("b.jpg"))
LOWER = Person("c" * 32, "atanu", np.eye(512, dtype=np.float32)[[0]], Path("c.jpg"))
BOB = Person("f" * 32, "bob", np.eye(512, dtype=np.float32)[[3]], Path("f.jpg"))
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
    """Sort root for people, the ones ticked: with two or more, group photos only."""
    scan = find_face_matches(root, subfolders, list(people), FakeEngine(faces))
    return scan, plan_faces(root, scan, list(people), answers or {})


def test_single_person_sure_goes_to_name_folder(tmp_path):
    touch(tmp_path, "x.jpg")
    scan, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9})]}, people=(ATANU,))
    assert scan.sure == {"x.jpg": {ATANU.id}}
    assert scan.questions == []
    assert (scan.photos, scan.unreadable) == (1, 0)
    assert not scan.groups_only
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


def _usable_folder(tmp_path, folder):
    assert len(folder) <= FACE_FOLDER_MAX
    assert len(folder.encode()) <= FACE_FOLDER_MAX_BYTES
    assert safe_name(folder) == folder
    (tmp_path / folder).mkdir()  # a Linux file system takes it


def test_together_folder_fits_in_bytes(tmp_path):
    # 95 characters but 261 bytes in UTF-8: too long for a folder name on ext4.
    names = ["দেবাশীষ কুমার বন্দ্যোপাধ্যায়", "প্রিয়াঙ্কা রানী চট্টোপাধ্যায়",
             "অনির্বাণ চন্দ্র মুখোপাধ্যায়ের"]
    folder = together_folder(names)
    first, second = sorted(names)[:2]
    assert folder == f"{first} & {second} & 1 more"
    _usable_folder(tmp_path, folder)


def test_long_name_cut_by_bytes(tmp_path):
    folder = together_folder(["অ" * 100])
    assert folder == "অ" * (FACE_FOLDER_MAX_BYTES // 3)
    _usable_folder(tmp_path, folder)


def test_cut_never_leaves_a_dangling_vowel_sign(tmp_path):
    # "কা" is KA + the vowel sign AA (a combining mark): cut between them, the
    # vowel sign goes too, rather than ending the name on it.
    name = "ক" * 65 + "া" + "ক" * 10  # 198 bytes fit: the cut falls just after the sign
    folder = together_folder([name])
    assert folder == "ক" * 65
    _usable_folder(tmp_path, folder)


@pytest.mark.parametrize("name, cut", [
    ("Duplicates" + " " * 95 + "x" * 20, "_Duplicates"),  # would be skipped by scans
    ("CON" + " " * 100 + "y", "_CON"),  # reserved on Windows
])
def test_cut_name_still_a_valid_scanned_folder(tmp_path, name, cut):
    folder = together_folder([name])
    assert folder == cut
    _usable_folder(tmp_path, folder)


def test_stranger_ignored(tmp_path):
    touch(tmp_path, "x.jpg")
    scan, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9}), face({STRANGER_AXIS: 0.95}),
                                           face({1: 0.2})]}, people=(ATANU,))
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
    touch(tmp_path, "b.jpg", "a.jpg")
    scan, _ = sort(tmp_path, {"b.jpg": [face({1: 0.4}), face({3: 0.35})],
                              "a.jpg": [face({0: 0.32}), face({3: 0.4})]},
                   people=(ZOE, ATANU, BOB))
    assert [(q.rel, q.name) for q in scan.questions] == [
        ("a.jpg", "Atanu"), ("a.jpg", "bob"), ("b.jpg", "bob"), ("b.jpg", "Zoe")]
    assert scan.sure == {}


def test_answers_yes_no_skip(tmp_path):
    touch(tmp_path, "yes.jpg", "no.jpg", "skip.jpg", "open.jpg")
    faces = {name: [face({1: 0.4})] for name in ("yes.jpg", "no.jpg", "skip.jpg", "open.jpg")}
    answers = {("yes.jpg", ZOE.id): "yes", ("no.jpg", ZOE.id): "no",
               ("skip.jpg", ZOE.id): "skip"}
    scan, plan = sort(tmp_path, faces, people=(ZOE,), answers=answers)
    assert len(scan.questions) == 4
    assert plan.moves == [Move("yes.jpg", "Zoe/yes.jpg")]
    assert plan.summary == ["1 photos → 1 person folders, 0 together folders",
                            "3 photos stay where they are"]


def test_one_ticked_sorts_every_photo_of_them(tmp_path):
    # One person ticked: as before, any photo with them goes to their folder,
    # with strangers or not, and a lone unsure match is asked about.
    touch(tmp_path, "alone.jpg", "with_zoe.jpg", "unsure.jpg")
    faces = {"alone.jpg": [face({0: 0.9})],
             "with_zoe.jpg": [face({0: 0.9}), face({1: 0.9})],  # Zoe not ticked
             "unsure.jpg": [face({0: 0.4})]}
    scan, plan = sort(tmp_path, faces, people=(ATANU,),
                      answers={("unsure.jpg", ATANU.id): "yes"})
    assert not scan.groups_only
    assert [(q.rel, q.name) for q in scan.questions] == [("unsure.jpg", "Atanu")]
    assert plan.moves == [Move("alone.jpg", "Atanu/alone.jpg"),
                          Move("unsure.jpg", "Atanu/unsure.jpg"),
                          Move("with_zoe.jpg", "Atanu/with_zoe.jpg")]


def test_two_ticked_sorts_only_their_group_photos(tmp_path):
    touch(tmp_path, "atanu.jpg", "zoe.jpg", "both.jpg", "crowd.jpg", "nobody.jpg")
    faces = {"atanu.jpg": [face({0: 0.9})],
             "zoe.jpg": [face({1: 0.9}), face({STRANGER_AXIS: 0.9})],
             "both.jpg": [face({0: 0.9}), face({1: 0.9})],
             "crowd.jpg": [face({0: 0.9}), face({STRANGER_AXIS: 0.9}), face({1: 0.9})],
             "nobody.jpg": [face({STRANGER_AXIS: 0.9})]}
    scan, plan = sort(tmp_path, faces, people=(ATANU, ZOE))
    assert scan.groups_only
    assert plan.moves == [Move("both.jpg", "Atanu & Zoe/both.jpg"),
                          Move("crowd.jpg", "Atanu & Zoe/crowd.jpg")]
    assert plan.summary == ["2 photos → 0 person folders, 1 together folders",
                            "3 photos stay where they are"]


def test_three_ticked_sorts_photos_of_any_two_of_them(tmp_path):
    touch(tmp_path, "a_b.jpg", "a_z.jpg", "all.jpg", "a.jpg", "b_stranger.jpg")
    faces = {"a_b.jpg": [face({0: 0.9}), face({3: 0.9})],
             "a_z.jpg": [face({0: 0.9}), face({1: 0.9})],
             "all.jpg": [face({0: 0.9}), face({1: 0.9}), face({3: 0.9})],
             "a.jpg": [face({0: 0.9})],
             "b_stranger.jpg": [face({3: 0.9}), face({STRANGER_AXIS: 0.9})]}
    _, plan = sort(tmp_path, faces, people=(ATANU, ZOE, BOB))
    assert plan.moves == [Move("a_b.jpg", "Atanu & bob/a_b.jpg"),
                          Move("a_z.jpg", "Atanu & Zoe/a_z.jpg"),
                          Move("all.jpg", "Atanu & bob & Zoe/all.jpg")]
    assert plan.summary == ["3 photos → 0 person folders, 3 together folders",
                            "2 photos stay where they are"]


def test_groups_only_asks_only_what_can_make_a_group_photo(tmp_path):
    touch(tmp_path, "sure_alone.jpg", "unsure_alone.jpg", "sure_and_unsure.jpg",
          "two_unsure.jpg", "unsure_with_stranger.jpg")
    faces = {"sure_alone.jpg": [face({0: 0.9})],
             "unsure_alone.jpg": [face({1: 0.4})],
             "sure_and_unsure.jpg": [face({0: 0.9}), face({1: 0.4})],
             "two_unsure.jpg": [face({0: 0.4}), face({1: 0.35})],
             "unsure_with_stranger.jpg": [face({0: 0.4}), face({STRANGER_AXIS: 0.9})]}
    scan, plan = sort(tmp_path, faces, people=(ATANU, ZOE))
    assert [(q.rel, q.name) for q in scan.questions] == [
        ("sure_and_unsure.jpg", "Zoe"),
        ("two_unsure.jpg", "Atanu"), ("two_unsure.jpg", "Zoe")]
    assert scan.sure == {"sure_alone.jpg": {ATANU.id}, "sure_and_unsure.jpg": {ATANU.id}}
    assert plan.moves == []  # nothing answered yet


@pytest.mark.parametrize("answer, moves", [
    ("yes", [Move("x.jpg", "Atanu & Zoe/x.jpg")]),
    ("no", []),
    ("skip", []),
])
def test_groups_only_answer_decides_the_group_photo(tmp_path, answer, moves):
    touch(tmp_path, "x.jpg")
    _, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9}), face({1: 0.4})]},
                   people=(ATANU, ZOE), answers={("x.jpg", ZOE.id): answer})
    assert plan.moves == moves
    assert plan.summary[1] == f"{1 - len(moves)} photos stay where they are"


def test_groups_only_needs_two_after_the_answers(tmp_path):
    touch(tmp_path, "one_yes.jpg", "both_yes.jpg", "three.jpg")
    faces = {"one_yes.jpg": [face({0: 0.4}), face({1: 0.35})],
             "both_yes.jpg": [face({0: 0.4}), face({1: 0.35})],
             "three.jpg": [face({0: 0.9}), face({1: 0.4}), face({3: 0.4})]}
    answers = {("one_yes.jpg", ATANU.id): "yes", ("one_yes.jpg", ZOE.id): "no",
               ("both_yes.jpg", ATANU.id): "yes", ("both_yes.jpg", ZOE.id): "yes",
               ("three.jpg", ZOE.id): "no", ("three.jpg", BOB.id): "yes"}
    _, plan = sort(tmp_path, faces, people=(ATANU, ZOE, BOB), answers=answers)
    assert plan.moves == [Move("both_yes.jpg", "Atanu & Zoe/both_yes.jpg"),
                          Move("three.jpg", "Atanu & bob/three.jpg")]


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
    assert plan.moves == [Move("pair.jpg", "Atanu & Zoe/pair.jpg")]  # twice.jpg: Atanu alone


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
                                 "x.jpg": [face({0: 0.9})]}, people=(ATANU,))
    assert (scan.photos, scan.unreadable) == (2, 1)
    assert plan.moves == [Move("x.jpg", "Atanu/x.jpg")]
    assert plan.summary == ["1 photos → 1 person folders, 0 together folders",
                            "0 photos stay where they are",
                            "1 photos couldn't be read"]


def test_already_in_person_folder_unchanged(tmp_path):
    touch(tmp_path, "Atanu/x.jpg", "Atanu/y.jpg", "Old/Atanu/z.jpg")
    faces = {"x.jpg": [face({0: 0.9})], "y.jpg": [face({0: 0.9}), face({1: 0.9})],
             "z.jpg": [face({0: 0.9})]}
    _, plan = sort(tmp_path, faces, people=(ATANU,), subfolders=True)
    # Only a photo directly inside its folder (at the root) is in place.
    assert plan.moves == [Move("Old/Atanu/z.jpg", "Atanu/z.jpg")]
    assert plan.unchanged == 2
    assert plan.summary == ["1 photos → 1 person folders, 0 together folders",
                            "0 photos stay where they are",
                            "2 photos are already in their folders"]


def test_groups_only_rerun_moves_group_photos_out_of_person_folders(tmp_path):
    touch(tmp_path, "atanu & zoe/in.jpg", "Atanu/pair.jpg", "Atanu/alone.jpg")
    faces = {"in.jpg": [face({0: 0.9}), face({1: 0.9})],
             "pair.jpg": [face({0: 0.9}), face({1: 0.9})],
             "alone.jpg": [face({0: 0.9})]}
    _, plan = sort(tmp_path, faces, people=(ATANU, ZOE), subfolders=True)
    assert plan.moves == [Move("Atanu/pair.jpg", "atanu & zoe/pair.jpg")]
    assert plan.unchanged == 1
    assert plan.summary == ["1 photos → 0 person folders, 1 together folders",
                            "1 photos stay where they are",
                            "1 photos are already in their folders"]


def test_summary_counts_add_up_to_the_photos(tmp_path):
    touch(tmp_path, "Atanu/in.jpg", "move.jpg", "nobody.jpg", "bad.jpg")
    faces = {"in.jpg": [face({0: 0.9})], "move.jpg": [face({0: 0.9})],
             "bad.jpg": UnreadableImage("bad")}
    _, plan = sort(tmp_path, faces, people=(ATANU,), subfolders=True)
    assert plan.summary == ["1 photos → 1 person folders, 0 together folders",
                            "1 photos stay where they are",
                            "1 photos are already in their folders",
                            "1 photos couldn't be read"]


def test_move_clashes_get_numbered(tmp_path):
    touch(tmp_path, "Atanu/x.jpg", "x.jpg")
    _, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9})]}, people=(ATANU,))  # Atanu/x.jpg not scanned
    assert plan.moves == [Move("x.jpg", "Atanu/x (1).jpg")]


def test_existing_folder_case_reused(tmp_path):
    (tmp_path / "atanu").mkdir()
    touch(tmp_path, "x.jpg")
    _, plan = sort(tmp_path, {"x.jpg": [face({0: 0.9})]}, people=(ATANU,))
    assert plan.moves == [Move("x.jpg", "atanu/x.jpg")]


def test_unknown_person_ignored_when_planning(tmp_path):
    touch(tmp_path, "all.jpg", "pair.jpg")
    faces = {"all.jpg": [face({0: 0.9}), face({1: 0.9}), face({3: 0.9})],
             "pair.jpg": [face({0: 0.9}), face({1: 0.9})]}
    scan = find_face_matches(tmp_path, False, [ATANU, ZOE, BOB], FakeEngine(faces))
    plan = plan_faces(tmp_path, scan, [ZOE, BOB], {})  # Atanu forgotten meanwhile
    # Still group photos only, as the scan was: pair.jpg is now Zoe's alone.
    assert plan.moves == [Move("all.jpg", "bob & Zoe/all.jpg")]


@pytest.mark.parametrize("name", ["Duplicates", "repeated shots", "BAD PHOTOS",
                                  ".photosort", ".hidden"])
def test_names_of_skipped_folders_never_planned(tmp_path, name):
    # PeopleStore refuses such names; a Person made some other way is dropped.
    odd = Person("d" * 32, name, ZOE.embeddings, ZOE.thumb)
    touch(tmp_path, "both.jpg", "alone.jpg")
    _, plan = sort(tmp_path, {"both.jpg": [face({0: 0.9}), face({1: 0.9})],
                              "alone.jpg": [face({1: 0.9})]}, people=(ATANU, odd))
    assert plan.moves == []  # never "Atanu & <name>/"; Atanu alone is no group photo
    assert plan.summary[1] == "2 photos stay where they are"
    _, plan = sort(tmp_path, {"alone.jpg": [face({1: 0.9})]}, people=(odd,))
    assert plan.moves == []


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
    faces = {"x.jpg": [face({0: 0.9})], "y.jpg": [face({0: 0.375})],
             "bad.jpg": UnreadableImage("bad")}
    with AnalysisCache(tmp_path) as cache:
        first = find_face_matches(tmp_path, False, [ATANU], FakeEngine(faces),
                                  cache=cache)
    engine = FakeEngine(faces)
    with AnalysisCache(tmp_path) as cache:
        second = find_face_matches(tmp_path, False, [ATANU], engine, cache=cache)
    assert engine.calls == ["bad.jpg"]  # unreadable photos are not cached
    assert second.sure == first.sure == {"x.jpg": {ATANU.id}}
    assert second.questions == first.questions
    assert len(second.questions) == 1
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


def test_face_mode_registered_and_offered():
    assert ("face", "Face", True) in MODES
    assert MODE_SPECS["face"].kind == "face"



def test_one_photo_failing_is_logged_and_counted_unreadable(tmp_path, caplog):
    touch(tmp_path, "a.jpg", "b.jpg", "c.jpg")
    engine = FakeEngine({"a.jpg": [face({0: 0.9})], "b.jpg": RuntimeError("CUDA out of memory"),
                         "c.jpg": [face({1: 0.9})]})
    with caplog.at_level("WARNING", logger="photosort"):
        scan = find_face_matches(tmp_path, False, [ATANU, ZOE], engine)
    assert (scan.photos, scan.unreadable) == (3, 1)
    assert scan.sure == {"a.jpg": {ATANU.id}, "c.jpg": {ZOE.id}}
    assert any("b.jpg" in r.getMessage() for r in caplog.records)


def test_failed_photo_is_not_cached(tmp_path):
    touch(tmp_path, "a.jpg", "b.jpg")
    faces = {"a.jpg": [face({0: 0.9})], "b.jpg": RuntimeError("model trouble")}
    with AnalysisCache(tmp_path) as cache:
        find_face_matches(tmp_path, False, [ATANU], FakeEngine(faces), cache=cache)
    engine = FakeEngine(faces)
    with AnalysisCache(tmp_path) as cache:
        scan = find_face_matches(tmp_path, False, [ATANU], engine, cache=cache)
    assert engine.calls == ["b.jpg"]
    assert scan.unreadable == 1


def test_first_five_photos_failing_means_the_engine_is_broken(tmp_path):
    names = [f"{n}.jpg" for n in range(7)]
    touch(tmp_path, *names)
    error = RuntimeError("model trouble")
    engine = FakeEngine({name: error for name in names})
    with pytest.raises(RuntimeError, match="model trouble"):
        find_face_matches(tmp_path, False, [ATANU], engine)
    assert len(engine.calls) == 5


def test_unreadable_photos_do_not_count_as_engine_failures(tmp_path):
    names = [f"{n}.jpg" for n in range(7)]
    touch(tmp_path, *names)
    faces = {name: UnreadableImage("bad") for name in names[:3]}
    faces |= {name: RuntimeError("one bad photo") for name in names[3:7]}
    scan = find_face_matches(tmp_path, False, [ATANU], FakeEngine(faces))
    assert scan.unreadable == 7  # only 4 engine failures: not given up


def test_failures_after_a_success_never_stop_the_scan(tmp_path):
    names = [f"{n}.jpg" for n in range(8)]
    touch(tmp_path, *names)
    faces = {name: RuntimeError("model trouble") for name in names[1:]}
    faces["0.jpg"] = [face({0: 0.9})]
    scan = find_face_matches(tmp_path, False, [ATANU], FakeEngine(faces))
    assert (scan.photos, scan.unreadable) == (8, 7)
    assert scan.sure == {"0.jpg": {ATANU.id}}


def test_folder_spelt_in_another_unicode_form_is_reused(tmp_path):
    joy = Person("d" * 32, "\u099c\u09af\u09bc", np.eye(512, dtype=np.float32)[[0]],
                 Path("d.jpg"))  # জয়, as the store keeps it (NFC)
    touch(tmp_path, "\u099c\u09df/x.jpg", "y.jpg")  # জয়/, spelt another way on disk
    faces = {"x.jpg": [face({0: 0.9})], "y.jpg": [face({0: 0.9})]}
    _, plan = sort(tmp_path, faces, people=[joy], subfolders=True)
    assert plan.moves == [Move("y.jpg", "\u099c\u09df/y.jpg")]
    assert plan.unchanged == 1
