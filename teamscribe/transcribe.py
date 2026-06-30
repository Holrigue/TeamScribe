"""Transcription with faster-whisper.

We deliberately use the standard multilingual ``large-v3-turbo`` (or
``large-v3``) model with ``language="fr"`` rather than a French fine-tuned
variant. A study on Québécois French (Zhang et al., Canadian Acoustics /
arXiv 2508.21193) found hexagonal-French fine-tunes underperform the base
multilingual model on the Québec accent.

GPU is auto-detected (CUDA + float16); otherwise we fall back to CPU + int8.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _register_nvidia_dll_dirs() -> None:
    """Register the pip-installed CUDA runtime's DLL folders with Windows.

    Since Python 3.8, Windows no longer searches ``PATH`` for DLLs loaded
    by ``ctypes``/native extensions — ``os.add_dll_directory()`` is
    required instead. The ``nvidia-cublas-cu12``/``nvidia-cudnn-cu12``/
    ``nvidia-cuda-runtime-cu12`` pip wheels (the admin-free way to get a
    usable CUDA runtime on Windows) ship their DLLs under
    ``site-packages/nvidia/<pkg>/bin`` but don't register that directory
    themselves, so without this neither our own DLL probe nor
    ctranslate2's internal CUDA loading can find them even though they're
    installed.
    """
    if sys.platform != "win32":
        return
    import importlib.util
    import os

    for pkg in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
        spec = importlib.util.find_spec(f"nvidia.{pkg}")
        if spec is None or not spec.submodule_search_locations:
            continue
        for location in spec.submodule_search_locations:
            bin_dir = Path(location) / "bin"
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except OSError:
                    pass


def _cuda_runtime_usable() -> bool:
    """Check that the CUDA runtime DLLs ctranslate2 needs can actually load.

    ``ctranslate2.get_cuda_device_count()`` only confirms an NVIDIA GPU is
    present, not that cuBLAS/cuDNN are installed. Loading the CUDA path
    in-process and having it fail partway through (e.g. missing
    cublas64_12.dll) can leave the process in a state where even a CPU
    fallback model construction segfaults — so we must verify the DLLs
    load *before* ctranslate2 ever touches CUDA, not catch failures after.
    """
    import ctypes

    _register_nvidia_dll_dirs()

    for dll in ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"):
        try:
            ctypes.WinDLL(dll)
        except OSError:
            return False
    return True


def _select_device(preference: str) -> tuple[str, str]:
    """Return (device, compute_type)."""
    if preference == "cpu":
        return "cpu", "int8"
    if preference == "cuda":
        return "cuda", "float16"

    # auto: only pick CUDA if a GPU is reported AND its runtime DLLs load.
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0 and _cuda_runtime_usable():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


# Module-level cache: (model_name, device, compute_type) -> WhisperModel.
# Loading the model from disk takes several seconds; keeping it resident
# means the second and subsequent transcriptions start immediately.
_model_cache: dict[tuple[str, str, str], object] = {}


def _get_model(model_name: str, device: str, compute_type: str):
    key = (model_name, device, compute_type)
    if key not in _model_cache:
        from faster_whisper import WhisperModel
        _model_cache[key] = WhisperModel(model_name, device=device, compute_type=compute_type)
    return _model_cache[key]


def warmup_model() -> None:
    """Load the Whisper model into the cache without transcribing anything.

    Call this at app startup (off the UI thread) so the model is already
    resident in memory by the time the user stops their first recording.
    """
    model_name = config.whisper_model()
    device, compute_type = _select_device(config.device_preference())
    _get_model(model_name, device, compute_type)


def transcribe(
    audio_path: Path,
    out_dir: Path | None = None,
    *,
    log=print,
) -> dict:
    """Transcribe ``audio_path`` and write transcript.txt / transcript.json.

    Returns a dict with keys: ``text``, ``segments`` (list of Segment dicts),
    ``language``, ``duration``.
    """
    audio_path = Path(audio_path)
    out_dir = Path(out_dir) if out_dir else audio_path.parent

    model_name = config.whisper_model()
    device, compute_type = _select_device(config.device_preference())

    def _run(device: str, compute_type: str) -> list[Segment]:
        log(f"Loading faster-whisper '{model_name}' on {device} ({compute_type})…")
        model = _get_model(model_name, device, compute_type)
        log("Transcribing (French, VAD filter on)…")
        segments_iter, info = model.transcribe(
            str(audio_path),
            language="fr",
            vad_filter=True,
            beam_size=5,
        )
        result = [
            Segment(start=seg.start, end=seg.end, text=seg.text.strip())
            for seg in segments_iter
        ]
        return result, info

    segments, info = _run(device, compute_type)

    full_text = "\n".join(s.text for s in segments).strip()
    result = {
        "text": full_text,
        "segments": [asdict(s) for s in segments],
        "language": info.language,
        "duration": info.duration,
    }

    (out_dir / "transcript.txt").write_text(full_text, encoding="utf-8")
    (out_dir / "transcript.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"Transcribed {len(segments)} segments ({info.duration:.0f}s of audio).")
    return result


def load_transcript(session_path: Path) -> str:
    """Read transcript.txt for a session, raising a clear error if missing."""
    txt = Path(session_path) / "transcript.txt"
    if not txt.is_file():
        raise FileNotFoundError(
            f"No transcript.txt in {session_path}. Run `teamscribe record` "
            f"or transcribe the session first."
        )
    return txt.read_text(encoding="utf-8")
