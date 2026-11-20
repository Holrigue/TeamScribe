"""Finish a session whose processing was interrupted.

A normally-recorded session goes through four stages:

    capture  ->  transcribe  ->  summarize  ->  finalize (contextual rename)

If TeamScribe is closed or crashes between the end of the recording and the
rename — for example a native ``faster-whisper`` fault during transcription,
as happened on 2026-11-11 — the folder is left as a bare ``YYYYMMDD_HHMMSS/``
holding only its ``audio.wav`` (or an already-renamed ``<slug>.wav`` if the
quick-title rename ran first) and no ``transcript.txt``. The recording itself
is safe on disk; only the post-capture processing never completed.

This module re-runs that post-capture half of the pipeline on the saved audio,
producing the transcript, the summary/notes and the contextual rename, so an
interrupted meeting can be recovered without re-recording anything. It is
deliberately free of any GUI/Qt import so the exact same code path is used by
the desktop widget's "Finish processing" action, the ``teamscribe reprocess``
CLI command, and the tests.
"""

from __future__ import annotations

from pathlib import Path


def find_session_audio(session: Path) -> Path | None:
    """Return the session's audio file, or ``None`` if there is none.

    Prefers the canonical ``audio.wav`` (a session interrupted before the
    rename), then falls back to any ``*.wav`` (a session whose quick-title
    rename ran before the crash, leaving ``<slug>.wav``).
    """
    session = Path(session)
    primary = session / "audio.wav"
    if primary.is_file():
        return primary
    others = sorted(p for p in session.glob("*.wav") if p.is_file())
    return others[0] if others else None


def has_transcript(session: Path) -> bool:
    """True once the transcript has been produced for this session."""
    return (Path(session) / "transcript.txt").is_file()


def is_incomplete(session: Path) -> bool:
    """True when a session has saved audio but no transcript.

    That is the signature of a recording whose processing was interrupted:
    either the audio was written to disk but ``transcribe`` never finished, or
    the recording itself was cut short (app closed, OOM crash, power loss) and
    only the raw capture parts remain, never mixed to ``audio.wav``. Both can
    (and should) be reprocessed. A session with no audio at all (e.g. privacy
    mode deleted it after a successful transcription) is *not* incomplete —
    there is nothing left to reprocess.
    """
    from . import capture

    session = Path(session)
    if not session.is_dir():
        return False
    if has_transcript(session):
        return False
    return find_session_audio(session) is not None or capture.has_recoverable_parts(session)


def reprocess_session(
    session: Path,
    *,
    do_summarize: bool = True,
    keep_audio: bool = True,
    log=print,
    on_phase=None,
) -> Path:
    """Re-run transcribe -> (summarize) -> finalize on an existing session.

    Reuses the audio already recorded in ``session`` instead of recording
    again. Writes ``transcript.txt``/``transcript.json`` and, when
    ``do_summarize`` is set, ``summary.json``/``.md``/``.txt``; then renames
    the folder and audio from the bare timestamp to the contextual name, the
    same way a freshly-recorded session is finalized.

    ``on_phase`` (optional) is called with ``"transcribing"``,
    ``"summarizing"`` and ``"saving"`` so a UI can show progress. The summary
    step is best-effort: if it fails (no API key, no credit, offline…) the
    transcript is still saved and the session still finalized, and the error
    is logged rather than raised — re-running it later recovers the summary.

    Returns the session's final path (it changes when the folder is renamed).
    Raises :class:`FileNotFoundError` if the session holds no audio to process.
    """
    from . import capture, naming, summarize as summarize_mod, transcribe

    session = Path(session)
    audio_path = find_session_audio(session)
    if audio_path is None:
        # A recording interrupted *during* capture leaves raw PCM parts but no
        # mixed WAV. Rebuild audio.wav from them before transcribing.
        audio_path = capture.recover_session(session)
    if audio_path is None:
        raise FileNotFoundError(
            f"No audio file (*.wav) in {session}; nothing to reprocess."
        )

    def phase(name: str) -> None:
        if on_phase is not None:
            on_phase(name)

    phase("transcribing")
    result = transcribe.transcribe(audio_path, session, log=log)

    if not keep_audio:
        # Privacy mode: the transcript is saved, so the raw audio is no
        # longer needed — mirror the recording pipeline and drop it now
        # (before finalize would otherwise rename it).
        try:
            audio_path.unlink()
        except OSError:
            pass

    if do_summarize:
        try:
            phase("summarizing")
            summarize_mod.summarize_session(session, log=log)
        except Exception as exc:  # noqa: BLE001 - best-effort, like the record path
            log(f"Summary skipped: {exc}")

    phase("saving")
    session = naming.finalize_session(session, result["text"], log=log)
    return session
