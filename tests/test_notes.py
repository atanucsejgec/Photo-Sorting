import math

import numpy as np
import pytest

from photosort.modes._clip_scan import ClipScan
from photosort.modes.content import Verdict
from photosort.modes.notes import KINDS, OTHER_PROMPTS, PAPER_PROMPTS, judge, plan_notes
from photosort.plan import Move
from photosort.thresholds import CLIP_LOGIT_SCALE, NOTES_SURE, NOTES_UNSURE

# Each prompt's text embedding is a basis vector, so a photo's logit for the
# prompt on axis d is exactly CLIP_LOGIT_SCALE times its weight on that axis.
# The paper prompts are on axes 0-3 (in KINDS' order), the others on 4-13.
PAPER = np.eye(512, dtype=np.float32)[0:4]
OTHER = np.eye(512, dtype=np.float32)[4:14]
HANDWRITTEN, BOOK, PRINTED, RECEIPT = 0, 1, 2, 3
PEOPLE, PLACE = 4, 5
_SPARE = 511  # takes up the rest of each photo's length


def photo(logits: dict[int, float]) -> np.ndarray:
    """A unit embedding whose logit for the prompt on axis d is logits[d] (others 0)."""
    v = np.zeros(512, np.float64)
    for axis, logit in logits.items():
        v[axis] = logit / CLIP_LOGIT_SCALE
    v[_SPARE] = np.sqrt(1.0 - float(np.sum(v**2)))
    return v.astype(np.float32)


def receipt_at(paper: float) -> np.ndarray:
    """A photo whose paper probability is paper, Receipts' logit its only one above 0.

    With e^L for Receipts and 1 for each other prompt, paper is
    (e^L + 3) / (e^L + 13).
    """
    others = len(PAPER) + len(OTHER) - 1
    other_paper = len(PAPER) - 1
    return photo({RECEIPT: math.log((paper * others - other_paper) / (1 - paper))})


def clip_scan(embeddings: dict[str, np.ndarray], unreadable: int = 0) -> ClipScan:
    return ClipScan(embeddings, len(embeddings) + unreadable, unreadable)


