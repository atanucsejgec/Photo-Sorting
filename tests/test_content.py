import logging
import math
import threading
from pathlib import Path

import numpy as np
import pytest

from photosort.cache import AnalysisCache
from photosort.clip import CLIP_KIND, pack, unpack
from photosort.imageinfo import UnreadableImage
from photosort.modes._clip_scan import ClipScan, embed_photos
from photosort.modes.content import Verdict, background_prompts, judge, plan_content, prompts
from photosort.plan import Move
from photosort.settings import DEFAULT_CONTENT_CATEGORIES
from photosort.thresholds import (
    CLIP_LOGIT_SCALE,
    CONTENT_BACKGROUND_PROMPTS,
    CONTENT_SURE,
    CONTENT_UNSURE,
)

# Each prompt's text embedding is a basis vector, so a photo's logit for the
# prompt on axis d is exactly CLIP_LOGIT_SCALE times its weight on that axis.
CATEGORIES = list(DEFAULT_CONTENT_CATEGORIES)  # Food on axis 0, Pets & Animals on 1, ...
TEXT = np.eye(512, dtype=np.float32)[:len(CATEGORIES)]
BG_AXES = [500, 501]  # the background prompts ("a photo", "a photo of something else")
BACKGROUND = np.eye(512, dtype=np.float32)[BG_AXES]
_SPARE = 511  # takes up the rest of each photo's length


def photo(logits: dict[int, float]) -> np.ndarray:
    """A unit embedding whose logit for the category on axis d is logits[d] (others 0)."""
    v = np.zeros(512, np.float64)
    for axis, logit in logits.items():
        v[axis] = logit / CLIP_LOGIT_SCALE
    v[_SPARE] = np.sqrt(1.0 - float(np.sum(v**2)))
    return v.astype(np.float32)


def food_at(p: float) -> np.ndarray:
    """A photo whose Food probability is p, every other prompt's logit 0."""
    others = len(CATEGORIES) - 1 + len(BACKGROUND)
    return photo({0: math.log(p * others / (1 - p))})


def clip_scan(embeddings: dict[str, np.ndarray], unreadable: int = 0) -> ClipScan:
    return ClipScan(embeddings, len(embeddings) + unreadable, unreadable)


class FakeEmbed:
    """embed() for the tests: an embedding by file name (or an error); records the calls."""

    def __init__(self, found: dict[str, np.ndarray | Exception] | None = None) -> None:
        self.found = found or {}
        self.calls: list[str] = []

    def __call__(self, path: Path) -> np.ndarray:
        self.calls.append(path.name)
        found = self.found.get(path.name, photo({}))
        if isinstance(found, Exception):
            raise found
        return found


