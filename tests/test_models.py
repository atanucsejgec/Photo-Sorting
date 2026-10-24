import hashlib
import http.server
import os
import threading

import pytest

from photosort.ai import models
from photosort.ai.models import Model, ModelError

MIB = 1 << 20


def _model(url: str, data: bytes, sha256: str | None = None) -> Model:
    return Model("test", "test/model.onnx", url,
                 len(data), sha256 or hashlib.sha256(data).hexdigest())


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point models at tmp_path: the real model files are never touched."""
    monkeypatch.setattr(models, "MODELS", {})
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path / "models")
    return tmp_path


@pytest.fixture
def source(store):
    """2.5 MiB of model bytes, served from a file:// URL."""
    data = os.urandom(5 * MIB // 2)
    src = store / "src" / "model.onnx"
    src.parent.mkdir()
    src.write_bytes(data)
    return data, src.as_uri()


def _part(key: str):
    path = models.model_path(key)
    return path.with_name(path.name + ".part")


def test_models_table_matches_buffalo_l():
    det, rec = models.MODELS["face_detection"], models.MODELS["face_recognition"]
    assert det.rel_path == "buffalo_l/detection/model.onnx"
    assert det.size == 16_923_827
    assert det.sha256 == "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91"
    assert det.url == ("https://huggingface.co/immich-app/buffalo_l/resolve/main/"
                       "detection/model.onnx")
    assert rec.rel_path == "buffalo_l/recognition/model.onnx"
    assert rec.size == 174_383_860
    assert rec.sha256 == "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43"
    assert rec.url == ("https://huggingface.co/immich-app/buffalo_l/resolve/main/"
                       "recognition/model.onnx")


def test_models_dir_is_in_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(models.platformdirs, "user_data_dir",
                        lambda app: str(tmp_path / app))
    assert models.models_dir() == tmp_path / "photo-sorter" / "models"
    assert models.model_path("face_detection") == (
        tmp_path / "photo-sorter" / "models" / "buffalo_l" / "detection" / "model.onnx")


def test_download_verifies_and_installs(store, source):
    data, url = source
    models.MODELS["m"] = _model(url, data)
    assert models.missing(["m"]) == ["m"]
    seen = []
    assert models.download("m", progress=lambda done, total: seen.append((done, total)))
    assert models.model_path("m").read_bytes() == data
    assert not _part("m").exists()
    assert models.missing(["m"]) == []
    assert seen[-1] == (len(data), len(data))
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)


def test_download_bad_checksum_raises_and_leaves_nothing(store, source):
    data, url = source
    models.MODELS["m"] = _model(url, data, sha256="0" * 64)
    with pytest.raises(ModelError, match="is damaged, please try again"):
        models.download("m")
    assert not models.model_path("m").exists()
    assert not _part("m").exists()
    assert models.missing(["m"]) == ["m"]


def test_download_cancel_keeps_part_not_file(store, source):
    data, url = source
    models.MODELS["m"] = _model(url, data)
    cancel = threading.Event()

    def progress(done, total):
        if done > 0:
            cancel.set()

    assert models.download("m", progress=progress, cancel=cancel) is False
    assert not models.model_path("m").exists()
    assert _part("m").read_bytes() == data[:MIB]

    # A later try finishes the job (file:// ignores Range, so it starts over).
    assert models.download("m")
    assert models.model_path("m").read_bytes() == data
    assert not _part("m").exists()


def test_download_cancelled_before_start_does_nothing(store, source):
    data, url = source
    models.MODELS["m"] = _model(url, data)
    cancel = threading.Event()
    cancel.set()
    assert models.download("m", cancel=cancel) is False
    assert not models.model_path("m").exists()


def test_missing_detects_wrong_size(store):
    data = b"model bytes"
    models.MODELS["m"] = _model("file:///nowhere", data)
    path = models.model_path("m")
    assert models.missing(["m"]) == ["m"]
    path.parent.mkdir(parents=True)
    path.write_bytes(data[:-1])
    assert models.missing(["m"]) == ["m"]
    path.write_bytes(data)
    assert models.missing(["m"]) == []


class _RangeServer:
    """Serve one blob on 127.0.0.1, honouring "Range: bytes=N-" when asked."""

    def __init__(self, data: bytes, honour_range: bool = True) -> None:
        self.ranges: list[str | None] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                rng = self.headers.get("Range")
                server.ranges.append(rng)
                start = 0
                if rng and honour_range:
                    start = int(rng.removeprefix("bytes=").removesuffix("-"))
                    self.send_response(206)
                    self.send_header("Content-Range",
                                     f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/model.onnx"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


@pytest.fixture
def serve():
    servers = []

    def _serve(data: bytes, honour_range: bool = True) -> _RangeServer:
        s = _RangeServer(data, honour_range)
        servers.append(s)
        return s

    yield _serve
    for s in servers:
        s.close()


def test_download_resumes_with_range(store, serve):
    data = os.urandom(5 * MIB // 2)
    server = serve(data)
    models.MODELS["m"] = _model(server.url, data)
    part = _part("m")
    part.parent.mkdir(parents=True)
    part.write_bytes(data[:MIB])

    seen = []
    assert models.download("m", progress=lambda d, t: seen.append(d))
    assert server.ranges == [f"bytes={MIB}-"]
    assert seen[0] == MIB and seen[-1] == len(data)
    assert models.model_path("m").read_bytes() == data
    assert not part.exists()


def test_download_starts_over_when_server_ignores_range(store, serve):
    data = os.urandom(5 * MIB // 2)
    server = serve(data, honour_range=False)
    models.MODELS["m"] = _model(server.url, data)
    part = _part("m")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x" * MIB)  # would corrupt the file if appended to

    assert models.download("m")
    assert server.ranges == [f"bytes={MIB}-"]
    assert models.model_path("m").read_bytes() == data


def test_download_with_complete_part_only_verifies(store, serve):
    data = os.urandom(MIB)
    server = serve(data)
    models.MODELS["m"] = _model(server.url, data)
    part = _part("m")
    part.parent.mkdir(parents=True)
    part.write_bytes(data)

    assert models.download("m")
    assert server.ranges == []
    assert models.model_path("m").read_bytes() == data
