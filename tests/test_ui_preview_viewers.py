"""Preview's viewers: image, PDF, video, audio, text and the info card.

Media is made here: a JPEG with Pillow, a PDF with QPdfWriter, a WAV with
wave, a video with ffmpeg (skipped without it). The audio output may be a
null sink offscreen, so the player's state is checked, never the sound.
"""

import logging
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QSizeF, Qt
from PySide6.QtGui import QImage, QKeyEvent, QPageSize, QPainter, QPdfWriter, QWheelEvent
from PySide6.QtMultimedia import QMediaPlayer

from photosort.ui import preview_viewers as pv

WAIT_MS = 10000


def _key(key: Qt.Key, text: str = "",
         mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, mods, text)


def _shown(qtbot, viewer, size=(800, 600)):
    qtbot.addWidget(viewer)
    viewer.resize(*size)
    viewer.show()
    qtbot.waitExposed(viewer)
    return viewer


def _jpeg(path: Path, size=(400, 300)) -> Path:
    Image.new("RGB", size, "red").save(path, "JPEG")
    return path


A6_PT = (297.0, 420.0)  # QPageSize A6 in points, as _pdf makes it


def _pdf(path: Path, pages: int = 2) -> Path:
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A6))
    painter = QPainter(writer)
    for n in range(pages):
        if n:
            writer.newPage()
        painter.drawText(100, 100, f"page {n + 1}")
    painter.end()
    return path


