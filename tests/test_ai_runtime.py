import logging
from pathlib import Path

import onnxruntime
import pytest

from photosort.ai import runtime

CUDA, CPU = "CUDAExecutionProvider", "CPUExecutionProvider"


@pytest.fixture
def fake_ort(monkeypatch):
    """Fake ONNX Runtime: CUDA listed, sessions recorded, nothing really loaded."""
    calls = {"preload": 0, "severity": [], "sessions": []}
    monkeypatch.setattr(runtime, "_dlls_preloaded", False)
    monkeypatch.setattr(runtime, "_logging_set", False)
    monkeypatch.setattr(runtime, "_gpu_failed", False)
    monkeypatch.setattr(runtime, "_cuda_driver_present", lambda: True)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CUDA, CPU])

    def preload():
        calls["preload"] += 1

    monkeypatch.setattr(onnxruntime, "preload_dlls", preload)
    monkeypatch.setattr(onnxruntime, "set_default_logger_severity",
                        lambda level: calls["severity"].append(level))

    class Session:
        cuda_breaks = False

        def __init__(self, path, providers):
            calls["sessions"].append(list(providers))
            if CUDA in providers and Session.cuda_breaks:
                raise RuntimeError("CUDA failure 100: no CUDA-capable device")
            self._providers = list(providers)

        def get_providers(self):
            return self._providers

    monkeypatch.setattr(onnxruntime, "InferenceSession", Session)
    calls["Session"] = Session
    return calls


def test_providers_gpu_and_fallback(monkeypatch):
    monkeypatch.setattr(runtime, "_cuda_driver_present", lambda: True)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CUDA, CPU])
    assert runtime.providers(True) == [CUDA, CPU]
    assert runtime.providers(False) == [CPU]
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CPU])
    assert runtime.providers(True) == [CPU]
    assert runtime.providers(False) == [CPU]


def test_no_nvidia_driver_means_cpu(fake_ort, monkeypatch, caplog):
    # onnxruntime-gpu lists CUDA even on a computer without an NVIDIA GPU.
    monkeypatch.setattr(runtime, "_cuda_driver_present", lambda: False)
    assert runtime.providers(True) == [CPU]
    assert runtime.runtime_status(True) == "CPU (no usable GPU found)"
    s = runtime.make_session(Path("det.onnx"), True)
    assert s.get_providers() == [CPU]
    assert fake_ort["sessions"] == [[CPU]]
    assert fake_ort["preload"] == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_cuda_driver_probe(monkeypatch):
    probe = runtime._cuda_driver_present.__wrapped__
    loaded = []
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.ctypes, "CDLL", lambda name: loaded.append(name))
    assert probe() is True
    assert loaded == ["libcuda.so.1"]

    def missing(name):
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(runtime.ctypes, "CDLL", missing)
    assert probe() is False
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime.ctypes, "WinDLL", missing, raising=False)
    assert probe() is False


def test_runtime_status_texts(fake_ort, monkeypatch):
    assert runtime.runtime_status(True) == "GPU (CUDA)"
    assert runtime.runtime_status(False) == "CPU (GPU switched off)"
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: [CPU])
    assert runtime.runtime_status(True) == "CPU (no usable GPU found)"
    assert runtime.runtime_status(False) == "CPU (GPU switched off)"


def test_make_session_uses_gpu_and_sets_up_once(fake_ort, caplog):
    caplog.set_level(logging.INFO, logger="photosort")
    s1 = runtime.make_session(Path("det.onnx"), True)
    s2 = runtime.make_session(Path("rec.onnx"), True)
    assert s1.get_providers()[0] == CUDA and s2.get_providers()[0] == CUDA
    assert fake_ort["preload"] == 1
    assert fake_ort["severity"] == [3]
    assert "det.onnx runs on CUDAExecutionProvider" in caplog.text


def test_make_session_cpu_only_skips_cuda_setup(fake_ort):
    s = runtime.make_session(Path("det.onnx"), False)
    assert s.get_providers() == [CPU]
    assert fake_ort["sessions"] == [[CPU]]
    assert fake_ort["preload"] == 0
    assert fake_ort["severity"] == [3]


def test_make_session_falls_back_to_cpu(fake_ort, caplog):
    caplog.set_level(logging.INFO, logger="photosort")
    fake_ort["Session"].cuda_breaks = True
    s = runtime.make_session(Path("det.onnx"), True)
    assert s.get_providers() == [CPU]
    assert fake_ort["sessions"] == [[CUDA, CPU], [CPU]]
    assert "det.onnx runs on CPUExecutionProvider" in caplog.text
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "no CUDA-capable device" in warning.getMessage()
    assert warning.exc_info is None
    # Settings now tells the truth about this run.
    assert runtime.runtime_status(True) == "CPU (no usable GPU found)"


def test_make_session_preload_failure_is_harmless(fake_ort, monkeypatch):
    def broken():
        raise OSError("libcudnn.so.9: cannot open shared object file")

    monkeypatch.setattr(onnxruntime, "preload_dlls", broken)
    s = runtime.make_session(Path("det.onnx"), True)
    assert s.get_providers()[0] == CUDA


def test_make_session_silent_cpu_fallback_is_noticed(fake_ort, monkeypatch):
    # ONNX Runtime may drop CUDA without raising when its libraries are missing.
    class QuietSession:
        def __init__(self, path, providers):
            pass

        def get_providers(self):
            return [CPU]

    monkeypatch.setattr(onnxruntime, "InferenceSession", QuietSession)
    runtime.make_session(Path("det.onnx"), True)
    assert runtime.runtime_status(True) == "CPU (no usable GPU found)"


def test_make_session_bad_file_raises_without_blaming_gpu(fake_ort, monkeypatch, caplog):
    class Broken:
        def __init__(self, path, providers):
            raise RuntimeError("NO_SUCHFILE")

    monkeypatch.setattr(onnxruntime, "InferenceSession", Broken)
    with pytest.raises(RuntimeError, match="NO_SUCHFILE"):
        runtime.make_session(Path("gone.onnx"), True)
    assert runtime.runtime_status(True) == "GPU (CUDA)"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
