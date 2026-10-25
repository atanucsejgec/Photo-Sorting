import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True, scope="session")
def _gc_on_the_gui_thread(qapp):
    """Collect garbage on the GUI thread only, as the app does (see gc_guard)."""
    from photosort.ui import gc_guard

    gc_guard.install(qapp)
    yield
    gc_guard.uninstall()


@pytest.fixture(autouse=True, scope="session")
def _dark_theme(qapp):
    """Run every test under the app's real look, as main() sets it."""
    from photosort.ui import theme

    theme.apply(qapp)


@pytest.fixture(autouse=True)
def _gc_check_after_each_test():
    """A test that never runs the event loop gets its garbage checked too."""
    yield
    from photosort.ui import gc_guard

    gc_guard.check()


@pytest.fixture
def case_sensitive_fs(tmp_path):
    """Skip unless tmp_path can hold names differing only in case."""
    probe = tmp_path / "case-probe"
    probe.write_bytes(b"")
    sensitive = not (tmp_path / "CASE-PROBE").exists()
    probe.unlink()
    if not sensitive:
        pytest.skip("case twins cannot exist on a case-insensitive filesystem")


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


@pytest.fixture
def make_pattern_jpeg():
    """A seeded random pattern that looks the same at any size.

    Low-frequency noise (a 16x12 grid, bicubic upscaled) carries the overall
    look, so resized copies hash alike. A finer 64x48 grid upscaled with
    hard block edges adds detail, so blur measurably lowers sharpness.
    """

    def _make(path: Path, seed: int, size=(800, 600), exif_dt: str | None = None,
              orientation: int | None = None, shift: int = 0, blur: float = 0.0,
              quality: int = 90) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(seed)
        coarse = rng.integers(0, 256, (12, 16, 3), dtype=np.uint8)
        fine = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
        smooth = np.asarray(Image.fromarray(coarse).resize(size, Image.Resampling.BICUBIC))
        edges = np.asarray(Image.fromarray(fine).resize(size, Image.Resampling.NEAREST))
        pixels = (0.65 * smooth + 0.35 * edges).astype(np.uint8)
        img = Image.fromarray(np.roll(pixels, shift, axis=1))
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(blur))
        exif = Image.Exif()
        if exif_dt is not None:
            exif.get_ifd(0x8769)[36867] = exif_dt
        if orientation is not None:
            exif[0x0112] = orientation
        img.save(path, "JPEG", quality=quality, exif=exif)
        return path

    return _make