def _wav(path: Path, seconds: float = 3.0, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\0\0" * int(rate * seconds))
    return path


# ----- image -------------------------------------------------------------------------

def test_image_viewer_zoom_fit_and_limits(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.ImageViewer())
    viewer.load(_jpeg(tmp_path / "a.jpg", (1600, 1200)))
    qtbot.waitUntil(viewer.is_loaded, timeout=WAIT_MS)
    fit = viewer.zoom()
    assert 0.1 <= fit < 1.0
    assert fit == pytest.approx(min(viewer.view.viewport().width() / 1600,
                                    viewer.view.viewport().height() / 1200), rel=0.02)
    for _ in range(40):
        assert viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    assert viewer.zoom() == pytest.approx(8.0)
    for _ in range(60):
        assert viewer.handle_key(_key(Qt.Key.Key_Minus, "-"))
    assert viewer.zoom() == pytest.approx(0.1)
    assert viewer.handle_key(_key(Qt.Key.Key_F, "f"))
    assert viewer.zoom() == pytest.approx(fit)
    # Ctrl+wheel zooms in
    centre = QPointF(viewer.view.viewport().rect().center())
    wheel = QWheelEvent(centre, viewer.view.viewport().mapToGlobal(centre), QPoint(0, 0),
                        QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    viewer.view.wheelEvent(wheel)
    assert viewer.zoom() > fit
    # ↑/↓ scroll a zoomed image; a key it doesn't use is left alone
    for _ in range(10):
        viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    bar = viewer.view.verticalScrollBar()
    before = bar.value()
    assert viewer.handle_key(_key(Qt.Key.Key_Down))
    assert bar.value() > before
    assert not viewer.handle_key(_key(Qt.Key.Key_Right))
    assert ("F", "fit") in viewer.keys()


def test_image_viewer_fit_never_enlarges_a_small_image(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.ImageViewer())
    viewer.load(_jpeg(tmp_path / "small.jpg", (100, 80)))
    qtbot.waitUntil(viewer.is_loaded, timeout=WAIT_MS)
    assert viewer.zoom() == 1.0  # its real size, not blown up to the window
    shown = viewer.view.mapFromScene(viewer.view.sceneRect()).boundingRect()
    middle = viewer.view.viewport().rect().center()
    assert abs(shown.center().x() - middle.x()) <= 2  # centred
    assert abs(shown.center().y() - middle.y()) <= 2
    for _ in range(40):
        viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    assert viewer.zoom() == pytest.approx(8.0)  # zooming in by hand still works
    viewer.handle_key(_key(Qt.Key.Key_F, "f"))
    assert viewer.zoom() == 1.0


def test_image_viewer_zoom_past_the_copy_decodes_the_original(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(pv, "IMAGE_MAX_PX", 300)  # the first copy: 300 px long side
    viewer = _shown(qtbot, pv.ImageViewer(), (200, 150))  # fitted: below 100 % of the copy
    viewer.load(_jpeg(tmp_path / "big.jpg", (1200, 800)))
    qtbot.waitUntil(viewer.is_loaded, timeout=WAIT_MS)
    assert viewer.decoded_size() == (300, 200)
    assert viewer.image_size() == (1200, 800)  # percentages are of the original's pixels
    fit = viewer.zoom()
    assert fit == pytest.approx(min(viewer.view.viewport().width() / 1200,
                                    viewer.view.viewport().height() / 800), rel=0.02)
    viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))  # still below 100 % of the copy
    assert viewer.zoom() < 0.25
    qtbot.wait(200)
    assert viewer.decoded_size() == (300, 200)
    while viewer.zoom() <= 0.25:
        viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    viewer.view.centerOn(700, 300)
    centre = viewer.view.mapToScene(viewer.view.viewport().rect().center())
    zoom = viewer.zoom()
    qtbot.waitUntil(lambda: viewer.decoded_size() == (1200, 800), timeout=WAIT_MS)
    assert viewer.zoom() == pytest.approx(zoom)
    after = viewer.view.mapToScene(viewer.view.viewport().rect().center())
    near = 2 / viewer.zoom()  # 2 screen pixels, in the photo's pixels
    assert abs(after.x() - centre.x()) <= near and abs(after.y() - centre.y()) <= near
    assert viewer.view.sceneRect().width() == pytest.approx(1200)


def test_viewer_pools_run_one_heavy_task_at_a_time():
    """Decodes and PDF renders have their own one-thread pools, never the
    global one (16 threads: 16 full-size decodes at once ran out of memory)."""
    from PySide6.QtCore import QThreadPool
    image, pdf = pv.image_pool(), pv.pdf_pool()
    assert image.maxThreadCount() == 1
    assert pdf.maxThreadCount() == 1
    assert image is not pdf
    assert QThreadPool.globalInstance() not in (image, pdf)
    assert pv.image_pool() is image and pv.pdf_pool() is pdf  # made once


def test_a_queued_decode_cancelled_returns_at_once(tmp_path, monkeypatch):
    def never(*_a, **_k):
        raise AssertionError("a cancelled decode opened the file")

    monkeypatch.setattr(pv, "open_image", never)
    cancel = threading.Event()
    cancel.set()
    assert pv._decode(7, tmp_path / "x.jpg", 4096, cancel=cancel) == (7, None, None)


def _full_asked(qtbot, tmp_path, monkeypatch, original: tuple[int, int],
                copy: tuple[int, int]) -> list[int]:
    """Load a photo whose own size says original (its file is copy), zoom
    in to 800 %; the max_px of every decode asked for."""
    asked = []
    real = pv._decode

    def spy(generation, path, max_px, progress=None, cancel=None):
        asked.append(max_px)
        return real(generation, path, max_px, progress, cancel)

    monkeypatch.setattr(pv, "_decode", spy)
    monkeypatch.setattr(pv, "image_size", lambda _path: original)
    viewer = _shown(qtbot, pv.ImageViewer(), (400, 300))
    viewer.load(_jpeg(tmp_path / "p.jpg", copy))
    qtbot.waitUntil(viewer.is_loaded, timeout=WAIT_MS)
    assert viewer.image_size() == original
    for _ in range(40):
        viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    assert viewer.zoom() == pv.ZOOM_MAX
    qtbot.wait(300)
    pv.image_pool().waitForDone()
    viewer.release()
    return asked


def test_full_resolution_decode_capped_at_8192(qtbot, tmp_path, monkeypatch):
    assert pv.FULL_MAX_PX == 8192
    asked = _full_asked(qtbot, tmp_path, monkeypatch, (9000, 6300), (900, 630))  # 56.7 MP
    assert asked == [pv.IMAGE_MAX_PX, 8192]


def test_no_full_resolution_decode_over_64_megapixels(qtbot, tmp_path, monkeypatch):
    assert pv.FULL_SKIP_PIXELS == 64_000_000
    asked = _full_asked(qtbot, tmp_path, monkeypatch, (10000, 7000), (1000, 700))  # 70 MP
    assert asked == [pv.IMAGE_MAX_PX]  # the zoom stays on the 4096 copy


def test_image_viewer_heic_or_jpg_loads_off_gui_thread(qtbot, tmp_path, monkeypatch):
    threads = []
    real = pv.open_image

    def spy(path, max_px=None):
        threads.append(threading.current_thread())
        return real(path, max_px)

    monkeypatch.setattr(pv, "open_image", spy)
    viewer = _shown(qtbot, pv.ImageViewer())
    viewer.load(_jpeg(tmp_path / "b.jpg", (64, 48)))
    qtbot.waitUntil(viewer.is_loaded, timeout=WAIT_MS)
    assert threads and threads[0] is not threading.main_thread()
    assert viewer.image_size() == (64, 48)
    assert viewer.decoded_size() == (64, 48)


def test_image_viewer_newest_load_wins_and_bad_file_fails(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.ImageViewer())
    viewer.load(_jpeg(tmp_path / "big.jpg", (300, 200)))
    viewer.load(_jpeg(tmp_path / "small.jpg", (30, 20)))
    qtbot.waitUntil(viewer.is_loaded, timeout=WAIT_MS)
    qtbot.wait(200)  # the first load's result, if late, must not replace it
    assert viewer.image_size() == (30, 20)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not a jpeg")
    with qtbot.waitSignal(viewer.failed, timeout=WAIT_MS):
        viewer.load(bad)


# ----- PDF ---------------------------------------------------------------------------

def test_pdf_viewer_page_count_and_render(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 400))
    viewer.load(_pdf(tmp_path / "doc.pdf", 2))
    qtbot.waitUntil(lambda: viewer.page_count() == 2, timeout=WAIT_MS)
    assert viewer.page_text() == "page 1 of 2"
    qtbot.waitUntil(lambda: 1 in viewer.rendered_pages(), timeout=WAIT_MS)
    # page 1 is in view; page 2 is one ahead
    qtbot.waitUntil(lambda: 2 in viewer.rendered_pages(), timeout=WAIT_MS)
    zoom = viewer.zoom()
    assert viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    assert viewer.zoom() > zoom
    assert viewer.dpi() == pytest.approx(min(400.0, 72 * viewer.zoom()
                                             * viewer.devicePixelRatioF()), abs=1)
    for _ in range(40):
        viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    assert viewer.dpi() <= 400
    assert viewer.handle_key(_key(Qt.Key.Key_F, "f"))
    assert viewer.zoom() == pytest.approx(zoom)
    with qtbot.waitSignal(viewer.page_changed, timeout=WAIT_MS):
        for _ in range(5):
            viewer.handle_key(_key(Qt.Key.Key_PageDown))
    assert viewer.page_text() == "page 2 of 2"


