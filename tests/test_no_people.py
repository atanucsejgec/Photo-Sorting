"""No people: photos where no face is found move into a "No people" folder."""

import threading
from pathlib import Path

import numpy as np
import pytest

from photosort.cache import AnalysisCache
from photosort.faces import FACES_KIND, Face, pack_faces
from photosort.imageinfo import UnreadableImage
from photosort.modes import MODE_SPECS, MODES
from photosort.modes.no_people import NO_PEOPLE, find_no_people
from photosort.plan import Move
from photosort.thresholds import FACE_ENGINE_FAILS


def _face() -> Face:
    v = np.zeros(512, np.float32)
    v[0] = 1.0
    return Face((10.0, 20.0, 50.0, 70.0), np.zeros((5, 2), np.float32), 0.9, v, (16, 16))


class FakeEngine:
    """analyse() for the tests: faces by file name (none if not named); records the calls."""

    def __init__(self, faces: dict[str, list[Face] | Exception] | None = None) -> None:
        self.faces = faces or {}
        self.calls: list[str] = []

    def __call__(self, path: Path) -> list[Face]:
        self.calls.append(path.name)
        found = self.faces.get(path.name, [])
        if isinstance(found, Exception):
            raise found
        return list(found)


def _dsts(plan) -> dict[str, str]:
    return {m.src: m.dst for m in plan.moves}


def test_the_folder_name():
    assert NO_PEOPLE == "No people"


def test_the_mode_is_listed_after_type_and_before_extension():
    keys = [key for key, _, _ in MODES]
    assert keys[keys.index("no_people") + 1] == "extension"
    assert ("no_people", "No people", True) in MODES
    spec = MODE_SPECS["no_people"]
    assert spec.kind == "nopeople"
    assert spec.warn_subfolders is False
    assert spec.asks_date_source is False


def test_a_photo_without_faces_moves(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "beach.jpg")
    plan = find_no_people(tmp_path, False, FakeEngine())
    assert plan.mode == "no_people"
    assert plan.moves == [Move("beach.jpg", "No people/beach.jpg")]
    assert plan.summary == ["1 photos without faces → No people", "0 photos with people stay"]
    assert plan.unchanged == 0


def test_a_photo_with_one_face_stays(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "me.jpg")
    make_jpeg(tmp_path / "sea.jpg")
    plan = find_no_people(tmp_path, False, FakeEngine({"me.jpg": [_face()]}))
    assert _dsts(plan) == {"sea.jpg": "No people/sea.jpg"}
    assert plan.summary == ["1 photos without faces → No people", "1 photos with people stay"]


def test_an_unreadable_photo_stays_and_is_counted(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "broken.jpg")
    plan = find_no_people(tmp_path, False,
                          FakeEngine({"broken.jpg": UnreadableImage("broken")}))
    assert _dsts(plan) == {"a.jpg": "No people/a.jpg"}
    assert plan.summary == ["1 photos without faces → No people", "0 photos with people stay",
                            "1 photos could not be read (they stay)"]


