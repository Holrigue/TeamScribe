"""Tests for teamscribe.resume (finishing an interrupted session).

The heavy stages (faster-whisper transcription, the LLM summary) are patched
out: these tests check the recovery *orchestration* — audio discovery, the
interrupted-state detection, phase order, best-effort summary, and the final
rename — not the models themselves.
"""

from __future__ import annotations

import json
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from teamscribe import resume


def _make_session(parent: Path, name: str = "20261111_120124", audio: str = "audio.wav") -> Path:
    session = parent / name
    session.mkdir()
    if audio:
        with wave.open(str(session / audio), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000)  # 1s of silence
    return session


class FindAudioTests(unittest.TestCase):
    def test_prefers_audio_wav(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp))
            self.assertEqual(resume.find_session_audio(session).name, "audio.wav")

    def test_falls_back_to_slug_wav(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp), audio="budget-reunion.wav")
            self.assertEqual(resume.find_session_audio(session).name, "budget-reunion.wav")

    def test_none_when_no_audio(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp), audio="")
            self.assertIsNone(resume.find_session_audio(session))


class IncompleteTests(unittest.TestCase):
    def test_audio_without_transcript_is_incomplete(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp))
            self.assertTrue(resume.is_incomplete(session))

    def test_audio_with_transcript_is_complete(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp))
            (session / "transcript.txt").write_text("hello", encoding="utf-8")
            self.assertFalse(resume.is_incomplete(session))

    def test_no_audio_is_not_incomplete(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp), audio="")
            self.assertFalse(resume.is_incomplete(session))


class ReprocessTests(unittest.TestCase):
    def _fake_transcribe(self, audio_path, out_dir, *, log=print):
        out_dir = Path(out_dir)
        (out_dir / "transcript.txt").write_text("le budget marketing du trimestre", encoding="utf-8")
        (out_dir / "transcript.json").write_text("{}", encoding="utf-8")
        return {"text": "le budget marketing du trimestre", "segments": [], "language": "fr", "duration": 1.0}

    def _fake_summarize(self, session_path, *, log=print):
        summary = {"sujets": ["budget"], "decisions": [], "actions": []}
        (Path(session_path) / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (Path(session_path) / "summary.md").write_text("# Résumé", encoding="utf-8")
        (Path(session_path) / "summary.txt").write_text("RESUME", encoding="utf-8")
        return summary

    def test_full_pipeline_renames_and_orders_phases(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp))
            phases: list[str] = []
            with mock.patch("teamscribe.transcribe.transcribe", side_effect=self._fake_transcribe), \
                 mock.patch("teamscribe.summarize.summarize_session", side_effect=self._fake_summarize), \
                 mock.patch("teamscribe.naming.derive_slug", return_value="budget-marketing"):
                final = resume.reprocess_session(
                    session, do_summarize=True, log=lambda *a: None, on_phase=phases.append
                )

            self.assertEqual(phases, ["transcribing", "summarizing", "saving"])
            # Folder renamed off the bare timestamp, audio + notes renamed too.
            self.assertFalse(session.exists())
            self.assertTrue(final.name.startswith("budget-marketing_"))
            self.assertTrue((final / "summary.json").is_file())
            self.assertTrue((final / f"{final.name}.wav").is_file())
            self.assertTrue((final / f"{final.name}_notes.md").is_file())

    def test_summary_failure_still_saves_transcript_and_renames(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp))
            with mock.patch("teamscribe.transcribe.transcribe", side_effect=self._fake_transcribe), \
                 mock.patch("teamscribe.summarize.summarize_session", side_effect=RuntimeError("no credit")), \
                 mock.patch("teamscribe.naming.derive_slug", return_value="budget-marketing"):
                final = resume.reprocess_session(session, do_summarize=True, log=lambda *a: None)

            self.assertTrue((final / "transcript.txt").is_file())
            self.assertFalse((final / "summary.json").is_file())  # summary failed, not fatal
            self.assertTrue(final.name.startswith("budget-marketing_"))

    def test_no_audio_raises(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp), audio="")
            with self.assertRaises(FileNotFoundError):
                resume.reprocess_session(session, log=lambda *a: None)

    def test_privacy_mode_deletes_audio(self):
        with TemporaryDirectory() as tmp:
            session = _make_session(Path(tmp))
            with mock.patch("teamscribe.transcribe.transcribe", side_effect=self._fake_transcribe), \
                 mock.patch("teamscribe.naming.derive_slug", return_value="budget"):
                final = resume.reprocess_session(
                    session, do_summarize=False, keep_audio=False, log=lambda *a: None
                )
            self.assertEqual(list(final.glob("*.wav")), [])


if __name__ == "__main__":
    unittest.main()