def test_pdf_viewer_renders_only_pages_in_view_and_one_ahead(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 300))
    viewer.load(_pdf(tmp_path / "long.pdf", 8))
    qtbot.waitUntil(lambda: 2 in viewer.rendered_pages(), timeout=WAIT_MS)
    qtbot.wait(300)
    assert viewer.rendered_pages() == {1, 2}


def test_pdf_renders_off_gui_thread(qtbot, tmp_path, monkeypatch):
    threads = []
    real_open, real_render = pv.PdfiumFile.open, pv.PdfiumFile.render

    def spy_open(self, *a, **k):
        threads.append(threading.current_thread())
        return real_open(self, *a, **k)

    def spy_render(self, *a, **k):
        threads.append(threading.current_thread())
        return real_render(self, *a, **k)

    monkeypatch.setattr(pv.PdfiumFile, "open", spy_open)
    monkeypatch.setattr(pv.PdfiumFile, "render", spy_render)
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 300))
    viewer.load(_pdf(tmp_path / "doc.pdf", 2))
    qtbot.waitUntil(lambda: viewer.rendered_pages() == {1, 2}, timeout=WAIT_MS)
    assert len(threads) >= 3  # the open and both pages
    assert threading.main_thread() not in threads


def test_pdf_viewer_release_drops_results(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 300))
    failures = []
    viewer.failed.connect(failures.append)
    viewer.load(_pdf(tmp_path / "doc.pdf", 2))
    viewer.release()  # the open and any render still on the pool are dropped
    qtbot.wait(500)
    assert viewer.rendered_pages() == set()
    assert viewer.page_count() == 0
    assert viewer.page_text() == ""
    assert failures == []  # a load stopped on purpose is not a failure


