"""Media file kinds, decided by file extension."""

from pathlib import Path
from typing import Literal

PHOTO_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"})
VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".mov", ".3gp", ".mkv"})


def kind(path: str | Path) -> Literal["photo", "video"] | None:
    """Return "photo" or "video" for a media file, or None for anything else."""
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None
