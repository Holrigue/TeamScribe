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
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _select_device(preference: str) -> tuple[str, str]:
    """Return (device, compute_type)."""
    if preference == "cpu":
        return "cpu", "int8"
    if preference == "cuda":
        return "cuda", "float16"

    # auto: try CUDA, fall back to CPU.
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


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
    from faster_whisper import WhisperModel

    audio_path = Path(audio_path)
    out_dir = Path(out_dir) if out_dir else audio_path.parent

    model_name = config.whisper_model()
    device, compute_type = _select_device(config.device_preference())
    log(f"Loading faster-whisper '{model_name}' on {device} ({compute_type})…")

    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    log("Transcribing (French, VAD filter on)…")
    segments_iter, info = model.transcribe(
        str(audio_path),
        language="fr",
        vad_filter=True,
        beam_size=5,
    )

    segments: list[Segment] = []
    for seg in segments_iter:
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip()))

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
