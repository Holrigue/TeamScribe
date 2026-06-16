"""Dual-stream audio capture for Windows 11 using PyAudioWPatch.

We capture two streams simultaneously:

  * the WASAPI **loopback** of the default speakers — i.e. everything the other
    meeting participants say (what plays out of your speakers), and
  * the default **microphone** — your own voice.

Both are recorded at their native device rates in background threads, then
mixed down to a single 16 kHz mono WAV on stop. WASAPI loopback needs no
"Stereo Mix", no signed driver, and no admin elevation.

The actual capture only runs on Windows (PyAudioWPatch is Windows-only). The
module imports lazily so the rest of TeamScribe (transcribe/summarize/planner)
can be used on any platform.
"""

from __future__ import annotations

import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

TARGET_RATE = 16_000  # faster-whisper expects 16 kHz mono
_CHUNK = 1024


@dataclass
class _StreamRecorder:
    """Reads one PyAudio stream in a thread and buffers raw frames."""

    name: str
    stream: object  # pyaudio.Stream
    rate: int
    channels: int
    sample_width: int  # bytes per sample
    _frames: list[bytes] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _running: bool = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            try:
                data = self.stream.read(_CHUNK, exception_on_overflow=False)
            except Exception:
                # An overflow or transient device hiccup: keep going rather
                # than killing the whole recording.
                continue
            self._frames.append(data)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass

    def to_mono_16k(self) -> np.ndarray:
        """Decode buffered int16 frames -> float32 mono @ 16 kHz."""
        if not self._frames:
            return np.zeros(0, dtype=np.float32)

        raw = b"".join(self._frames)
        # PyAudioWPatch loopback and default mic are paInt16 here (see open()).
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if self.channels > 1:
            # Trim to a whole number of frames, then average channels.
            usable = (audio.size // self.channels) * self.channels
            audio = audio[:usable].reshape(-1, self.channels).mean(axis=1)

        if self.rate != TARGET_RATE:
            audio = resample_poly(audio, TARGET_RATE, self.rate)

        return audio.astype(np.float32)


def _open_loopback(p, pyaudio):
    """Return an opened WASAPI loopback stream for the default speakers."""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

    if not speakers.get("isLoopbackDevice", False):
        # Find the loopback companion device for the default speakers.
        for loop in p.get_loopback_device_info_generator():
            if speakers["name"] in loop["name"]:
                speakers = loop
                break
        else:
            raise RuntimeError(
                "No WASAPI loopback device found for the default speakers. "
                "Make sure audio output is configured in Windows."
            )

    rate = int(speakers["defaultSampleRate"])
    channels = int(speakers["maxInputChannels"]) or 2
    stream = p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        frames_per_buffer=_CHUNK,
        input=True,
        input_device_index=speakers["index"],
    )
    return stream, rate, channels, speakers["name"]


def _open_microphone(p, pyaudio):
    """Return an opened stream for the default input device (your mic)."""
    info = p.get_default_input_device_info()
    rate = int(info["defaultSampleRate"])
    channels = min(int(info["maxInputChannels"]) or 1, 2)
    stream = p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        frames_per_buffer=_CHUNK,
        input=True,
        input_device_index=info["index"],
    )
    return stream, rate, channels, info["name"]


def _mix(mic: np.ndarray, loopback: np.ndarray) -> np.ndarray:
    """Overlay two mono float32 tracks, padding the shorter one with silence."""
    length = max(mic.size, loopback.size)
    if length == 0:
        return np.zeros(0, dtype=np.float32)
    a = np.pad(mic, (0, length - mic.size))
    b = np.pad(loopback, (0, length - loopback.size))
    mixed = a + b
    # Prevent clipping after summation.
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 1.0:
        mixed = mixed / peak
    return mixed.astype(np.float32)


def _write_wav(path: Path, mono16k: np.ndarray) -> None:
    pcm16 = np.clip(mono16k, -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm16.tobytes())


def _pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            "PyAudioWPatch is required and is Windows-only. "
            "Install it with `pip install --user PyAudioWPatch`."
        ) from exc
    return pyaudio


