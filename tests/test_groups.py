import time
from datetime import datetime

import numpy as np
import pytest

from photosort.groups import (
    Candidate, Group, best_copy, close_pairs, plan_set_aside, sharpest, sharpness_label,
)


def _cand(rel, width=800, height=600, size=1000, taken=None, sharpness=100.0, kind="photo"):
    return Candidate(rel, kind, width, height, size, taken, sharpness)


def test_close_pairs_small():
    assert close_pairs([0b0, 0b1, 0b111111111], 1) == [(0, 1)]


def test_close_pairs_full_width_and_empty():
    top = 2**64 - 1
    assert close_pairs([top, top ^ 0b11, 0], 2) == [(0, 1)]
    assert close_pairs([], 6) == []
    assert close_pairs([5], 6) == []


def test_close_pairs_fast():
    rng = np.random.default_rng(7)
    hashes = [int(v) for v in rng.integers(0, 2**64, 3000, dtype=np.uint64)]
    planted = []
    for n, src in enumerate((3, 500, 1200, 2222, 2999)):
        flipped = hashes[src] ^ (1 << n) ^ (1 << (40 + n))  # 2 bits away
        planted.append((src, len(hashes)))
        hashes.append(flipped)
    start = time.perf_counter()
    pairs = close_pairs(hashes, 6)
    elapsed = time.perf_counter() - start
    assert set(planted) <= set(pairs)
    assert all(i < j for i, j in pairs)
    assert elapsed < 2


def test_best_copy_ranking():
    big = _cand("z/big.jpg", 4080, 3060, size=10)
    small = _cand("a/small.jpg", 1600, 1200, size=10_000, taken=datetime(2025, 1, 1))
    assert best_copy([small, big]) == big

    dated = _cand("z.jpg", taken=datetime(2025, 1, 1), size=10)
    undated = _cand("a.jpg", size=10_000)
    assert best_copy([undated, dated]) == dated

    larger = _cand("z.jpg", size=2000, sharpness=1.0)
    smaller = _cand("a.jpg", size=1000, sharpness=500.0)
    assert best_copy([smaller, larger]) == larger

    sharper = _cand("z.jpg", sharpness=200.0)
    softer = _cand("a.jpg", sharpness=100.0)
    assert best_copy([softer, sharper]) == sharper

    first = _cand("a.jpg")
    second = _cand("b.jpg")
    assert best_copy([second, first]) == first


def test_best_copy_handles_videos():
    one = _cand("b.mp4", None, None, sharpness=None, kind="video")
    two = _cand("a.mp4", None, None, sharpness=None, kind="video")
    assert best_copy([one, two]) == two


def test_sharpest_and_labels():
    best = _cand("a.jpg", sharpness=100.0)
    fine = _cand("b.jpg", sharpness=90.0)
    slight = _cand("c.jpg", sharpness=70.0)
    blurry = _cand("d.jpg", sharpness=30.0)
    unknown = _cand("e.jpg", sharpness=None)
    group = Group("repeated", [fine, best, slight, blurry, unknown], {"a.jpg"})
    assert sharpest(group.members) == best
    assert [sharpness_label(c, group) for c in (best, fine, slight, blurry, unknown)] == [
        "best", "sharp", "slightly blurry", "blurry", "",
    ]


def _write(root, rel):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return rel


def test_plan_set_aside_moves_unkept(tmp_path):
    g0 = Group("exact", [_cand("a/x.jpg"), _cand("b/x2.jpg"), _cand("c/x3.jpg")], {"a/x.jpg"})
    g1 = Group("resized", [_cand("p.jpg"), _cand("q.jpg")], {"p.jpg"})
    plan = plan_set_aside(tmp_path, "duplicates", "Duplicates", [g0, g1],
                          {0: {"a/x.jpg"}})
    assert plan.mode == "duplicates"
    assert {(m.src, m.dst) for m in plan.moves} == {
        ("b/x2.jpg", "Duplicates/x2.jpg"),
        ("c/x3.jpg", "Duplicates/x3.jpg"),
    }
    assert plan.summary == [
        "2 files → Duplicates/ (from 1 groups)",
        "Nothing is deleted. You can look through Duplicates/ later.",
    ]


def test_plan_set_aside_keep_all_moves_nothing(tmp_path):
    g0 = Group("exact", [_cand("a.jpg"), _cand("b.jpg")], {"a.jpg"})
    plan = plan_set_aside(tmp_path, "duplicates", "Duplicates", [g0], {0: {"a.jpg", "b.jpg"}})
    assert plan.moves == []
    assert plan.summary[0] == "0 files → Duplicates/ (from 0 groups)"


def test_plan_set_aside_clash(tmp_path):
    g0 = Group("exact", [_cand("a/IMG_1.jpg"), _cand("b/IMG_1.jpg"), _cand("c/IMG_1.jpg")],
               {"a/IMG_1.jpg"})
    plan = plan_set_aside(tmp_path, "duplicates", "Duplicates", [g0], {0: {"a/IMG_1.jpg"}})
    assert [(m.src, m.dst) for m in plan.moves] == [
        ("b/IMG_1.jpg", "Duplicates/IMG_1.jpg"),
        ("c/IMG_1.jpg", "Duplicates/IMG_1 (1).jpg"),
    ]


def test_plan_set_aside_uses_existing_folder_and_names(tmp_path):
    _write(tmp_path, "duplicates/IMG_1.jpg")
    g0 = Group("exact", [_cand("a/IMG_1.jpg"), _cand("b/IMG_1.jpg")], {"a/IMG_1.jpg"})
    plan = plan_set_aside(tmp_path, "duplicates", "Duplicates", [g0], {0: {"a/IMG_1.jpg"}})
    assert [m.dst for m in plan.moves] == ["duplicates/IMG_1 (1).jpg"]


def test_plan_set_aside_refuses_empty_keep(tmp_path):
    g0 = Group("exact", [_cand("a.jpg"), _cand("b.jpg")], {"a.jpg"})
    with pytest.raises(ValueError):
        plan_set_aside(tmp_path, "duplicates", "Duplicates", [g0], {0: set()})


def test_plan_set_aside_refuses_keep_outside_group(tmp_path):
    # Keeping only names that aren't in the group would set aside every copy.
    g0 = Group("exact", [_cand("a.jpg"), _cand("b.jpg")], {"a.jpg"})
    with pytest.raises(ValueError):
        plan_set_aside(tmp_path, "duplicates", "Duplicates", [g0], {0: {"other.jpg"}})