def touch(root, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not really a photo: " + rel.encode())


# --- the prompts ----------------------------------------------------------------

def test_kinds_and_prompts():
    assert KINDS == ["Handwritten", "Book Pages", "Printed Documents", "Receipts"]
    assert PAPER_PROMPTS == ["a photo of handwritten notes", "a photo of a page from a book",
                             "a photo of a printed document", "a photo of a shop receipt"]
    assert OTHER_PROMPTS == ["a photo of people", "a photo of a place", "a photo of food",
                             "a photo of an animal", "a photo of an object",
                             "a screenshot of a phone screen", "a photo of a sign",
                             "a photo of a bank card", "a photo of a computer screen",
                             "a photo of an old printed photograph"]


def test_paper_prompts_are_tuned_in_thresholds():
    from photosort import thresholds

    assert thresholds.NOTES_PAPER_PROMPTS == tuple(PAPER_PROMPTS)
    assert isinstance(PAPER_PROMPTS, list)


# --- judge ----------------------------------------------------------------------

def test_judge_receipt_is_sure():
    scan = clip_scan({"shop.jpg": photo({RECEIPT: 50.0})})
    assert judge(scan, PAPER, OTHER) == ({"shop.jpg": "Receipts"}, [])


def test_judge_sure_from_notes_sure():
    scan = clip_scan({
        "sure.jpg": receipt_at(NOTES_SURE + 1e-4),
        "almost.jpg": receipt_at(NOTES_SURE - 1e-4),
    })
    sure, unsure = judge(scan, PAPER, OTHER)
    assert sure == {"sure.jpg": "Receipts"}
    assert [v.rel for v in unsure] == ["almost.jpg"]
    assert unsure[0].prob == pytest.approx(NOTES_SURE - 1e-4, abs=1e-6)


def test_judge_below_notes_unsure_stays_put():
    scan = clip_scan({
        "faint.jpg": receipt_at(NOTES_UNSURE - 1e-4),
        "maybe.jpg": receipt_at(NOTES_UNSURE + 1e-4),
        "nothing.jpg": photo({}),  # orthogonal to every prompt: paper 4/14, 29 %
    })
    sure, unsure = judge(scan, PAPER, OTHER)
    assert sure == {}
    assert [v.rel for v in unsure] == ["maybe.jpg"]


def test_judge_printed_document_with_people_is_unsure():
    # Printed document's logit is ln(1.5) above people's: paper 60 %, people 40 %.
    scan = clip_scan({"meeting.jpg": photo({PRINTED: 50.0, PEOPLE: 50.0 - math.log(1.5)})})
    sure, unsure = judge(scan, PAPER, OTHER)
    assert sure == {}
    [verdict] = unsure
    assert isinstance(verdict, Verdict)
    assert (verdict.rel, verdict.best) == ("meeting.jpg", "Printed Documents")
    assert verdict.prob == pytest.approx(0.6, abs=1e-6)
    assert isinstance(verdict.ranked, tuple)
    # The four kinds, best first; the ones tied at nothing keep their order.
    assert [name for name, _ in verdict.ranked] == ["Printed Documents", "Handwritten",
                                                    "Book Pages", "Receipts"]
    assert verdict.ranked[0][1] == pytest.approx(0.6, abs=1e-6)


def test_judge_paper_is_the_sum_of_the_kinds():
    # Handwritten 45 %, book page 40 %, people 15 %: paper 85 %, sure, Handwritten.
    scan = clip_scan({"diary.jpg": photo({HANDWRITTEN: 50.0 + math.log(0.45 / 0.15),
                                          BOOK: 50.0 + math.log(0.40 / 0.15),
                                          PEOPLE: 50.0})})
    assert judge(scan, PAPER, OTHER) == ({"diary.jpg": "Handwritten"}, [])

    # Book page 35 %, receipt 30 %, a place 35 %: paper 65 %, unsure, Book Pages.
    scan = clip_scan({"desk.jpg": photo({BOOK: 50.0 + math.log(0.35 / 0.35),
                                         RECEIPT: 50.0 + math.log(0.30 / 0.35),
                                         PLACE: 50.0})})
    _, [verdict] = judge(scan, PAPER, OTHER)
    assert verdict.best == "Book Pages"
    assert verdict.prob == pytest.approx(0.65, abs=1e-6)
    assert [name for name, _ in verdict.ranked] == ["Book Pages", "Receipts", "Handwritten",
                                                    "Printed Documents"]
    assert [p for _, p in verdict.ranked[:2]] == pytest.approx([0.35, 0.30], abs=1e-6)


def test_judge_people_photo_goes_nowhere():
    scan = clip_scan({"family.jpg": photo({PEOPLE: 50.0}), "trip.jpg": photo({PLACE: 50.0})})
    assert judge(scan, PAPER, OTHER) == ({}, [])


def test_judge_sorts_the_unsure_by_rel():
    maybe = receipt_at(0.6)
    scan = clip_scan({"b.jpg": maybe, "a/z.jpg": maybe, "a.jpg": maybe})
    _, unsure = judge(scan, PAPER, OTHER)
    assert [v.rel for v in unsure] == ["a.jpg", "a/z.jpg", "b.jpg"]


def test_judge_of_no_photos():
    assert judge(clip_scan({}), PAPER, OTHER) == ({}, [])


@pytest.mark.parametrize("paper, other", [(PAPER[:3], OTHER), (PAPER, OTHER[:5])])
def test_judge_refuses_embeddings_not_matching_the_prompts(paper, other):
    with pytest.raises(ValueError):
        judge(clip_scan({"a.jpg": photo({})}), paper, other)


# --- plan_notes -----------------------------------------------------------------

def test_plan_moves_a_receipt_to_its_folder(tmp_path):
    touch(tmp_path, "shop.jpg")
    scan = clip_scan({"shop.jpg": photo({RECEIPT: 50.0})})
    sure, _ = judge(scan, PAPER, OTHER)
    plan = plan_notes(tmp_path, scan, sure, {})
    assert plan.mode == "notes"
    assert plan.moves == [Move("shop.jpg", "Notes & Pages/Receipts/shop.jpg")]
    assert plan.summary == ["1 photos → Notes & Pages"]
    assert plan.unchanged == 0


def test_plan_follows_the_answers(tmp_path):
    touch(tmp_path, "a.jpg", "b.jpg", "c.jpg", "d.jpg")
    scan = clip_scan({rel: photo({}) for rel in ("a.jpg", "b.jpg", "c.jpg", "d.jpg")})
    # b and c were unsure and answered; d was unsure and is not answered yet.
    plan = plan_notes(tmp_path, scan, {"a.jpg": "Handwritten"},
                      {"b.jpg": "Book Pages", "c.jpg": None})
    assert plan.moves == [Move("a.jpg", "Notes & Pages/Handwritten/a.jpg"),
                          Move("b.jpg", "Notes & Pages/Book Pages/b.jpg")]
    assert plan.summary == ["2 photos → Notes & Pages", "2 photos stay where they are"]


def test_plan_leaves_a_photo_already_in_its_folder(tmp_path):
    rels = ("Notes & Pages/Receipts/a.jpg", "Notes & Pages/Handwritten/b.jpg",
            "trip/Notes & Pages/Receipts/c.jpg", "Receipts/d.jpg")
    touch(tmp_path, *rels)
    scan = clip_scan({rel: photo({}) for rel in rels})
    plan = plan_notes(tmp_path, scan, {rel: "Receipts" for rel in rels}, {})
    # The folder is under the root's Notes & Pages: one deeper down is not it.
    assert plan.moves == [Move("Notes & Pages/Handwritten/b.jpg",
                               "Notes & Pages/Receipts/b.jpg"),
                          Move("Receipts/d.jpg", "Notes & Pages/Receipts/d.jpg"),
                          Move("trip/Notes & Pages/Receipts/c.jpg",
                               "Notes & Pages/Receipts/c.jpg")]
    assert plan.unchanged == 1
    assert plan.summary == ["3 photos → Notes & Pages", "1 already in place"]


def test_plan_reuses_the_folders_on_disk_in_another_case(tmp_path):
    touch(tmp_path, "notes & pages/receipts/a.jpg", "a.jpg", "b.jpg")
    scan = clip_scan({rel: photo({}) for rel in ("a.jpg", "b.jpg",
                                                 "notes & pages/receipts/a.jpg")})
    plan = plan_notes(tmp_path, scan, {"a.jpg": "Receipts", "b.jpg": "Handwritten",
                                       "notes & pages/receipts/a.jpg": "Receipts"}, {})
    assert plan.moves == [Move("a.jpg", "notes & pages/receipts/a (1).jpg"),
                          Move("b.jpg", "notes & pages/Handwritten/b.jpg")]
    assert plan.unchanged == 1


def test_plan_summary_lines(tmp_path):
    rels = ("a.jpg", "b.jpg", "Notes & Pages/Receipts/c.jpg", "d.jpg")
    touch(tmp_path, *rels)
    scan = clip_scan({rel: photo({}) for rel in rels}, unreadable=1)
    plan = plan_notes(tmp_path, scan, {"a.jpg": "Receipts",
                                       "Notes & Pages/Receipts/c.jpg": "Receipts"},
                      {"b.jpg": "Printed Documents"})
    assert plan.summary == ["2 photos → Notes & Pages",
                            "1 photos stay where they are",
                            "1 photos couldn't be read",
                            "1 already in place"]


def test_no_paper_found(tmp_path):
    """Review Focus 3: no paper gives "0 photos → Notes & Pages" and nothing to review."""
    touch(tmp_path, "family.jpg", "trip.jpg")
    scan = clip_scan({"family.jpg": photo({PEOPLE: 50.0}), "trip.jpg": photo({PLACE: 50.0})})
    sure, unsure = judge(scan, PAPER, OTHER)
    assert unsure == []
    plan = plan_notes(tmp_path, scan, sure, {})
    assert plan.moves == []
    assert plan.summary == ["0 photos → Notes & Pages", "2 photos stay where they are"]
