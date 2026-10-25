"""Notes & Pages mode: move each photo of paper to the folder of its kind.

The four kinds of paper (KINDS) are each described to CLIP by a prompt
(PAPER_PROMPTS); beside them are prompts for photos of other things
(OTHER_PROMPTS). Both are tuned in thresholds. A photo's probability for
each prompt is the softmax, over all of them, of its similarities to them
times thresholds.CLIP_LOGIT_SCALE.
Its paper probability is the sum of the four kinds', and its kind the
likeliest of them. At NOTES_SURE or above it goes to Notes & Pages/<kind>;
from NOTES_UNSURE up to that, the owner is asked first (a Verdict); below, it
stays put. Videos are skipped (see _clip_scan).
"""

from pathlib import Path

import numpy as np

from photosort.modes._clip_scan import ClipScan, place, probabilities, summary_rest
from photosort.modes.content import Verdict
from photosort.plan import Plan
from photosort.thresholds import (
    NOTES_OTHER_PROMPTS,
    NOTES_PAPER_PROMPTS,
    NOTES_SURE,
    NOTES_UNSURE,
)

NOTES_FOLDER = "Notes & Pages"  # at the root; each kind has its folder in it
KINDS = ["Handwritten", "Book Pages", "Printed Documents", "Receipts"]
PAPER_PROMPTS = list(NOTES_PAPER_PROMPTS)  # one per kind, in KINDS' order
OTHER_PROMPTS = list(NOTES_OTHER_PROMPTS)


def judge(scan: ClipScan, text_embeddings_paper: np.ndarray,
          text_embeddings_other: np.ndarray) -> tuple[dict[str, str], list[Verdict]]:
    """The sure photos (rel -> kind) and the unsure ones (ordered by rel).

    text_embeddings_paper holds the unit embeddings of PAPER_PROMPTS, one row
    each, and text_embeddings_other those of OTHER_PROMPTS. An unsure
    Verdict's prob is the photo's paper probability, and its ranked the four
    kinds and their probabilities, best first. Photos below NOTES_UNSURE are
    in neither.
    """
    paper = np.asarray(text_embeddings_paper, np.float64)
    other = np.asarray(text_embeddings_other, np.float64)
    if paper.shape[0] != len(PAPER_PROMPTS) or other.shape[0] != len(OTHER_PROMPTS):
        raise ValueError(f"{paper.shape[0]} paper and {other.shape[0]} other text embeddings, "
                         f"not {len(PAPER_PROMPTS)} and {len(OTHER_PROMPTS)}")
    rels, probs = probabilities(scan, np.concatenate([paper, other]))

    k = len(KINDS)
    sure: dict[str, str] = {}
    unsure: list[Verdict] = []
    for rel, row in zip(rels, probs):
        order = np.argsort(-row[:k], kind="stable")  # ties keep the kinds' order
        best = KINDS[int(order[0])]
        p = float(row[:k].sum())
        if p >= NOTES_SURE:
            sure[rel] = best
        elif p >= NOTES_UNSURE:
            ranked = tuple((KINDS[i], float(row[i])) for i in order)
            unsure.append(Verdict(rel, best, p, ranked))
    return sure, unsure


def plan_notes(root: Path, scan: ClipScan, sure: dict[str, str],
               answers: dict[str, str | None]) -> Plan:
    """Plan the moves for scan's photos.

    sure maps a rel to its kind; answers maps an unsure rel to the kind the
    owner chose, or to None to leave it where it is. An unsure photo not
    answered stays put. A kind's folder is Notes & Pages/<kind> at the root,
    spelt as on disk where it exists in another case; a photo already in it
    is left there.
    """
    placed = place(root, scan, sure, answers, lambda kind: f"{NOTES_FOLDER}/{kind}")
    summary = [f"{len(placed.moves)} photos → {NOTES_FOLDER}", *summary_rest(scan, placed)]
    return Plan("notes", placed.moves, summary, placed.unchanged)
