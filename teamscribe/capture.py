"""Dual-stream audio capture for Windows 11 using PyAudioWPatch.

We capture two streams simultaneously:

  * the WASAPI **loopback** of the default speakers — i.e. everything the other
    meeting participants say (what plays out of your speakers), and
  * the default **microphone** — your own voice.

Both are recorded at their native device rates in background threads and
**streamed straight to disk** as raw PCM, then mixed down to a single 16 kHz
mono WAV on stop. WASAPI loopback needs no "Stereo Mix", no signed driver, and
no admin elevation.

Why stream to disk instead of buffering in RAM
-----------------------------------------------
An earlier version kept every captured frame of both streams in memory and only
wrote the WAV at the very end. For a long meeting (~45 min+) that buffer grew to
~1 GB of raw bytes, and the stop-time conversion (join → int16→float32 → polyphase
resample → mix) multiplied it to several GB of peak allocation on top of the
resident Whisper model. On a typical work PC that exhausted memory and crashed
the process (``mkl_malloc: failed to allocate memory`` / a native access
violation) **before the WAV was ever written — losing the whole recording.**

Now each stream is appended to its own raw ``.pcm`` file as it is captured, so:

  * memory stays flat and bounded no matter how long the meeting runs, and
  * if the process dies mid-session the already-captured audio is safe on disk
    and can be recovered (see :func:`recover_session`), because a raw PCM file's
    length is simply its byte count — there is no header to finalize.

The final mix/resample is done **block by block** streamed from those files to
the output WAV, so peak memory there is a few MB, not a few GB.

The actual capture only runs on Windows (PyAudioWPatch is Windows-only). The
module imports lazily so the rest of TeamScribe (transcribe/summarize/planner)
can be used on any platform.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from . import config

TARGET_RATE = 16_000  # faster-whisper expects 16 kHz mono
_CHUNK = 1024
_SAMPLE_WIDTH = 2  # paInt16 — both streams are opened as 16-bit (see open()).
_FLUSH_EVERY = 64  # flush the PCM file to the OS roughly every ~64 chunks
_MIX_BLOCK_SECONDS = 30.0  # mix/resample this many seconds at a time on stop

# While a recording is in progress, the raw per-stream PCM is streamed to a
# machine-local staging directory (see config.staging_dir()), OUTSIDE the
# session folder, so it never syncs to a cloud-synced project folder and privacy
# mode leaves nothing behind. A tiny JSON manifest — no audio, just how to find
# and decode those parts, plus the privacy flag — is written IN the session
# folder so an interrupted capture can be discovered and recovered (or purged)
# at next launch. The manifest's name does not end in ``.wav`` so the ``*.wav``
# globs elsewhere (quick_rename, finalize, resume, CLI) never pick it up. All of
# it is removed once ``audio.wav`` has been written (or the capture discarded).
LOOPBACK_PCM = "audio.loopback.pcm"
MIC_PCM = "audio.mic.pcm"
CAPTURE_META = "audio.capture.json"


@dataclass
class _StreamRecorder:
    """Reads one PyAudio stream in a thread and appends raw frames to disk."""

    name: str
    stream: object  # pyaudio.Stream
    rate: int
    channels: int
    pcm_path: Path
    sample_width: int = _SAMPLE_WIDTH  # bytes per sample
    _thread: threading.Thread | None = None
    _running: bool = False
    _file: object | None = None
    frames_written: int = 0

    def start(self) -> None:
        self.pcm_path = Path(self.pcm_path)
        self.pcm_path.parent.mkdir(parents=True, exist_ok=True)
        # Large buffer: we want sequential disk writes, not a syscall per chunk.
        self._file = open(self.pcm_path, "wb", buffering=1024 * 1024)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        # A WASAPI loopback stream delivers nothing while nothing is playing,
        # so captured frames alone would drop every silence and drift ahead of
        # the mic. Pad those gaps from the wall clock to keep both tracks on the
        # same timeline.
        bytes_per_frame = self.channels * self.sample_width
        written = 0
        started = time.monotonic()
        i = 0
        while self._running:
            try:
                data = self.stream.read(_CHUNK, exception_on_overflow=False)
            except Exception:
                # An overflow or transient device hiccup: keep going rather
                # than killing the whole recording.
                continue
            got = len(data) // bytes_per_frame
            missing = int((time.monotonic() - started) * self.rate) - written - got
            if missing > 2 * _CHUNK:
                self._file.write(b"\x00" * (missing * bytes_per_frame))
                written += missing
            self._file.write(data)
            written += got
            i += 1
            if i % _FLUSH_EVERY == 0:
                # Push to the OS page cache periodically so a process crash
                # (OOM / native fault) still leaves the captured audio on disk.
                # We don't fsync — surviving a process crash needs only a flush,
                # and fsync on every block would stall the reader.
                self._file.flush()
        self.frames_written = written

    def stop(self) -> None:
        self._running = False
        # stop_stream() must happen *before* we join: the reader thread is
        # blocked inside a blocking stream.read() call, and stop_stream()
        # is what makes that pending read return. If we instead joined
        # first (with a timeout) and then called close() regardless, a
        # slow-to-return read could still be in flight inside PortAudio
        # when close() frees the underlying WASAPI buffers — a
        # use-after-free that crashes the whole process (SIGSEGV), not a
        # catchable Python exception.
        try:
            self.stream.stop_stream()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        try:
            self.stream.close()
        except Exception:
            pass
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            self._file = None


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


def _pcm_to_mono16k(raw: bytes, rate: int, channels: int) -> np.ndarray:
    """Decode raw int16 PCM frames -> float32 mono @ 16 kHz."""
    if not raw:
        return np.zeros(0, dtype=np.float32)
    # Guard against a torn final frame after a crash: int16 needs an even count.
    usable_bytes = (len(raw) // 2) * 2
    audio = np.frombuffer(raw[:usable_bytes], dtype=np.int16).astype(np.float32) / 32768.0

    if channels > 1:
        # Trim to a whole number of frames, then average channels.
        usable = (audio.size // channels) * channels
        audio = audio[:usable].reshape(-1, channels).mean(axis=1)

    if rate != TARGET_RATE:
        audio = resample_poly(audio, TARGET_RATE, rate)

    return audio.astype(np.float32)


def _write_wav(path: Path, mono16k: np.ndarray) -> None:
    pcm16 = np.clip(mono16k, -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm16.tobytes())


def _write_meta(meta_path: Path, staging: Path, keep_audio: bool, streams: list[dict]) -> None:
    """Write the capture manifest in the session folder.

    Written before capture starts so an interrupted session can be found and
    recovered: the raw ``.pcm`` files (in ``staging``, a machine-local dir) hold
    the audio, this manifest holds where they are, how to decode them, and
    whether the recording was made in privacy mode (``keep_audio`` False). The
    manifest itself contains no audio. ``staging`` is stored as an absolute path
    because it lives outside the session folder (and off any cloud sync); on a
    different machine it simply won't exist, which recovery treats as "nothing
    to recover here".
    """
    meta_path.write_text(
        json.dumps(
            {
                "target": "audio.wav",
                "staging": str(staging),
                "keep_audio": bool(keep_audio),
                "streams": streams,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_meta(meta_path: Path) -> tuple[Path, dict] | None:
    """Return (staging_base, manifest) for a capture manifest, or ``None``.

    ``staging_base`` is where the ``.pcm`` parts live: the manifest's stored
    staging path, or — for backward compatibility with the first version that
    kept parts next to the manifest — the manifest's own folder.
    """
    meta_path = Path(meta_path)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    base = Path(meta["staging"]) if meta.get("staging") else meta_path.parent
    return base, meta


def _finalize_mix(meta_path: Path, out_path: Path, block_seconds: float = _MIX_BLOCK_SECONDS) -> Path:
    """Stream the per-stream PCM parts into one mixed 16 kHz mono WAV.

    Reads both parts ``block_seconds`` of source audio at a time, downmixes and
    resamples each block to 16 kHz mono, overlays them (padding the shorter of
    the two, and continuing with silence once one stream ends), and appends the
    result to ``out_path``. Peak memory is one block per stream (a few MB), not
    the whole recording.

    A rare simultaneous-loud-peak may hard-clip at ±1 rather than being globally
    normalized as the old in-RAM path did; this is inaudible for the speech
    Whisper transcribes and keeps the mix single-pass.
    """
    meta_path = Path(meta_path)
    out_path = Path(out_path)
    read = _read_meta(meta_path)
    if read is None:
        _write_wav(out_path, np.zeros(0, dtype=np.float32))
        return out_path
    base, meta = read

    handles = []  # list of dicts: {fh, rate, channels, chunk_bytes}
    try:
        for st in meta.get("streams", []):
            pcm = base / st["pcm"]
            if not pcm.is_file() or pcm.stat().st_size == 0:
                continue
            rate = int(st["rate"])
            channels = int(st["channels"])
            width = int(st.get("width", _SAMPLE_WIDTH))
            handles.append(
                {
                    "fh": open(pcm, "rb"),
                    "rate": rate,
                    "channels": channels,
                    "chunk_bytes": int(rate * block_seconds) * channels * width,
                }
            )

        if not handles:
            # Nothing was captured — still write a valid (empty) WAV so the
            # rest of the pipeline has a file to point at.
            _write_wav(out_path, np.zeros(0, dtype=np.float32))
            return out_path

        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_RATE)
            while True:
                blocks = []
                any_data = False
                for h in handles:
                    raw = h["fh"].read(h["chunk_bytes"])
                    if raw:
                        any_data = True
                    blocks.append(_pcm_to_mono16k(raw, h["rate"], h["channels"]))
                if not any_data:
                    break
                length = max((b.size for b in blocks), default=0)
                if length == 0:
                    break
                mixed = np.zeros(length, dtype=np.float32)
                for b in blocks:
                    if b.size < length:
                        b = np.pad(b, (0, length - b.size))
                    mixed += b
                np.clip(mixed, -1.0, 1.0, out=mixed)
                wf.writeframes((mixed * 32767.0).astype(np.int16).tobytes())
    finally:
        for h in handles:
            try:
                h["fh"].close()
            except Exception:
                pass
    return out_path


def _cleanup_capture(session_dir: Path) -> None:
    """Remove all capture leftovers: the staging dir of parts and the manifest.

    Also clears any first-version parts that were written next to the manifest.
    """
    session_dir = Path(session_dir)
    meta_path = session_dir / CAPTURE_META
    read = _read_meta(meta_path)
    if read is not None:
        base, _meta = read
        if base != session_dir:
            try:
                shutil.rmtree(base)
            except OSError:
                pass
    for name in (LOOPBACK_PCM, MIC_PCM, CAPTURE_META):
        try:
            (session_dir / name).unlink()
        except OSError:
            pass


def _staged_parts_present(base: Path, meta: dict) -> bool:
    for st in meta.get("streams", []):
        pcm = base / st["pcm"]
        try:
            if pcm.is_file() and pcm.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def has_recoverable_parts(session_dir: Path) -> bool:
    """True if a session holds in-progress capture parts but no finished WAV.

    This is the signature of a recording whose *capture* (not just its
    post-capture processing) was interrupted — the raw PCM is on disk but was
    never mixed to ``audio.wav``. Returns False when the staging parts are not
    present on this machine (e.g. a manifest synced from another computer).
    """
    session_dir = Path(session_dir)
    if (session_dir / "audio.wav").is_file():
        return False
    read = _read_meta(session_dir / CAPTURE_META)
    if read is None:
        return False
    base, meta = read
    return _staged_parts_present(base, meta)


def capture_keep_audio(session_dir: Path) -> bool | None:
    """Return the privacy state recorded in the capture manifest.

    ``False`` means the recording was made in privacy mode (no audio kept);
    ``True`` means audio is kept; ``None`` if there is no manifest.
    """
    read = _read_meta(Path(session_dir) / CAPTURE_META)
    if read is None:
        return None
    return bool(read[1].get("keep_audio", True))


def recover_session(session_dir: Path) -> Path | None:
    """Rebuild ``audio.wav`` from leftover capture parts, or return ``None``.

    Used by the resume path so a meeting whose recording was interrupted (app
    closed, OOM crash, power loss) can still be transcribed from the raw audio
    that was streamed to disk. Removes the parts (and manifest) once the WAV is
    written.
    """
    session_dir = Path(session_dir)
    out_path = session_dir / "audio.wav"
    if out_path.is_file():
        return out_path
    if not has_recoverable_parts(session_dir):
        return None
    _finalize_mix(session_dir / CAPTURE_META, out_path)
    _cleanup_capture(session_dir)
    return out_path if out_path.is_file() else None


def discard_capture(session_dir: Path) -> None:
    """Permanently purge an interrupted capture's raw audio without recovering.

    Honors a privacy-mode user's intent when they decline to recover: the
    staging PCM parts and the manifest are deleted, leaving no raw audio behind.
    """
    _cleanup_capture(session_dir)


def prune_stale_manifest(session_dir: Path) -> bool:
    """Drop a capture manifest whose staged parts are gone and unrecoverable.

    A manifest can outlive its parts — e.g. it synced to another machine where
    the local staging dir never existed, or the parts were cleared. If there is
    also no ``audio.wav`` to recover from, the manifest is a dangling pointer;
    remove it so the session is no longer flagged as recoverable. Returns True
    if a manifest was removed.
    """
    session_dir = Path(session_dir)
    meta_path = session_dir / CAPTURE_META
    if not meta_path.is_file():
        return False
    if (session_dir / "audio.wav").is_file() or has_recoverable_parts(session_dir):
        return False
    try:
        meta_path.unlink()
        return True
    except OSError:
        return False


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
    import tempfile

    pyaudio = _pyaudio()
    p = pyaudio.PyAudio()
    recorders: list[_StreamRecorder] = []
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        try:
            loop_stream, loop_rate, loop_ch, speaker_name = _open_loopback(p, pyaudio)
            recorders.append(
                _StreamRecorder("loopback", loop_stream, loop_rate, loop_ch, td_path / "loop.pcm")
            )
            mic_stream, mic_rate, mic_ch, mic_name = _open_microphone(p, pyaudio)
            recorders.append(
                _StreamRecorder("microphone", mic_stream, mic_rate, mic_ch, td_path / "mic.pcm")
            )

            for r in recorders:
                r.start()
            time.sleep(seconds)
        finally:
            for r in recorders:
                r.stop()
            p.terminate()

        loopback = _pcm_to_mono16k((td_path / "loop.pcm").read_bytes(), loop_rate, loop_ch)
        mic = _pcm_to_mono16k((td_path / "mic.pcm").read_bytes(), mic_rate, mic_ch)

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


def record(
    out_path: Path,
    max_minutes: float,
    on_tick=None,
    stop_event: threading.Event | None = None,
    keep_audio: bool = True,
) -> Path:
    """Capture loopback + mic until Ctrl+C, ``stop_event``, or ``max_minutes``.

    Audio is streamed to per-stream ``.pcm`` files in a machine-local staging
    directory as it is captured (bounded memory, crash-recoverable, off any
    cloud-synced folder) and mixed to a single 16 kHz mono WAV at ``out_path``
    on stop.

    Args:
        out_path: where to write the mixed 16 kHz mono WAV.
        max_minutes: hard stop after this many minutes.
        on_tick: optional callable(elapsed_seconds, mic_name, speaker_name)
                 invoked roughly once a second for a live timer display.
        stop_event: optional threading.Event a caller can set() from another
                    thread to stop the recording (used by the GUI's Stop
                    button, since a non-console caller can't send Ctrl+C).
        keep_audio: recorded into the manifest so an interrupted privacy-mode
                    (``keep_audio=False``) capture can be surfaced for purging
                    at next launch instead of silently lingering.

    Returns the written WAV path.
    """
    pyaudio = _pyaudio()

    out_path = Path(out_path)
    session_dir = out_path.parent
    session_dir.mkdir(parents=True, exist_ok=True)

    # Raw parts go to a machine-local staging dir (outside the session folder,
    # so off OneDrive); the manifest stays in the session folder.
    staging = config.staging_dir() / session_dir.name
    staging.mkdir(parents=True, exist_ok=True)
    loop_pcm = staging / LOOPBACK_PCM
    mic_pcm = staging / MIC_PCM
    meta_path = session_dir / CAPTURE_META

    p = pyaudio.PyAudio()
    recorders: list[_StreamRecorder] = []
    try:
        loop_stream, loop_rate, loop_ch, speaker_name = _open_loopback(p, pyaudio)
        recorders.append(
            _StreamRecorder("loopback", loop_stream, loop_rate, loop_ch, loop_pcm)
        )
        mic_stream, mic_rate, mic_ch, mic_name = _open_microphone(p, pyaudio)
        recorders.append(
            _StreamRecorder("microphone", mic_stream, mic_rate, mic_ch, mic_pcm)
        )

        # Written before capture starts so a crash mid-recording leaves enough
        # on disk (parts + how to decode them + privacy flag) for
        # recover_session() to rebuild audio.wav, or for the launch-time prompt
        # to purge it. Order of streams here is the overlay order in the mix.
        _write_meta(
            meta_path,
            staging,
            keep_audio,
            [
                {"name": "loopback", "pcm": LOOPBACK_PCM, "rate": loop_rate,
                 "channels": loop_ch, "width": _SAMPLE_WIDTH},
                {"name": "microphone", "pcm": MIC_PCM, "rate": mic_rate,
                 "channels": mic_ch, "width": _SAMPLE_WIDTH},
            ],
        )

        for r in recorders:
            r.start()

        start = time.monotonic()
        deadline = start + max_minutes * 60.0
        try:
            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    break
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

    # Stream the captured parts into the final mixed WAV, then drop the staging
    # parts and the manifest.
    _finalize_mix(meta_path, out_path)
    _cleanup_capture(session_dir)
    return out_path