def _rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def list_devices() -> dict:
    """Return the default speakers (loopback), default mic, and all loopbacks.

    Used by `teamscribe devices` — opens no streams, just queries the host API.
    """
    pyaudio = _pyaudio()
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        mic = p.get_default_input_device_info()
        loopbacks = [
            {"name": d["name"], "index": d["index"], "rate": int(d["defaultSampleRate"])}
            for d in p.get_loopback_device_info_generator()
        ]
        return {
            "default_speakers": {
                "name": speakers["name"],
                "rate": int(speakers["defaultSampleRate"]),
            },
            "default_mic": {
                "name": mic["name"],
                "rate": int(mic["defaultSampleRate"]),
            },
            "loopback_devices": loopbacks,
        }
    finally:
        p.terminate()


def probe(seconds: float = 6.0) -> dict:
    """Record the loopback and mic SEPARATELY for ``seconds`` and report levels.

    Returns per-stream RMS/peak so each source can be confirmed independently
    (a single mixed file can't prove both contributed). Used by
    `teamscribe selftest`.
    """
    pyaudio = _pyaudio()
    p = pyaudio.PyAudio()
    recorders: list[_StreamRecorder] = []
    try:
        loop_stream, loop_rate, loop_ch, speaker_name = _open_loopback(p, pyaudio)
        recorders.append(_StreamRecorder("loopback", loop_stream, loop_rate, loop_ch, 2))
        mic_stream, mic_rate, mic_ch, mic_name = _open_microphone(p, pyaudio)
        recorders.append(_StreamRecorder("microphone", mic_stream, mic_rate, mic_ch, 2))

        for r in recorders:
            r.start()
        time.sleep(seconds)
    finally:
        for r in recorders:
            r.stop()
        p.terminate()

    loopback = recorders[0].to_mono_16k()
    mic = recorders[1].to_mono_16k()
    return {
        "seconds": seconds,
        "loopback": {
            "name": speaker_name,
            "rms": _rms(loopback),
            "peak": float(np.max(np.abs(loopback))) if loopback.size else 0.0,
            "samples": int(loopback.size),
        },
        "microphone": {
            "name": mic_name,
            "rms": _rms(mic),
            "peak": float(np.max(np.abs(mic))) if mic.size else 0.0,
            "samples": int(mic.size),
        },
    }


def record(out_path: Path, max_minutes: float, on_tick=None) -> Path:
    """Capture loopback + mic until Ctrl+C or ``max_minutes``.

    Args:
        out_path: where to write the mixed 16 kHz mono WAV.
        max_minutes: hard stop after this many minutes.
        on_tick: optional callable(elapsed_seconds, mic_name, speaker_name)
                 invoked roughly once a second for a live timer display.

    Returns the written WAV path.
    """
    pyaudio = _pyaudio()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    p = pyaudio.PyAudio()
    recorders: list[_StreamRecorder] = []
    try:
        loop_stream, loop_rate, loop_ch, speaker_name = _open_loopback(p, pyaudio)
        recorders.append(
            _StreamRecorder("loopback", loop_stream, loop_rate, loop_ch, 2)
        )
        mic_stream, mic_rate, mic_ch, mic_name = _open_microphone(p, pyaudio)
        recorders.append(
            _StreamRecorder("microphone", mic_stream, mic_rate, mic_ch, 2)
        )

        for r in recorders:
            r.start()

        start = time.monotonic()
        deadline = start + max_minutes * 60.0
        try:
            while time.monotonic() < deadline:
                elapsed = time.monotonic() - start
                if on_tick is not None:
                    on_tick(elapsed, mic_name, speaker_name)
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    finally:
        for r in recorders:
            r.stop()
        p.terminate()

    mixed = _mix(
        recorders[1].to_mono_16k() if len(recorders) > 1 else np.zeros(0, np.float32),
        recorders[0].to_mono_16k() if recorders else np.zeros(0, np.float32),
    )
    _write_wav(out_path, mixed)
    return out_path
