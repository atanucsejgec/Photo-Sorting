"""How good a photo is to keep: sharpness and light, and why a bad one is bad.

Everything is measured on the greyscale photo resized to SHARPNESS_WIDTH
wide (see imageinfo.measuring_grey), so a big photo and a small copy of it
measure alike, and the sharpness is the one the other modes compare.
"""

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from photosort.cache import AnalysisCache
from photosort.imageinfo import UnreadableImage, decode, laplacian_variance, measuring_grey
from photosort.thresholds import (
    BAD_BLUR, BAD_BRIGHT_FRAC, BAD_BRIGHT_MEAN, BAD_DARK_MEAN, BAD_DARK_P95, BAD_FLAT_STD,
    SHARPNESS_WIDTH,
)

# Bump the version (quality-v2, ...) whenever what is stored would come out
# differently: a new packing, or a change to thresholds.SHARPNESS_WIDTH or to
# how a measure is worked out. Old entries are then never read.
QUALITY_KIND = "quality-v1"
_QUALITY = struct.Struct("<5d")  # the fields of Quality, in order
BRIGHT = 250  # a grey value at or above this counts as burnt out


@dataclass(frozen=True)
class Quality:
    sharpness: float  # Laplacian variance, as imageinfo.sharpness; higher is sharper
    mean: float  # mean grey value, 0-255
    p95: float  # 95th percentile of the grey values
    std: float  # standard deviation of the grey values
    bright_frac: float  # fraction of pixels at BRIGHT or above


def measure_quality(path: Path, cache: AnalysisCache | None = None) -> Quality | None:
    """Return the photo's Quality, or None if it can't be read.

    With a cache, a photo measured before is not read again. Unreadable
    photos are not cached.
    """
    if cache is not None:
        data = cache.get(QUALITY_KIND, path)
        if data is not None and len(data) == _QUALITY.size:
            return Quality(*_QUALITY.unpack(data))
    try:
        grey = measuring_grey(decode(path, SHARPNESS_WIDTH))
    except UnreadableImage:
        return None
    q = Quality(
        sharpness=laplacian_variance(grey),
        mean=float(grey.mean()),
        p95=float(np.percentile(grey, 95)),
        std=float(grey.std()),
        bright_frac=float(np.count_nonzero(grey >= BRIGHT) / grey.size),
    )
    if cache is not None:
        cache.put(QUALITY_KIND, path, _QUALITY.pack(
            q.sharpness, q.mean, q.p95, q.std, q.bright_frac))
    return q


def bad_reason(q: Quality) -> str | None:
    """Why the photo is bad ("accidental shot", "too dark", "too bright",
    "blurry"), or None if it looks fine. The first rule that matches wins."""
    if q.std < BAD_FLAT_STD:
        return "accidental shot"  # all one shade: a pocket, a lens cap, the floor
    if q.mean < BAD_DARK_MEAN and q.p95 < BAD_DARK_P95:
        return "too dark"
    if q.bright_frac > BAD_BRIGHT_FRAC or q.mean > BAD_BRIGHT_MEAN:
        return "too bright"
    if q.sharpness < BAD_BLUR:
        return "blurry"
    return None