def touch(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really a photo: " + rel.encode())


# --- judge --------------------------------------------------------------------

def test_background_prompts():
    assert background_prompts() == ["a photo", "a photo of something else"]
    assert CONTENT_BACKGROUND_PROMPTS == ("a photo", "a photo of something else")


def test_prompts():
    assert prompts(["Food", "Pets & Animals"]) == ["a photo of food",
                                                   "a photo of pets & animals"]


def test_judge_sure_from_content_sure():
    scan = clip_scan({
        "aligned.jpg": photo({0: 50.0}),
        "sure.jpg": food_at(CONTENT_SURE + 1e-4),
        "almost.jpg": food_at(CONTENT_SURE - 1e-4),
    })
    sure, unsure = judge(scan, CATEGORIES, TEXT, BACKGROUND)
    assert sure == {"aligned.jpg": "Food", "sure.jpg": "Food"}
    assert [v.rel for v in unsure] == ["almost.jpg"]
    assert unsure[0].best == "Food"
    assert unsure[0].prob == pytest.approx(CONTENT_SURE - 1e-4, abs=1e-6)


def test_judge_photo_between_two_categories_is_unsure():
    # Food's logit is ln(1.5) above Pets & Animals': 60 % against 40 %.
    scan = clip_scan({"cat_eating.jpg": photo({0: 50.0, 1: 50.0 - math.log(1.5)})})
    sure, unsure = judge(scan, CATEGORIES, TEXT, BACKGROUND)
    assert sure == {}
    [verdict] = unsure
    assert isinstance(verdict, Verdict)
    assert (verdict.rel, verdict.best) == ("cat_eating.jpg", "Food")
    assert verdict.prob == pytest.approx(0.6, abs=1e-6)
    # All categories, best first; the ones tied at nothing keep their order.
    assert [name for name, _ in verdict.ranked] == CATEGORIES
    assert [p for _, p in verdict.ranked[:2]] == pytest.approx([0.6, 0.4], abs=1e-6)
    assert sum(p for _, p in verdict.ranked) == pytest.approx(1.0)


def test_judge_ranks_best_first():
    scan = clip_scan({"car.jpg": photo({5: 50.0, 2: 49.0})})  # Vehicles, then Nature
    _, [verdict] = judge(scan, CATEGORIES, TEXT, BACKGROUND)
    assert verdict.best == "Vehicles"
    assert [name for name, _ in verdict.ranked[:3]] == ["Vehicles", "Nature & Landscapes",
                                                        "Food"]
    probs = [p for _, p in verdict.ranked]
    assert probs == sorted(probs, reverse=True)


def test_judge_photo_below_content_unsure_goes_nowhere():
    scan = clip_scan({
        "nothing.jpg": photo({}),  # orthogonal to every prompt: 1/10 each
        "faint.jpg": food_at(CONTENT_UNSURE - 1e-4),
        "maybe.jpg": food_at(CONTENT_UNSURE + 1e-4),
    })
    sure, unsure = judge(scan, CATEGORIES, TEXT, BACKGROUND)
    assert sure == {}
    assert [v.rel for v in unsure] == ["maybe.jpg"]


def test_judge_sorts_the_unsure_by_rel():
    maybe = food_at(0.6)
    scan = clip_scan({"b.jpg": maybe, "a/z.jpg": maybe, "a.jpg": maybe})
    _, unsure = judge(scan, CATEGORIES, TEXT, BACKGROUND)
    assert [v.rel for v in unsure] == ["a.jpg", "a/z.jpg", "b.jpg"]


def test_judge_of_no_photos():
    assert judge(clip_scan({}), CATEGORIES, TEXT, BACKGROUND) == ({}, [])


@pytest.mark.parametrize("categories, text", [(CATEGORIES[:3], TEXT), ([], TEXT[:0])])
def test_judge_refuses_categories_not_matching_the_text(categories, text):
    with pytest.raises(ValueError):
        judge(clip_scan({"a.jpg": photo({})}), categories, text, BACKGROUND)


def test_judge_one_category_still_lets_photos_stay():
    scan = clip_scan({"food.jpg": photo({0: 50.0}), "other.jpg": photo({BG_AXES[0]: 50.0})})
    sure, unsure = judge(scan, ["Food"], TEXT[:1], BACKGROUND)
    assert sure == {"food.jpg": "Food"}
    assert unsure == []


def test_judge_two_categories_still_let_photos_stay():
    scan = clip_scan({"food.jpg": photo({0: 50.0}),
                      "other.jpg": photo({BG_AXES[1]: 50.0}),
                      "nothing.jpg": photo({})})  # 1/4 each: Food 25 %
    sure, unsure = judge(scan, CATEGORIES[:2], TEXT[:2], BACKGROUND)
    assert sure == {"food.jpg": "Food"}
    assert unsure == []


def test_judge_background_beating_every_category_leaves_the_photo_put():
    # Food 46 % (above CONTENT_UNSURE), but "a photo" 54 %.
    scan = clip_scan({"a.jpg": photo({0: 50.0, BG_AXES[0]: 50.0 + math.log(0.54 / 0.46)})})
    assert judge(scan, CATEGORIES, TEXT, BACKGROUND) == ({}, [])


def test_judge_probabilities_are_over_categories_and_background():
    # Food and "a photo of something else" tie: Food is 50 %, unsure.
    scan = clip_scan({"a.jpg": photo({0: 50.0, BG_AXES[1]: 50.0})})
    _, [verdict] = judge(scan, CATEGORIES, TEXT, BACKGROUND)
    assert verdict.prob == pytest.approx(0.5, abs=1e-6)
    assert isinstance(verdict.ranked, tuple)
    assert [name for name, _ in verdict.ranked] == CATEGORIES  # categories only
    assert verdict.ranked[0][1] == pytest.approx(0.5, abs=1e-6)


# --- embed_photos -------------------------------------------------------------

def test_embed_photos_skips_videos(tmp_path):
    touch(tmp_path, "a.jpg", "clip.mp4", "b.png", "notes.txt")
    embed = FakeEmbed({"a.jpg": photo({0: 50.0})})
    seen = []
    scan = embed_photos(tmp_path, False, embed, progress=lambda i, n: seen.append((i, n)))
    assert embed.calls == ["a.jpg", "b.png"]
    assert sorted(scan.embeddings) == ["a.jpg", "b.png"]
    np.testing.assert_array_equal(scan.embeddings["a.jpg"], photo({0: 50.0}))
    assert (scan.photos, scan.unreadable) == (2, 0)
    assert seen == [(1, 2), (2, 2)]


def test_embed_photos_looks_into_subfolders_only_when_asked(tmp_path):
    touch(tmp_path, "a.jpg", "Food/b.jpg")
    assert sorted(embed_photos(tmp_path, False, FakeEmbed()).embeddings) == ["a.jpg"]
    assert sorted(embed_photos(tmp_path, True, FakeEmbed()).embeddings) == ["Food/b.jpg",
                                                                            "a.jpg"]


def test_embed_photos_counts_unreadable_and_never_caches_it(tmp_path):
    touch(tmp_path, "a.jpg", "bad.jpg")
    embed = FakeEmbed({"bad.jpg": UnreadableImage("bad.jpg: broken")})
    with AnalysisCache(tmp_path) as cache:
        scan = embed_photos(tmp_path, False, embed, cache=cache)
        assert sorted(scan.embeddings) == ["a.jpg"]
        assert (scan.photos, scan.unreadable) == (2, 1)
        assert cache.get(CLIP_KIND, tmp_path / "bad.jpg") is None
        assert cache.get(CLIP_KIND, tmp_path / "a.jpg") is not None


def test_embed_photos_second_run_is_all_cache_hits(tmp_path):
    touch(tmp_path, "a.jpg", "b.jpg", "bad.jpg")
    found = {"a.jpg": photo({0: 50.0}), "b.jpg": photo({1: 50.0}),
             "bad.jpg": UnreadableImage("bad.jpg: broken")}
    with AnalysisCache(tmp_path) as cache:
        first = embed_photos(tmp_path, False, FakeEmbed(found), cache=cache)
    again = FakeEmbed(found)
    with AnalysisCache(tmp_path) as cache:
        second = embed_photos(tmp_path, False, again, cache=cache)
    assert again.calls == ["bad.jpg"]  # an unreadable photo is tried again
    assert sorted(second.embeddings) == sorted(first.embeddings) == ["a.jpg", "b.jpg"]
    for rel in first.embeddings:
        np.testing.assert_array_equal(second.embeddings[rel], first.embeddings[rel])
    assert (second.photos, second.unreadable) == (3, 1)


def test_embed_photos_garbage_cache_entry_is_embedded_again(tmp_path):
    touch(tmp_path, "a.jpg")
    embed = FakeEmbed({"a.jpg": photo({0: 50.0})})
    with AnalysisCache(tmp_path) as cache:
        cache.put(CLIP_KIND, tmp_path / "a.jpg", b"not an embedding")
        scan = embed_photos(tmp_path, False, embed, cache=cache)
        assert embed.calls == ["a.jpg"]
        np.testing.assert_array_equal(scan.embeddings["a.jpg"], photo({0: 50.0}))
        np.testing.assert_array_equal(unpack(cache.get(CLIP_KIND, tmp_path / "a.jpg")),
                                      photo({0: 50.0}))


def test_embed_photos_cancelled_gives_none(tmp_path):
    touch(tmp_path, "a.jpg", "b.jpg", "c.jpg")
    cancel = threading.Event()
    cancel.set()
    embed = FakeEmbed()
    assert embed_photos(tmp_path, False, embed, cancel=cancel) is None
    assert embed.calls == []

    cancel = threading.Event()
    embed = FakeEmbed()
    assert embed_photos(tmp_path, False, embed, progress=lambda i, n: cancel.set(),
                        cancel=cancel) is None
    assert embed.calls == ["a.jpg"]

    cancel = threading.Event()  # cancelled after the last photo
    assert embed_photos(tmp_path, False, FakeEmbed(),
                        progress=lambda i, n: i == n and cancel.set(), cancel=cancel) is None


def test_embed_photos_raises_when_the_first_five_photos_all_fail(tmp_path):
    names = [f"{c}.jpg" for c in "abcdef"]
    touch(tmp_path, *names)
    embed = FakeEmbed({name: RuntimeError("model broken") for name in names})
    with pytest.raises(RuntimeError, match="model broken"):
        embed_photos(tmp_path, False, embed)
    assert embed.calls == names[:5]


def test_embed_photos_unreadable_photos_do_not_hide_a_broken_model(tmp_path):
    names = [f"{c}.jpg" for c in "abcdef"]
    touch(tmp_path, *names)
    found = {name: RuntimeError("model broken") for name in names}
    found["a.jpg"] = UnreadableImage("a.jpg: broken")
    with pytest.raises(RuntimeError, match="model broken"):
        embed_photos(tmp_path, False, FakeEmbed(found))


def test_embed_photos_counts_a_failing_photo_unreadable_once_one_worked(tmp_path, caplog):
    names = [f"{c}.jpg" for c in "abcdefg"]
    touch(tmp_path, *names)
    found = {name: RuntimeError("odd photo") for name in names[1:]}
    with caplog.at_level(logging.WARNING, logger="photosort"):
        scan = embed_photos(tmp_path, False, FakeEmbed(found))
    assert sorted(scan.embeddings) == ["a.jpg"]
    assert (scan.photos, scan.unreadable) == (7, 6)
    assert sum("b.jpg" in r.getMessage() for r in caplog.records) == 1


def test_embed_photos_four_fails_then_success_is_fine(tmp_path):
    names = [f"{c}.jpg" for c in "abcde"]
    touch(tmp_path, *names)
    found = {name: RuntimeError("odd photo") for name in names[:4]}
    scan = embed_photos(tmp_path, False, FakeEmbed(found))
    assert sorted(scan.embeddings) == ["e.jpg"]
    assert scan.unreadable == 4


# --- plan_content -------------------------------------------------------------

def test_plan_moves_the_sure_photos_to_their_folders(tmp_path):
    touch(tmp_path, "a.jpg", "b.jpg", "c.jpg")
    scan = clip_scan({rel: photo({}) for rel in ("a.jpg", "b.jpg", "c.jpg")})
    plan = plan_content(tmp_path, scan, {"b.jpg": "Pets & Animals", "a.jpg": "Food",
                                         "c.jpg": "Food"}, {})
    assert plan.mode == "content"
    assert plan.moves == [Move("a.jpg", "Food/a.jpg"), Move("b.jpg", "Pets & Animals/b.jpg"),
                          Move("c.jpg", "Food/c.jpg")]
    assert plan.summary == ["3 photos → 2 category folders"]
    assert plan.unchanged == 0


def test_plan_follows_the_answers(tmp_path):
    touch(tmp_path, "a.jpg", "c.jpg", "d.jpg", "e.jpg")
    scan = clip_scan({rel: photo({}) for rel in ("a.jpg", "c.jpg", "d.jpg", "e.jpg")})
    # c and d were unsure and answered; e was unsure and is not answered yet.
    plan = plan_content(tmp_path, scan, {"a.jpg": "Vehicles"},
                        {"c.jpg": "Food", "d.jpg": None})
    assert plan.moves == [Move("a.jpg", "Vehicles/a.jpg"), Move("c.jpg", "Food/c.jpg")]
    assert plan.summary == ["2 photos → 2 category folders",
                            "2 photos stay where they are"]


def test_plan_leaves_a_photo_already_in_its_folder(tmp_path):
    touch(tmp_path, "Food/a.jpg", "trip/b.jpg", "trip/Food/c.jpg")
    scan = clip_scan({rel: photo({}) for rel in ("Food/a.jpg", "trip/b.jpg",
                                                 "trip/Food/c.jpg")})
    sure = {"Food/a.jpg": "Food", "trip/b.jpg": "Food", "trip/Food/c.jpg": "Food"}
    plan = plan_content(tmp_path, scan, sure, {})
    # The folder is at the root: one deeper down is not it.
    assert plan.moves == [Move("trip/Food/c.jpg", "Food/c.jpg"),
                          Move("trip/b.jpg", "Food/b.jpg")]
    assert plan.unchanged == 1
    assert plan.summary == ["2 photos → 1 category folders", "1 already in place"]


def test_plan_uses_the_folder_on_disk_in_another_case(tmp_path):
    touch(tmp_path, "food/a.jpg", "b.jpg", "a.jpg")
    scan = clip_scan({rel: photo({}) for rel in ("a.jpg", "b.jpg", "food/a.jpg")})
    plan = plan_content(tmp_path, scan, {"a.jpg": "Food", "b.jpg": "Food",
                                         "food/a.jpg": "Food"}, {})
    assert plan.moves == [Move("a.jpg", "food/a (1).jpg"), Move("b.jpg", "food/b.jpg")]
    assert plan.unchanged == 1


@pytest.mark.parametrize("category, folder", [
    ("Bad Photos", "_Bad Photos"),  # the scanner skips "Bad Photos"
    (".hidden", "_.hidden"),
    ("a/b", "a_b"),
    ("x" * 200, "x" * 100),
])
def test_plan_folder_is_a_valid_scanned_folder(tmp_path, category, folder):
    touch(tmp_path, "a.jpg")
    plan = plan_content(tmp_path, clip_scan({"a.jpg": photo({})}), {"a.jpg": category}, {})
    assert plan.moves == [Move("a.jpg", f"{folder}/a.jpg")]


def test_plan_summary_lines(tmp_path):
    rels = ("a.jpg", "b.jpg", "Food/c.jpg", "d.jpg")
    touch(tmp_path, *rels)
    scan = clip_scan({rel: photo({}) for rel in rels}, unreadable=1)
    plan = plan_content(tmp_path, scan, {"a.jpg": "Food", "Food/c.jpg": "Food"},
                        {"b.jpg": "Food"})
    assert plan.summary == ["2 photos → 1 category folders",
                            "1 photos stay where they are",
                            "1 photos couldn't be read",
                            "1 already in place"]


def test_plan_of_nothing_sure(tmp_path):
    touch(tmp_path, "a.jpg")
    plan = plan_content(tmp_path, clip_scan({"a.jpg": photo({})}), {}, {})
    assert plan.moves == []
    assert plan.summary == ["0 photos → 0 category folders", "1 photos stay where they are"]


def test_changed_categories_rejudge_without_reading_photos(tmp_path):
    """Review Focus 1: the owner renames Food to Meals and adds Documents."""
    touch(tmp_path, "a.jpg", "Food/b.jpg", "doc.jpg")
    found = {"a.jpg": photo({0: 50.0}), "b.jpg": photo({0: 50.0}),
             "doc.jpg": photo({8: 50.0})}  # on no axis of the first categories
    with AnalysisCache(tmp_path) as cache:
        scan = embed_photos(tmp_path, True, FakeEmbed(found), cache=cache)
    sure, _ = judge(scan, CATEGORIES, TEXT, BACKGROUND)
    plan = plan_content(tmp_path, scan, sure, {})
    assert plan.moves == [Move("a.jpg", "Food/a.jpg")]
    assert plan.unchanged == 1  # Food/b.jpg

    renamed = ["Meals"] + CATEGORIES[1:] + ["Documents"]  # Meals on axis 0, Documents on 8
    text = np.eye(512, dtype=np.float32)[:len(renamed)]
    again = FakeEmbed(found)
    with AnalysisCache(tmp_path) as cache:
        rerun = embed_photos(tmp_path, True, again, cache=cache)
    assert again.calls == []
    for each in (scan, rerun):
        sure, unsure = judge(each, renamed, text, BACKGROUND)
        assert unsure == []
        plan = plan_content(tmp_path, each, sure, {})
        assert plan.moves == [Move("Food/b.jpg", "Meals/b.jpg"), Move("a.jpg", "Meals/a.jpg"),
                              Move("doc.jpg", "Documents/doc.jpg")]
        assert plan.summary == ["3 photos → 2 category folders"]


def test_cache_holds_packed_embeddings(tmp_path):
    touch(tmp_path, "a.jpg")
    with AnalysisCache(tmp_path) as cache:
        embed_photos(tmp_path, False, FakeEmbed({"a.jpg": photo({3: 50.0})}), cache=cache)
        assert cache.get(CLIP_KIND, tmp_path / "a.jpg") == pack(photo({3: 50.0}))
