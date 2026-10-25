"""Embed every photo under a folder with CLIP, for the Content and Notes & Pages modes.

The embeddings are cached (clip.CLIP_KIND), so a rerun with other categories
or prompts never reads the photos again. Videos are skipped. Also the parts
the two modes judge and plan with alike: the probabilities over the prompts,
and the moves to the chosen folders.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from photosort.cache import AnalysisCache
from photosort.clip import CLIP_KIND, pack, unpack
from photosort.imageinfo import UnreadableImage
from photosort.media import kind
from photosort.naming import TargetAllocator, fold
from photosort.plan import Move
from photosort.scanner import scan
from photosort.thresholds import CLIP_ENGINE_FAILS, CLIP_LOGIT_SCALE

_log = logging.getLogger("photosort")


@dataclass
class ClipScan:
    embeddings: dict[str, np.ndarray]  # rel -> the photo's (512,) unit embedding
    photos: int  # photos looked at, unreadable ones included
    unreadable: int


class _Embedder:
    """embed(path), where one photo failing never stops the scan.

    An error other than UnreadableImage is logged with the photo's path and
    the photo counted as unreadable; but when the first CLIP_ENGINE_FAILS
    photos embedded all fail so, the model is at fault, not the photos, and
    the error is raised.
    """

    def __init__(self, embed: Callable[[Path], np.ndarray]) -> None:
        self._embed = embed
        self._worked = False  # a photo has been embedded
        self._fails = 0

    def __call__(self, path: Path, rel: str) -> np.ndarray | None:
        try:
            embedding = self._embed(path)
        except UnreadableImage:
            return None
        except Exception:
            self._fails += 1
            if not self._worked and self._fails >= CLIP_ENGINE_FAILS:
                raise
            _log.warning("Describing %s with CLIP failed", rel, exc_info=True)
            return None
        self._worked = True
        return embedding


def _embedding_of(root: Path, rel: str, embed: _Embedder,
                  cache: AnalysisCache | None) -> np.ndarray | None:
    """The embedding of the photo root/rel, or None if it can't be read (not cached)."""
    path = root / rel
    if cache is not None:
        data = cache.get(CLIP_KIND, path)
        if data is not None:
            try:
                return unpack(data)
            except ValueError:
                pass  # not ours after all: embed it again
    embedding = embed(path, rel)
    if embedding is not None and cache is not None:
        cache.put(CLIP_KIND, path, pack(embedding))
    return embedding


def embed_photos(
    root: Path,
    include_subfolders: bool,
    embed: Callable[[Path], np.ndarray],
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    cache: AnalysisCache | None = None,
) -> ClipScan | None:
    """Embed every photo under root; None if cancelled.

    embed(path) returns a photo's (512,) unit embedding (raising
    UnreadableImage if it can't). A photo it fails on otherwise is counted as
    unreadable, unless the first photos all fail (see _Embedder).
    progress(i, total) is called after each photo; videos are not looked at.
    With a cache, a photo embedded before is not embedded again.
    """
    root = Path(root)
    embedder = _Embedder(embed)
    photos = [rel for rel in scan(root, include_subfolders) if kind(rel) == "photo"]
    total = len(photos)
    embeddings: dict[str, np.ndarray] = {}
    unreadable = 0
    for i, rel in enumerate(photos, start=1):
        if cancel is not None and cancel.is_set():
            return None
        embedding = _embedding_of(root, rel, embedder, cache)
        if embedding is None:
            unreadable += 1
        else:
            embeddings[rel] = embedding
        if progress is not None:
            progress(i, total)
    if cancel is not None and cancel.is_set():
        return None
    return ClipScan(embeddings, total, unreadable)


def probabilities(scan: ClipScan, text_embeddings: np.ndarray) -> tuple[list[str], np.ndarray]:
    """scan's rels, sorted, and each photo's probabilities over the prompts.

    text_embeddings holds the prompts' unit embeddings, one row each. Row i of
    the result is the softmax, over the prompts, of photo i's similarities to
    them times CLIP_LOGIT_SCALE.
    """
    rels = sorted(scan.embeddings)
    text = np.asarray(text_embeddings, np.float64)
    if not rels:
        return rels, np.zeros((0, text.shape[0]))
    images = np.stack([scan.embeddings[rel] for rel in rels]).astype(np.float64)
    logits = CLIP_LOGIT_SCALE * images @ text.T
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)
    return rels, probs


@dataclass
class Placed:
    moves: list[Move]
    folders: set[str]  # the folded folders moved to
    unchanged: int  # photos already in their folder


def place(root: Path, scan: ClipScan, sure: dict[str, str], answers: dict[str, str | None],
          folder_of: Callable[[str], str]) -> Placed:
    """The moves of scan's photos to the folders of their labels.

    sure maps a rel to its label; answers maps an unsure rel to the label the
    owner chose, or to None to leave it where it is. An unsure photo not
    answered stays put. folder_of(label) is the label's folder (POSIX, under
    root); a photo already in it (by fold) is left there.
    """
    chosen = dict(sure)
    for rel, label in answers.items():
        if label is None:
            chosen.pop(rel, None)
        else:
            chosen[rel] = label

    allocator = TargetAllocator(Path(root))
    placed = Placed([], set(), 0)
    for rel in sorted(chosen):
        if rel not in scan.embeddings:
            continue  # not a photo of this scan
        folder = folder_of(chosen[rel])
        parent, _, name = rel.rpartition("/")
        if fold(parent) == fold(folder):
            placed.unchanged += 1
            continue
        placed.moves.append(Move(rel, allocator.allocate(f"{folder}/{name}")))
        placed.folders.add(fold(folder))
    return placed


def summary_rest(scan: ClipScan, placed: Placed) -> list[str]:
    """The summary lines after the first: the photos left, unreadable and in place."""
    lines = []
    stay = scan.photos - scan.unreadable - len(placed.moves) - placed.unchanged
    if stay:
        lines.append(f"{stay} photos stay where they are")
    if scan.unreadable:
        lines.append(f"{scan.unreadable} photos couldn't be read")
    if placed.unchanged:
        lines.append(f"{placed.unchanged} already in place")
    return lines
