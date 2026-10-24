"""Content hashes of files."""

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of the file's bytes, as hex, reading 1 MiB at a time."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
