"""The model files the AI modes need, and downloading them once.

This is the app's only network use: a model file is fetched from its URL
below only when it is missing. Downloads stream into "<name>.part", resume
from there after a cancel or a dropped connection, and are checked against
their SHA-256 before taking the real name, so a model file on disk is either
complete or absent.

missing() checks only the byte size, so it is fast enough to run at every
start; the hash is checked once, when the file is downloaded.
"""

import hashlib
import os
import threading
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import platformdirs

_CHUNK = 1 << 20  # 1 MiB
_TIMEOUT = 30  # seconds without data before a download gives up


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class Model:
    name: str  # for messages: "The <name> model is damaged …"
    rel_path: str  # POSIX path under models_dir()
    url: str
    size: int  # bytes
    sha256: str


_BUFFALO_L = "https://huggingface.co/immich-app/buffalo_l/resolve/main"
_CLIP = "https://huggingface.co/immich-app/ViT-B-32__openai/resolve/main"

MODELS: dict[str, Model] = {
    "face_detection": Model(
        "face detection",
        "buffalo_l/detection/model.onnx",
        f"{_BUFFALO_L}/detection/model.onnx",
        16_923_827,
        "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    ),
    "face_recognition": Model(
        "face recognition",
        "buffalo_l/recognition/model.onnx",
        f"{_BUFFALO_L}/recognition/model.onnx",
        174_383_860,
        "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    ),
    "clip_visual": Model(
        "content image",
        "ViT-B-32__openai/visual/model.onnx",
        f"{_CLIP}/visual/model.onnx",
        351_613_724,
        "33a3df41ceef21acdf371af00f6dd0456ec1f9eba24d03a7720f9c3734e40859",
    ),
    "clip_textual": Model(
        "content text",
        "ViT-B-32__openai/textual/model.onnx",
        f"{_CLIP}/textual/model.onnx",
        254_193_396,
        "b80cf0af751533a6712d92247f0ddc0c95208748bc59a1a27f33e67be6864e3b",
    ),
    "clip_tokenizer": Model(
        "content word list",
        "ViT-B-32__openai/textual/tokenizer.json",
        f"{_CLIP}/textual/tokenizer.json",
        3_642_073,
        "6d9109cc838977f3ca94a379eec36aecc7c807e1785cd729660ca2fc0171fb35",
    ),
}

# The files the Content and Notes & Pages modes need (OpenCLIP ViT-B/32).
CLIP_MODELS = ["clip_visual", "clip_textual", "clip_tokenizer"]


def models_dir() -> Path:
    return Path(platformdirs.user_data_dir("photo-sorter")) / "models"


def model_path(key: str) -> Path:
    return models_dir().joinpath(*MODELS[key].rel_path.split("/"))


def _part_path(path: Path) -> Path:
    return path.with_name(path.name + ".part")


def missing(keys: Iterable[str]) -> list[str]:
    """The keys whose file is absent or has the wrong byte size."""
    out = []
    for key in keys:
        try:
            ok = model_path(key).stat().st_size == MODELS[key].size
        except OSError:
            ok = False
        if not ok:
            out.append(key)
    return out


def download(key: str, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> bool:
    """Fetch model key; True once installed, False if cancelled.

    progress(done, total) is called in bytes. A cancel keeps the .part file
    so the next call resumes. A file whose size or SHA-256 is wrong is
    deleted and raises ModelError.
    """
    m = MODELS[key]
    path = model_path(key)
    part = _part_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        have = part.stat().st_size
    except FileNotFoundError:
        have = 0
    if have > m.size:
        have = 0  # not a prefix of this model; start over

    digest = hashlib.sha256()
    if have:
        with open(part, "rb") as f:
            while chunk := f.read(_CHUNK):
                digest.update(chunk)
    if progress:
        progress(have, m.size)

    if have < m.size:
        if cancel is not None and cancel.is_set():
            return False
        request = urllib.request.Request(m.url)
        if have:
            request.add_header("Range", f"bytes={have}-")
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            if have and getattr(response, "status", None) != 206:
                # The server sent the whole file, not the rest of it.
                have = 0
                digest = hashlib.sha256()
            with open(part, "ab" if have else "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        return False
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    have += len(chunk)
                    if have > m.size:
                        break  # more than the model holds: damaged below
                    if progress:
                        progress(have, m.size)
                f.flush()
                os.fsync(f.fileno())

    if have != m.size or digest.hexdigest() != m.sha256:
        part.unlink(missing_ok=True)
        raise ModelError(f"The downloaded {m.name} model is damaged, please try again.")
    os.replace(part, path)
    return True
