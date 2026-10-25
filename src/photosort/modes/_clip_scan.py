"""Embed every photo under a folder with CLIP, for the Content and Notes & Pages modes.

The embeddings are cached (clip.CLIP_KIND), so a rerun with other categories
or prompts never reads the photos again. Videos are skipped.
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
from photosort.scanner import scan
from photosort.thresholds import CLIP_ENGINE_FAILS

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