def test_pdf_open_of_2000_pages_is_quick(tmp_path, monkeypatch):
    """Opening reads the page sizes without loading each page (that took
    seconds on a real 2000-page PDF, under the lock every PDF shares, and
    could not be stopped)."""
    path = _pdf(tmp_path / "long.pdf", 2000)
    loaded = []
    real_get_page = pv.pdfium.PdfDocument.get_page
    monkeypatch.setattr(pv.pdfium.PdfDocument, "get_page",
                        lambda self, n: loaded.append(n) or real_get_page(self, n))
    file = pv.PdfiumFile(path)
    started = time.monotonic()
    sizes = file.open(threading.Event())
    took = time.monotonic() - started
    file.close()
    assert isinstance(sizes, list) and len(sizes) == 2000
    assert sizes[0] == pytest.approx((A6_PT[0], A6_PT[1]), abs=1)
    assert took < 1.0
    assert loaded == []  # no page was loaded just to read its size


def test_pdf_open_stops_when_cancelled_part_way(tmp_path, monkeypatch):
    path = _pdf(tmp_path / "long.pdf", 300)
    cancel = threading.Event()
    real = pv.pdfium.PdfDocument.get_page_size
    asked = []

    def spy(self, n):
        asked.append(n)
        if n == 150:
            cancel.set()
        return real(self, n)

    monkeypatch.setattr(pv.pdfium.PdfDocument, "get_page_size", spy)
    file = pv.PdfiumFile(path)
    assert file.open(cancel) is None
    assert len(asked) <= 200  # stopped at the next check, not after all 300
    file.close()


def test_pdf_release_closes_document(qtbot, tmp_path, monkeypatch):
    closed = []
    real_close = pv.pdfium.PdfDocument.close

    def spy_close(self, *a, **k):
        # every pdfium call is made holding the one lock
        closed.append((self, pv._PDFIUM_LOCK.locked()))
        return real_close(self, *a, **k)

    monkeypatch.setattr(pv.pdfium.PdfDocument, "close", spy_close)
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 300))
    viewer.load(_pdf(tmp_path / "first.pdf", 2))
    qtbot.waitUntil(lambda: viewer.rendered_pages() == {1, 2}, timeout=WAIT_MS)
    first = viewer._file
    assert first.document is not None and closed == []
    viewer.load(_pdf(tmp_path / "second.pdf", 1))  # a new load closes the old file
    qtbot.waitUntil(lambda: first.document is None, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: viewer.rendered_pages() == {1}, timeout=WAIT_MS)
    second = viewer._file
    viewer.release()
    qtbot.waitUntil(lambda: second.document is None, timeout=WAIT_MS)
    assert len(closed) == 2 and all(locked for _, locked in closed)
    # a render asked of a closed file does nothing
    assert second.render(1, 1.0, threading.Event()) is None


