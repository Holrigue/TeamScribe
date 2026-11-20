"""Tests for the streamed, crash-recoverable, privacy-aware audio capture.

These exercise the pure mix/recovery/privacy logic in ``teamscribe.capture``
without any audio hardware: synthetic PCM parts are written to a staging dir and
fed through the real code paths. (The live WASAPI capture itself is Windows-only
and not unit-tested here.)
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from teamscribe import capture


# --- helpers ---------------------------------------------------------------

def _write_pcm(path: Path, seconds: float, rate: int, channels: int, freq: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    mono = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    if channels > 1:
        mono = np.repeat(mono[:, None], channels, axis=1).reshape(-1)
    path.write_bytes(mono.tobytes())


def _simulate_capture(session: Path, staging: Path, *, keep_audio: bool,
                      loop_secs: float = 5.0, mic_secs: float = 5.0,
                      loop_rate: int = 48000, mic_rate: int = 44100,
                      loop_ch: int = 2, mic_ch: int = 1) -> None:
    """Recreate what record() leaves on disk mid-capture: parts in staging,
    manifest in the session folder."""
    session.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    _write_pcm(staging / capture.LOOPBACK_PCM, loop_secs, loop_rate, loop_ch, 220.0)
    _write_pcm(staging / capture.MIC_PCM, mic_secs, mic_rate, mic_ch, 440.0)
    capture._write_meta(
        session / capture.CAPTURE_META,
        staging,
        keep_audio,
        [
            {"name": "loopback", "pcm": capture.LOOPBACK_PCM, "rate": loop_rate,
             "channels": loop_ch, "width": 2},
            {"name": "microphone", "pcm": capture.MIC_PCM, "rate": mic_rate,
             "channels": mic_ch, "width": 2},
        ],
    )


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == capture.TARGET_RATE
        return wf.getnframes() / wf.getframerate()


# --- tests -----------------------------------------------------------------

def test_parts_live_in_staging_not_session(tmp_path):
    session = tmp_path / "sess"
    staging = tmp_path / "stage" / "sess"
    _simulate_capture(session, staging, keep_audio=True)
    # Raw audio is in staging, NOT in the (cloud-synced) session folder.
    assert (staging / capture.LOOPBACK_PCM).is_file()
    assert not (session / capture.LOOPBACK_PCM).exists()
    # Only the tiny no-audio manifest sits in the session folder.
    assert (session / capture.CAPTURE_META).is_file()


def test_finalize_mix_multiblock(tmp_path):
    session = tmp_path / "sess"
    staging = tmp_path / "stage" / "sess"
    _simulate_capture(session, staging, keep_audio=True, loop_secs=65, mic_secs=65)
    capture._finalize_mix(session / capture.CAPTURE_META, session / "audio.wav")
    assert abs(_wav_duration(session / "audio.wav") - 65.0) < 0.2


def test_recover_session_builds_wav_and_purges_staging(tmp_path):
    session = tmp_path / "sess"
    staging = tmp_path / "stage" / "sess"
    _simulate_capture(session, staging, keep_audio=False)
    assert capture.has_recoverable_parts(session) is True
    out = capture.recover_session(session)
    assert out is not None and out.is_file()
    # Everything raw is gone afterwards: staging emptied, manifest removed.
    assert not (staging / capture.LOOPBACK_PCM).exists()
    assert not (session / capture.CAPTURE_META).exists()
    assert capture.has_recoverable_parts(session) is False


def test_capture_keep_audio_reflects_privacy_flag(tmp_path):
    s_priv = tmp_path / "priv"
    s_keep = tmp_path / "keep"
    _simulate_capture(s_priv, tmp_path / "st1", keep_audio=False)
    _simulate_capture(s_keep, tmp_path / "st2", keep_audio=True)
    assert capture.capture_keep_audio(s_priv) is False
    assert capture.capture_keep_audio(s_keep) is True
    assert capture.capture_keep_audio(tmp_path / "nope") is None


def test_discard_capture_purges_raw_audio(tmp_path):
    session = tmp_path / "sess"
    staging = tmp_path / "stage" / "sess"
    _simulate_capture(session, staging, keep_audio=False)
    capture.discard_capture(session)
    assert not staging.exists() or not any(staging.iterdir())
    assert not (session / capture.CAPTURE_META).exists()
    assert not (session / "audio.wav").exists()  # discard never transcribes/keeps


def test_staging_from_another_machine_is_not_recoverable(tmp_path):
    # Manifest synced from another computer: its staging path doesn't exist here.
    session = tmp_path / "sess"
    missing_staging = tmp_path / "does-not-exist" / "sess"
    session.mkdir()
    capture._write_meta(
        session / capture.CAPTURE_META, missing_staging, False,
        [{"name": "loopback", "pcm": capture.LOOPBACK_PCM, "rate": 48000, "channels": 2, "width": 2}],
    )
    assert capture.has_recoverable_parts(session) is False


def test_prune_stale_manifest_removes_dangling_pointer(tmp_path):
    session = tmp_path / "sess"
    missing_staging = tmp_path / "gone" / "sess"
    session.mkdir()
    capture._write_meta(
        session / capture.CAPTURE_META, missing_staging, True,
        [{"name": "loopback", "pcm": capture.LOOPBACK_PCM, "rate": 48000, "channels": 2, "width": 2}],
    )
    assert capture.prune_stale_manifest(session) is True
    assert not (session / capture.CAPTURE_META).exists()
    # A recoverable session is NOT pruned.
    session2 = tmp_path / "sess2"
    staging2 = tmp_path / "stage2" / "sess2"
    _simulate_capture(session2, staging2, keep_audio=True)
    assert capture.prune_stale_manifest(session2) is False
    assert (session2 / capture.CAPTURE_META).is_file()


def test_torn_final_frame_tolerated(tmp_path):
    session = tmp_path / "sess"
    staging = tmp_path / "stage" / "sess"
    _simulate_capture(session, staging, keep_audio=True, loop_secs=4, mic_secs=4)
    p = staging / capture.LOOPBACK_PCM
    p.write_bytes(p.read_bytes() + b"\x01")  # half-written sample after a crash
    capture._finalize_mix(session / capture.CAPTURE_META, session / "audio.wav")
    assert abs(_wav_duration(session / "audio.wav") - 4.0) < 0.2


def test_resume_is_incomplete_for_part_only_session(tmp_path):
    from teamscribe import resume
    session = tmp_path / "sess"
    staging = tmp_path / "stage" / "sess"
    _simulate_capture(session, staging, keep_audio=False)
    assert resume.is_incomplete(session) is True
