"""Open ONNX models on the GPU when it works, otherwise on the CPU.

The GPU is used when the owner has "Use GPU" on and ONNX Runtime offers
CUDA. A CUDA session that fails to start is retried on the CPU, and the
failure is remembered so Settings can say the run is on the CPU.
"""

import logging
import threading
from pathlib import Path

import onnxruntime as ort

_log = logging.getLogger("photosort")

CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"
_ERRORS_ONLY = 3  # ONNX Runtime's own logging: 0 verbose … 3 error, 4 fatal

_lock = threading.Lock()
_logging_set = False
_dlls_preloaded = False
_gpu_failed = False  # a CUDA session fell back to the CPU in this process


def providers(use_gpu: bool) -> list[str]:
    if use_gpu and CUDA in ort.get_available_providers():
        return [CUDA, CPU]
    return [CPU]


def runtime_status(use_gpu: bool) -> str:
    if not use_gpu:
        return "CPU (GPU switched off)"
    if providers(True)[0] == CUDA and not _gpu_failed:
        return "GPU (CUDA)"
    return "CPU (no usable GPU found)"


def _set_up(cuda: bool) -> None:
    """Quiet ONNX Runtime's logging, and load the CUDA libraries, once each."""
    global _logging_set, _dlls_preloaded
    with _lock:
        if not _logging_set:
            ort.set_default_logger_severity(_ERRORS_ONLY)
            _logging_set = True
        if cuda and not _dlls_preloaded:
            _dlls_preloaded = True
            try:
                # Finds the CUDA/cuDNN libraries installed as nvidia-* wheels.
                ort.preload_dlls()
            except Exception:
                _log.warning("Could not preload the CUDA libraries", exc_info=True)


def make_session(path: Path, use_gpu: bool) -> ort.InferenceSession:
    global _gpu_failed
    path = Path(path)
    wanted = providers(use_gpu)
    _set_up(cuda=wanted[0] == CUDA)
    if wanted[0] == CUDA:
        try:
            session = ort.InferenceSession(str(path), providers=wanted)
        except Exception:
            _log.warning("Could not open %s on the GPU, trying the CPU", path.name,
                         exc_info=True)
            # Raises on its own if the file itself is bad; only then is the
            # GPU known to be at fault.
            session = ort.InferenceSession(str(path), providers=[CPU])
            _gpu_failed = True
    else:
        session = ort.InferenceSession(str(path), providers=[CPU])
    used = session.get_providers()[0]
    if wanted[0] == CUDA and used != CUDA:
        _gpu_failed = True  # ONNX Runtime dropped CUDA without raising
    _log.info("%s runs on %s", path.name, used)
    return session
