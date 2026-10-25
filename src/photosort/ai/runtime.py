"""Open ONNX models on the GPU when it works, otherwise on the CPU.

The GPU is used when the owner has "Use GPU" on, ONNX Runtime offers CUDA
and an NVIDIA driver is installed (the onnxruntime-gpu wheel offers CUDA
even on computers without one). A CUDA session that fails to start is retried on the CPU, and the
failure is remembered so Settings can say the run is on the CPU.

ONNX Runtime is imported only when it is first needed, so the app starts
(and the other modes work) even where it can't be loaded.
"""

import ctypes
import functools
import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import onnxruntime

_log = logging.getLogger("photosort")

CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"
_ERRORS_ONLY = 3  # ONNX Runtime's own logging: 0 verbose … 3 error, 4 fatal

_lock = threading.Lock()
_logging_set = False
_dlls_preloaded = False
_gpu_failed = False  # a CUDA session fell back to the CPU in this process


@functools.cache
def _cuda_driver_present() -> bool:
    """True when the NVIDIA driver's CUDA library can be loaded."""
    try:
        if sys.platform == "win32":
            ctypes.WinDLL("nvcuda.dll")
        else:
            ctypes.CDLL("libcuda.so.1")
    except OSError:
        return False
    return True


def _ort():
    """The onnxruntime module, imported on first use."""
    import onnxruntime

    return onnxruntime


def providers(use_gpu: bool) -> list[str]:
    ort = _ort()
    if use_gpu and CUDA in ort.get_available_providers() and _cuda_driver_present():
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
    ort = _ort()
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


def make_session(path: Path, use_gpu: bool) -> "onnxruntime.InferenceSession":
    global _gpu_failed
    ort = _ort()
    path = Path(path)
    wanted = providers(use_gpu)
    _set_up(cuda=wanted[0] == CUDA)
    if wanted[0] == CUDA:
        try:
            session = ort.InferenceSession(str(path), providers=wanted)
        except Exception as gpu_error:
            # If the file itself is bad this raises too, with its own error;
            # only once the CPU copes is the GPU known to be at fault.
            session = ort.InferenceSession(str(path), providers=[CPU])
            _log.warning("Could not open %s on the GPU (%s), using the CPU",
                         path.name, gpu_error)
            _gpu_failed = True
    else:
        session = ort.InferenceSession(str(path), providers=[CPU])
    used = session.get_providers()[0]
    if wanted[0] == CUDA and used != CUDA:
        _gpu_failed = True  # ONNX Runtime dropped CUDA without raising
    _log.info("%s runs on %s", path.name, used)
    return session