def test_pdf_viewer_ignores_results_of_an_earlier_load(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 300))
    viewer.load(_pdf(tmp_path / "first.pdf", 3))
    old = viewer._tasks.generation
    viewer.load(_pdf(tmp_path / "second.pdf", 1))
    qtbot.waitUntil(lambda: viewer.rendered_pages() == {1}, timeout=WAIT_MS)
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    viewer._got_pages((old, [(100.0, 100.0)] * 3))
    viewer._rendered((old, 2, 72, image))
    viewer._rendered((old, 1, 72, image))
    assert viewer.page_count() == 1
    assert viewer.rendered_pages() == {1}
    assert viewer.page_text() == "page 1 of 1"


def test_pdf_zoom_is_debounced(qtbot, tmp_path, monkeypatch):
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 300))
    viewer.load(_pdf(tmp_path / "doc.pdf", 2))
    qtbot.waitUntil(lambda: viewer.rendered_pages() == {1, 2}, timeout=WAIT_MS)
    started = []
    real = pv._pdf_render
    monkeypatch.setattr(pv, "_pdf_render",
                        lambda *a, **k: started.append(a[2]) or real(*a, **k))
    for _ in range(5):
        viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    assert started == []  # nothing yet: the timer gathers the steps
    qtbot.waitUntil(lambda: len(started) >= 1, timeout=WAIT_MS)
    qtbot.wait(200)
    assert set(started) == {round(viewer.dpi())}  # only the final dpi was drawn


def test_pdf_huge_page_capped_at_40mp(qtbot, tmp_path):
    path = tmp_path / "huge.pdf"
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QSizeF(5000, 5000), QPageSize.Unit.Point))
    painter = QPainter(writer)
    painter.drawText(100, 100, "huge")
    painter.end()
    viewer = _shown(qtbot, pv.PdfViewer(), (600, 300))
    viewer.load(path)
    qtbot.waitUntil(lambda: viewer.rendered_pages() == {1}, timeout=WAIT_MS)
    for _ in range(40):
        viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    # 400 dpi would be 27778 px square, 771 MP
    assert 72 * viewer.zoom() * viewer.devicePixelRatioF() >= 400
    qtbot.waitUntil(lambda: viewer._drawn.get(1) == round(viewer.dpi()), timeout=30000)
    pixmap = viewer._labels[0].pixmap()
    assert pixmap.width() * pixmap.height() <= pv.PDF_MAX_PIXELS == 40_000_000
    assert pixmap.width() * pixmap.height() > 0.95 * pv.PDF_MAX_PIXELS
    assert pixmap.width() == pytest.approx(pixmap.height(), abs=1)
    # the 40000 px square box is painted only where it shows, never scaled whole
    assert not viewer.grab().isNull()


def _bad_pdf(tmp_path: Path) -> Path:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 nothing here")
    return bad


