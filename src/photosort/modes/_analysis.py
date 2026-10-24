"""Measuring photos for the modes that compare them (duplicates, repeated shots)."""

from pathlib import Path

from photosort.imageinfo import UnreadableImage, analyse, image_size


def measure_photo(path: Path) -> tuple[int, int, int, float] | None:
    """Return (width, height, perceptual hash, sharpness), or None if unreadable.

    Width and height are the right way up.
    """
    try:
        width, height = image_size(path)
        phash, sharp = analyse(path)
    except UnreadableImage:
        return None
    return width, height, phash, sharp
