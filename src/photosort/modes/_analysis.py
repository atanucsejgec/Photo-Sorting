"""Measuring photos for the modes that compare them (duplicates, repeated shots)."""

import struct
from pathlib import Path

from photosort.cache import AnalysisCache
from photosort.imageinfo import UnreadableImage, analyse, image_size

PHOTO_KIND = "photo-v1"
_PHOTO = struct.Struct("<IIQd")  # width, height, perceptual hash, sharpness


def measure_photo(path: Path, cache: AnalysisCache | None = None
                  ) -> tuple[int, int, int, float] | None:
    """Return (width, height, perceptual hash, sharpness), or None if unreadable.

    Width and height are the right way up. With a cache, a photo measured
    before is not read again. Unreadable photos are not cached.
    """
    if cache is not None:
        data = cache.get(PHOTO_KIND, path)
        if data is not None and len(data) == _PHOTO.size:
            return _PHOTO.unpack(data)
    try:
        width, height = image_size(path)
        phash, sharp = analyse(path)
    except UnreadableImage:
        return None
    if cache is not None:
        cache.put(PHOTO_KIND, path, _PHOTO.pack(width, height, phash, sharp))
    return width, height, phash, sharp