def test_pdf_expected_failure_is_not_logged_as_error(qtbot, tmp_path, caplog):
    bad = _bad_pdf(tmp_path)
    viewer = _shown(qtbot, pv.PdfViewer())
    with qtbot.waitSignal(viewer.failed, timeout=WAIT_MS):
        viewer.load(bad)
    qtbot.wait(100)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    records = [r for r in caplog.records if "bad.pdf" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.WARNING


def test_pdf_viewer_without_pdfium_fails_to_info(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(pv, "pdfium", None)  # the import failed
    viewer = _shown(qtbot, pv.PdfViewer())
    with qtbot.waitSignal(viewer.failed, timeout=WAIT_MS) as blocker:
        viewer.load(_pdf(tmp_path / "doc.pdf", 1))
    assert blocker.args == [pv.CANT_SHOW_PDF]
    assert pv.CANT_SHOW_PDF == "Can't show this PDF"


def test_pdf_viewer_broken_pdf_fails(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.PdfViewer())
    with qtbot.waitSignal(viewer.failed, timeout=WAIT_MS) as blocker:
        viewer.load(_bad_pdf(tmp_path))
    assert blocker.args == [pv.CANT_SHOW_PDF]
    assert viewer.page_count() == 0


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
def test_pdf_locked_with_a_password_fails(qtbot, tmp_path):
    locked = tmp_path / "locked.pdf"
    subprocess.run(["gs", "-q", "-sDEVICE=pdfwrite", "-sOwnerPassword=own",
                    "-sUserPassword=user", "-o", str(locked), str(_pdf(tmp_path / "doc.pdf", 1))],
                   check=True, timeout=60)
    viewer = _shown(qtbot, pv.PdfViewer())
    with qtbot.waitSignal(viewer.failed, timeout=WAIT_MS) as blocker:
        viewer.load(locked)
    assert blocker.args == [pv.CANT_SHOW_PDF]


# ----- video and audio ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def _speed_back_to_one():
    pv.MediaViewer.speed = 1.0
    yield
    pv.MediaViewer.speed = 1.0


def test_media_viewer_audio_wav_seek_speed(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.MediaViewer(video=False))
    viewer.load(_wav(tmp_path / "tone.wav", 3.0))
    player = viewer.player
    qtbot.waitUntil(lambda: player.duration() > 0, timeout=WAIT_MS)
    assert player.playbackState() != QMediaPlayer.PlaybackState.PlayingState  # no autoplay
    assert viewer.time_text() == "0:00 / 0:03"
    viewer.seek(+10000)
    qtbot.waitUntil(lambda: player.position() == player.duration(), timeout=WAIT_MS)
    viewer.seek(-10000)
    qtbot.waitUntil(lambda: player.position() == 0, timeout=WAIT_MS)
    rates = []
    for _ in range(3):
        viewer.cycle_speed()
        rates.append(player.playbackRate())
    assert rates == [2.0, 4.0, 1.0]
    viewer.set_speed(4.0)
    assert player.playbackRate() == 4.0
    assert [b.isChecked() for b in viewer.speed_buttons] == [False, False, True]
    viewer.speed_buttons[1].click()
    assert player.playbackRate() == 2.0
    # keys
    assert viewer.handle_key(_key(Qt.Key.Key_S, "s"))
    assert player.playbackRate() == 4.0
    assert viewer.handle_key(_key(Qt.Key.Key_Right, "", Qt.KeyboardModifier.ShiftModifier))
    assert not viewer.handle_key(_key(Qt.Key.Key_Right))  # the page's: next file
    viewer.play_pause()
    qtbot.waitUntil(lambda: player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                    timeout=WAIT_MS)
    assert viewer.handle_key(_key(Qt.Key.Key_Space, " "))
    qtbot.waitUntil(lambda: player.playbackState() != QMediaPlayer.PlaybackState.PlayingState,
                    timeout=WAIT_MS)
    # the speed is kept for the next file
    other = _shown(qtbot, pv.MediaViewer(video=False))
    other.load(_wav(tmp_path / "two.wav", 1.0))
    assert other.player.playbackRate() == 4.0
    assert [b.isChecked() for b in other.speed_buttons] == [False, False, True]
    viewer.release()
    other.release()


def test_media_slider_click_jumps_there(qtbot, tmp_path):
    viewer = _shown(qtbot, pv.MediaViewer(video=False))
    viewer.load(_wav(tmp_path / "long.wav", 10.0))
    player = viewer.player
    qtbot.waitUntil(lambda: player.duration() > 0 and viewer.slider.maximum() > 0,
                    timeout=WAIT_MS)
    slider = viewer.slider
    qtbot.mouseClick(slider, Qt.MouseButton.LeftButton,
                     pos=QPoint(slider.width() // 2, slider.height() // 2))
    qtbot.waitUntil(lambda: abs(player.position() - player.duration() / 2)
                    < player.duration() * 0.1, timeout=WAIT_MS)
    # a drag from there still moves it
    qtbot.mousePress(slider, Qt.MouseButton.LeftButton,
                     pos=QPoint(slider.width() // 2, slider.height() // 2))
    qtbot.mouseMove(slider, QPoint(slider.width() * 3 // 4, slider.height() // 2))
    qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton,
                       pos=QPoint(slider.width() * 3 // 4, slider.height() // 2))
    qtbot.waitUntil(lambda: player.position() > player.duration() * 0.6, timeout=WAIT_MS)
    viewer.release()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_media_viewer_video_first_frame_paused(qtbot, tmp_path):
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=s=64x48:d=2", "-pix_fmt", "yuv420p", str(video)],
                   check=True)
    viewer = _shown(qtbot, pv.MediaViewer(video=True))
    viewer.load(video)
    sink = viewer.video_widget.videoSink()
    qtbot.waitUntil(lambda: sink.videoFrame().isValid(), timeout=WAIT_MS)
    assert viewer.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    assert viewer.player.position() == 0
    qtbot.wait(300)
    assert viewer.player.position() == 0  # still not playing
    viewer.release()


def test_media_viewers_can_share_one_player(qtbot, tmp_path):
    """The page gives its video and audio viewers one SharedPlayer: only the
    viewer with a file loaded acts on the player's signals."""
    shared = pv.SharedPlayer()
    sound = _shown(qtbot, pv.MediaViewer(video=False, shared=shared))
    picture = pv.MediaViewer(video=True, shared=shared)
    qtbot.addWidget(picture)
    assert sound.player is picture.player is shared.player
    assert sound.audio is shared.audio
    failed = []
    sound.failed.connect(failed.append)
    picture.failed.connect(failed.append)
    sound.load(_wav(tmp_path / "a.wav", seconds=2.0))
    qtbot.waitUntil(lambda: sound.player.duration() > 0, timeout=WAIT_MS)
    assert sound.time_text() == "0:00 / 0:02"
    sound.release()
    picture.load(_wav(tmp_path / "b.wav", seconds=4.0))
    qtbot.waitUntil(lambda: picture.player.duration() > 3000, timeout=WAIT_MS)
    assert picture.player.videoOutput() is picture.video_widget
    picture.release()
    sound.load(tmp_path / "a.wav")
    qtbot.waitUntil(lambda: 0 < sound.player.duration() < 3000, timeout=WAIT_MS)
    assert sound.player.videoOutput() is None
    sound.release()
    assert failed == []


def test_media_viewer_unplayable_fails(qtbot, tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio at all" * 10)
    viewer = _shown(qtbot, pv.MediaViewer(video=False))
    with qtbot.waitSignal(viewer.failed, timeout=WAIT_MS):
        viewer.load(bad)
    viewer.release()


def test_release_clears_source(qtbot, tmp_path):
    media = _shown(qtbot, pv.MediaViewer(video=False))
    media.load(_wav(tmp_path / "a.wav", 1.0))
    qtbot.waitUntil(lambda: media.player.duration() > 0, timeout=WAIT_MS)
    media.play_pause()
    media.release()
    assert media.player.source().isEmpty()
    assert media.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState

    image = _shown(qtbot, pv.ImageViewer())
    image.load(_jpeg(tmp_path / "a.jpg"))
    qtbot.waitUntil(image.is_loaded, timeout=WAIT_MS)
    image.release()
    assert not image.is_loaded()

    text = _shown(qtbot, pv.TextViewer())
    (tmp_path / "a.txt").write_text("hello")
    text.load(tmp_path / "a.txt")
    text.release()
    qtbot.wait(200)  # a read that comes back after release is dropped
    assert text.edit.toPlainText() == ""


# ----- text and info -----------------------------------------------------------------

def test_text_viewer_reads_at_most_1mb(qtbot, tmp_path, monkeypatch):
    threads = []
    real = pv._read_text
    monkeypatch.setattr(pv, "_read_text", lambda *a, **k: threads.append(
        threading.current_thread()) or real(*a, **k))
    big = tmp_path / "big.log"
    big.write_bytes(b"a" * (pv.MAX_TEXT_BYTES + 5000))
    viewer = _shown(qtbot, pv.TextViewer())
    viewer.load(big)
    qtbot.waitUntil(lambda: viewer.edit.toPlainText() != "", timeout=WAIT_MS)
    assert threads and threads[0] is not threading.main_thread()  # read on the pool
    text = viewer.edit.toPlainText()
    assert text.count("a") == pv.MAX_TEXT_BYTES
    assert pv.MAX_TEXT_BYTES == 1024 * 1024
    assert viewer.edit.isReadOnly()
    # one 1 MB line is shown unwrapped (wrapping it would freeze the window)
    assert viewer.edit.lineWrapMode() == viewer.edit.LineWrapMode.NoWrap
    lines = tmp_path / "lines.txt"
    lines.write_text("short line\n" * 100)
    viewer.load(lines)
    qtbot.waitUntil(lambda: viewer.edit.toPlainText().startswith("short"), timeout=WAIT_MS)
    assert viewer.edit.lineWrapMode() == viewer.edit.LineWrapMode.WidgetWidth
    # not UTF-8: shown with replacement characters, never an error
    odd = tmp_path / "odd.txt"
    odd.write_bytes(b"caf\xe9")
    viewer.load(odd)
    qtbot.waitUntil(lambda: viewer.edit.toPlainText() == "caf�", timeout=WAIT_MS)
    size = viewer.edit.font().pointSizeF()
    assert viewer.handle_key(_key(Qt.Key.Key_Plus, "+"))
    assert viewer.edit.font().pointSizeF() > size
    assert viewer.handle_key(_key(Qt.Key.Key_Minus, "-"))
    assert viewer.edit.font().pointSizeF() == size
    assert viewer.edit.font().fixedPitch() or viewer.edit.font().styleHint() \
        == viewer.edit.font().StyleHint.Monospace


def test_info_card_shows_details(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "archive.zip"
    path.write_bytes(b"PK\x03\x04" + b"\0" * 2044)
    threads = []
    real = pv._file_facts
    monkeypatch.setattr(pv, "_file_facts", lambda *a, **k: threads.append(
        threading.current_thread()) or real(*a, **k))
    card = _shown(qtbot, pv.InfoCard())
    card.load(path)
    qtbot.waitUntil(lambda: "2.0 KB" in card.text(), timeout=WAIT_MS)
    assert threads and threads[0] is not threading.main_thread()  # stat and type on the pool
    shown = card.text()
    assert "archive.zip" in shown
    assert "2.0 KB" in shown
    assert str(path) in shown
    assert pv.date_text(path.stat().st_mtime) in shown
    assert card.open_button.text() == "Open with the system app (O)"
    opened = []
    monkeypatch.setattr(pv.QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    assert card.handle_key(_key(Qt.Key.Key_O, "o"))
    card.open_button.click()
    assert [u.toLocalFile() for u in opened] == [str(path), str(path)]
    card.set_message("Can't show this PDF")
    assert "Can't show this PDF" in card.text()
    card.load(path)  # a new file starts without the message
    assert "Can't show this PDF" not in card.text()
    card.set_message("Can't show this PDF")  # set after load: the late facts keep it
    qtbot.waitUntil(lambda: "2.0 KB" in card.text(), timeout=WAIT_MS)
    assert "Can't show this PDF" in card.text()
    assert ("O", "open with the system app") in card.keys()


def test_size_text():
    assert pv.size_text(0) == "0 B"
    assert pv.size_text(900) == "900 B"
    assert pv.size_text(2048) == "2.0 KB"
    assert pv.size_text(int(2.3 * 1024 * 1024)) == "2.3 MB"
    assert pv.size_text(5 * 1024 ** 3) == "5.0 GB"
