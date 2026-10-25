"""Content mode: move each photo to the folder of its best category.

The categories are the owner's list (Settings.content_categories), each
described to CLIP by one prompt per part of its name ("Pets & Animals" by "a
photo of pets" and "a photo of animals"), its text embedding being the unit
mean of theirs. Beside them are the background prompts
(thresholds.CONTENT_BACKGROUND_PROMPTS), which stand for "none of these". A
photo's probability for each category or background prompt is the softmax,
over all of them, of its similarities to them times thresholds.CLIP_LOGIT_SCALE.
A photo a background prompt fits best stays put. Otherwise, at CONTENT_SURE
or above it goes to its best category's folder at the root; from
CONTENT_UNSURE up to that, the owner is asked first (a Verdict); below, it
stays put. Videos are skipped (see _clip_scan).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from photosort.modes._clip_scan import ClipScan, place, probabilities, summary_rest
from photosort.naming import fit_name
from photosort.plan import Plan
from photosort.thresholds import (
    CONTENT_BACKGROUND_PROMPTS,
    CONTENT_SURE,
    CONTENT_UNSURE,
    FOLDER_NAME_MAX,
    FOLDER_NAME_MAX_BYTES,
)


@dataclass(frozen=True)
class Verdict:
    """An unsure photo to ask the owner about: is rel a photo of best?"""

    rel: str
    best: str  # the likeliest category
    prob: float  # its probability
    ranked: tuple[tuple[str, float], ...]  # every category and its probability, best first


# A category name's parts are split by " & " and by " and " (any case).
_PART_SEPARATOR = re.compile(r"\s+&\s+|\s+and\s+", re.IGNORECASE)


def _parts(name: str) -> list[str]:
    """The parts of a category name; the whole name if none is left."""
    parts = [part.strip() for part in _PART_SEPARATOR.split(name)]
    return [part for part in parts if part] or [name]


def prompt_sets(categories: list[str]) -> list[list[str]]:
    """The text CLIP compares the photos with: per category, in order, one
    prompt per part of its name ("Selfies & People" -> "a photo of selfies",
    "a photo of people")."""
    return [[f"a photo of {part.casefold()}" for part in _parts(name)] for name in categories]


def category_embeddings(text_embeddings: Callable[[list[str]], np.ndarray],
                        categories: list[str]) -> np.ndarray:
    """One unit text embedding per category (k x 512): the mean of its parts'.

    text_embeddings(texts) gives the prompts' unit embeddings, one row each;
    it is called once, with every category's prompts in order.
    """
    sets = prompt_sets(categories)
    rows = np.asarray(text_embeddings([text for parts in sets for text in parts]), np.float64)
    out = np.zeros((len(sets), rows.shape[1]))
    start = 0
    for i, parts in enumerate(sets):
        mean = rows[start:start + len(parts)].mean(axis=0)
        out[i] = mean / np.linalg.norm(mean)
        start += len(parts)
    return out


def background_prompts() -> list[str]:
    """The text standing for "none of the categories", judged beside them."""
    return list(CONTENT_BACKGROUND_PROMPTS)


def judge(scan: ClipScan, categories: list[str], category_embeddings: np.ndarray,
          background_embeddings: np.ndarray) -> tuple[dict[str, str], list[Verdict]]:
    """The sure photos (rel -> category) and the unsure ones (ordered by rel).

    category_embeddings holds the categories' unit text embeddings, one row
    each (see category_embeddings()), and background_embeddings those of
    background_prompts().
    Photos a background prompt fits best, or below CONTENT_UNSURE, are in neither.
    """
    cats = np.asarray(category_embeddings, np.float64)
    if not categories or cats.shape[0] != len(categories):
        raise ValueError(f"{len(categories)} categories but {cats.shape[0]} text embeddings")
    rels, probs = probabilities(scan, np.concatenate([cats, background_embeddings]))
    k = len(categories)
    sure: dict[str, str] = {}
    unsure: list[Verdict] = []
    for rel, row in zip(rels, probs):
        order = np.argsort(-row[:k], kind="stable")  # ties keep the categories' order
        best = int(order[0])
        p = float(row[best])
        if p < row[k:].max(initial=0.0):
            continue  # none of the categories fits it best
        if p >= CONTENT_SURE:
            sure[rel] = categories[best]
        elif p >= CONTENT_UNSURE:
            ranked = tuple((categories[i], float(row[i])) for i in order)
            unsure.append(Verdict(rel, categories[best], p, ranked))
    return sure, unsure


def plan_content(root: Path, scan: ClipScan, sure: dict[str, str],
                 answers: dict[str, str | None]) -> Plan:
    """Plan the moves for scan's photos.

    sure maps a rel to its category; answers maps an unsure rel to the
    category the owner chose, or to None to leave it where it is. An unsure
    photo not answered stays put. Each category's folder is
    fit_name(category) at the root; a photo already in it is left there.
    """
    placed = place(root, scan, sure, answers,
                   lambda category: fit_name(category, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES))
    summary = [f"{len(placed.moves)} photos → {len(placed.folders)} category folders",
               *summary_rest(scan, placed)]
    return Plan("content", placed.moves, summary, placed.unchanged)