def test_one_photo_failing_after_others_worked_stays(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    engine = FakeEngine({"b.jpg": RuntimeError("one bad photo")})
    plan = find_no_people(tmp_path, False, engine)
    assert _dsts(plan) == {"a.jpg": "No people/a.jpg"}
    assert "1 photos could not be read (they stay)" in plan.summary


def test_videos_and_other_files_are_untouched(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    (tmp_path / "clip.mp4").write_bytes(b"not really a video")
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    engine = FakeEngine()
    plan = find_no_people(tmp_path, False, engine)
    assert engine.calls == ["a.jpg"]
    assert _dsts(plan) == {"a.jpg": "No people/a.jpg"}


def test_subfolders_are_looked_in_only_when_asked(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "trip" / "a.jpg")
    assert _dsts(find_no_people(tmp_path, False, FakeEngine())) == {
        "a.jpg": "No people/a.jpg"}
    assert _dsts(find_no_people(tmp_path, True, FakeEngine())) == {
        "a.jpg": "No people/a.jpg", "trip/a.jpg": "No people/a (1).jpg"}


def test_a_name_already_in_the_folder_is_never_overwritten(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    (tmp_path / "No people").mkdir()
    (tmp_path / "No people" / "a.JPG").write_bytes(b"something else")
    plan = find_no_people(tmp_path, False, FakeEngine())
    assert _dsts(plan) == {"a.jpg": "No people/a (1).jpg"}


@pytest.mark.parametrize("folder, target", [("No people", "No people"),
                                            ("no PEOPLE", "no PEOPLE"),
                                            ("trip/No People", "No people")])
def test_photos_already_in_a_no_people_folder_are_in_place(tmp_path, make_jpeg, folder,
                                                           target):
    make_jpeg(tmp_path / folder / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    engine = FakeEngine()
    plan = find_no_people(tmp_path, True, engine)
    assert _dsts(plan) == {"b.jpg": f"{target}/b.jpg"}
    assert engine.calls == ["b.jpg"]  # not looked at again
    assert plan.unchanged == 1
    assert plan.summary == ["1 photos without faces → No people", "0 photos with people stay",
                            "1 already in place"]


def test_an_existing_folder_in_another_case_is_used(tmp_path, make_jpeg):
    (tmp_path / "no people").mkdir()
    make_jpeg(tmp_path / "a.jpg")
    plan = find_no_people(tmp_path, False, FakeEngine())
    assert _dsts(plan) == {"a.jpg": "no people/a.jpg"}


def test_the_summary_lines_are_exact(tmp_path, make_jpeg):
    for name in ("p1.jpg", "p2.jpg", "n1.jpg", "n2.jpg", "n3.jpg", "bad.jpg"):
        make_jpeg(tmp_path / name)
    make_jpeg(tmp_path / "No people" / "old.jpg")
    engine = FakeEngine({"p1.jpg": [_face()], "p2.jpg": [_face(), _face()],
                         "bad.jpg": UnreadableImage("bad")})
    plan = find_no_people(tmp_path, True, engine)
    assert plan.summary == [
        "3 photos without faces → No people",
        "2 photos with people stay",
        "1 photos could not be read (they stay)",
        "1 already in place",
    ]


def test_a_cached_faces_entry_is_used_without_looking_again(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "me.jpg")
    make_jpeg(tmp_path / "sea.jpg")
    with AnalysisCache(tmp_path) as cache:
        cache.put(FACES_KIND, tmp_path / "me.jpg", pack_faces([_face()]))
        cache.put(FACES_KIND, tmp_path / "sea.jpg", pack_faces([]))
        engine = FakeEngine({"sea.jpg": [_face()]})  # would say otherwise if asked
        plan = find_no_people(tmp_path, False, engine, cache=cache)
    assert engine.calls == []
    assert _dsts(plan) == {"sea.jpg": "No people/sea.jpg"}


def test_what_is_found_is_cached_for_face_mode(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "me.jpg")
    make_jpeg(tmp_path / "sea.jpg")
    faces = {"me.jpg": [_face()]}
    with AnalysisCache(tmp_path) as cache:
        find_no_people(tmp_path, False, FakeEngine(faces), cache=cache)
    engine = FakeEngine(faces)
    with AnalysisCache(tmp_path) as cache:
        plan = find_no_people(tmp_path, False, engine, cache=cache)
        assert cache.get(FACES_KIND, tmp_path / "me.jpg") == pack_faces([_face()])
    assert engine.calls == []
    assert _dsts(plan) == {"sea.jpg": "No people/sea.jpg"}


def test_an_unreadable_photo_is_not_cached(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "bad.jpg")
    faces = {"bad.jpg": UnreadableImage("bad")}
    with AnalysisCache(tmp_path) as cache:
        find_no_people(tmp_path, False, FakeEngine(faces), cache=cache)
    engine = FakeEngine(faces)
    with AnalysisCache(tmp_path) as cache:
        find_no_people(tmp_path, False, engine, cache=cache)
    assert engine.calls == ["bad.jpg"]


def test_a_broken_face_engine_is_an_error_never_no_faces(tmp_path, make_jpeg):
    # Review Focus 4
    names = [f"{n}.jpg" for n in range(FACE_ENGINE_FAILS + 2)]
    for name in names:
        make_jpeg(tmp_path / name)
    engine = FakeEngine({name: RuntimeError("model trouble") for name in names})
    with pytest.raises(RuntimeError, match="model trouble"):
        find_no_people(tmp_path, False, engine)
    assert len(engine.calls) == FACE_ENGINE_FAILS


def test_progress_is_reported_for_each_photo(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    make_jpeg(tmp_path / "No people" / "c.jpg")
    ticks = []
    find_no_people(tmp_path, True, FakeEngine(), progress=lambda i, n: ticks.append((i, n)))
    assert ticks == [(1, 3), (2, 3), (3, 3)]


def test_cancel_gives_none(tmp_path, make_jpeg):
    make_jpeg(tmp_path / "a.jpg")
    make_jpeg(tmp_path / "b.jpg")
    cancel = threading.Event()

    def analyse(path):
        cancel.set()
        return []

    assert find_no_people(tmp_path, False, analyse, cancel=cancel) is None
