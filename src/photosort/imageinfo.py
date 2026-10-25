"""Read photos for comparing them: size, perceptual hash, sharpness, thumbnail.

Every function sees the photo the right way up (EXIF orientation applied)
and raises UnreadableImage for any file Pillow can't read.
"""

from pathlib import Path

import imagehash
import numpy as np
import pillow_heif
from PIL import Image, ImageOps

from photosort.thresholds import SHARPNESS_MAX_ROWS, SHARPNESS_WIDTH, THUMB_PX

pillow_heif.register_heif_opener()

ORIENTATION = 0x0112
_SWAPS_SIDES = {5, 6, 7, 8}  # orientations that turn the photo a quarter turn
HASH_PX = 512


class UnreadableImage(Exception):
    """Pillow could not read the file as an image."""


def open_image(path: Path, max_px: int | None = None) -> Image.Image:
    """Return the photo as RGB, the right way up, at most max_px on its longest side."""
    img = decode(path, max_px)
    if max_px is not None:
        img.thumbnail((max_px, max_px))
    return img


def decode(path: Path, min_px: int | None) -> Image.Image:
    """Return the photo as RGB, the right way up.

    With min_px, a JPEG may be decoded at a smaller scale, but never below
    min_px on either side (unless the photo itself is smaller).
    """
    try:
        with Image.open(path) as img:
            if min_px is not None:
                img.draft("RGB", (min_px, min_px))
            return _to_8_bit(ImageOps.exif_transpose(img)).convert("RGB")
    except Exception as exc:
        raise UnreadableImage(f"{path}: {exc}") from exc


def _to_8_bit(img: Image.Image) -> Image.Image:
    """img with 16- and 32-bit integer and float greys scaled to 8-bit "L".

    Pillow's own convert clips those at 255 instead of scaling them: a
    16-bit PNG would come out nearly white, a 0..1 float image black.
    Integer greys are taken as 16-bit (0..65535); float greys as 0..1 when
    none is above 1, else as 0..255 (clipped). Other modes are left alone.
    """
    if img.mode == "I" or img.mode.startswith("I;16"):
        pixels = np.clip(np.asarray(img).astype(np.int64), 0, 65535) >> 8
    elif img.mode == "F":
        pixels = np.nan_to_num(np.asarray(img, dtype=np.float64))
        if pixels.size and pixels.max() <= 1:
            pixels = pixels * 255
        pixels = np.clip(pixels, 0, 255).round()
    else:
        return img
    return Image.fromarray(pixels.astype(np.uint8))


def image_size(path: Path) -> tuple[int, int]:
    """Return (width, height) the right way up, without decoding the pixels."""
    try:
        with Image.open(path) as img:
            width, height = img.size
            orientation = img.getexif().get(ORIENTATION)
    except Exception as exc:
        raise UnreadableImage(f"{path}: {exc}") from exc
    if orientation in _SWAPS_SIDES:
        return height, width
    return width, height


def perceptual_hash(path: Path) -> int:
    """Return the photo's 64-bit perceptual hash; similar photos differ in few bits."""
    return _hash_of(decode(path, HASH_PX))


def sharpness(path: Path) -> float:
    """Return how sharp the photo is (higher is sharper); compare within a group.

    The variance of a 4-neighbour Laplacian of the greyscale photo resized
    to SHARPNESS_WIDTH wide.
    """
    return _sharpness_of(decode(path, SHARPNESS_WIDTH))


def analyse(path: Path) -> tuple[int, float]:
    """Return (perceptual_hash(path), sharpness(path)), decoding the photo once."""
    img = decode(path, max(HASH_PX, SHARPNESS_WIDTH))
    measured = _sharpness_of(img)  # first: _hash_of shrinks img in place
    return _hash_of(img), measured


def _hash_of(img: Image.Image) -> int:
    """The perceptual hash of img, shrunk in place to HASH_PX first."""
    img.thumbnail((HASH_PX, HASH_PX))
    return int(str(imagehash.phash(img)), 16)


def _sharpness_of(img: Image.Image) -> float:
    """The sharpness measure of img (see sharpness); img is left unchanged."""
    return laplacian_variance(measuring_grey(img))


def measuring_grey(img: Image.Image) -> np.ndarray:
    """img in greyscale (values 0-255, as float64), resized to SHARPNESS_WIDTH
    wide and 3 to SHARPNESS_MAX_ROWS rows high; img is left unchanged."""
    height = min(max(3, round(img.height * SHARPNESS_WIDTH / img.width)), SHARPNESS_MAX_ROWS)
    return np.asarray(img.convert("L").resize((SHARPNESS_WIDTH, height)), dtype=np.float64)


def laplacian_variance(grey: np.ndarray) -> float:
    """The variance of the 4-neighbour Laplacian of grey (at least 3 by 3)."""
    laplacian = (
        grey[:-2, 1:-1] + grey[2:, 1:-1] + grey[1:-1, :-2] + grey[1:-1, 2:]
        - 4 * grey[1:-1, 1:-1]
    )
    return float(laplacian.var())


def thumbnail(path: Path, max_px: int = THUMB_PX) -> Image.Image:
    """Return a small RGB copy for review screens."""
    return open_image(path, max_px)
