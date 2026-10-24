import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def make_jpeg():
    def _make(path: Path, exif_dt: str | None = None, size=(16, 16)) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", size, "red")
        if exif_dt is None:
            img.save(path, "JPEG")
        else:
            exif = Image.Exif()
            exif.get_ifd(0x8769)[36867] = exif_dt
            img.save(path, "JPEG", exif=exif)
        return path

    return _make


@pytest.fixture
def make_mp4():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    def _make(path: Path, creation_time: str | None) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", "color=c=black:s=16x16:d=0.1"]
        if creation_time is not None:
            cmd += ["-metadata", f"creation_time={creation_time}"]
        cmd.append(str(path))
        subprocess.run(cmd, check=True)
        return path

    return _make
