"""Derive a short, human-readable session name from transcript context.

Used to rename a freshly-recorded session's audio/notes files from a bare
timestamp to something browsable in Explorer, e.g.
``budget-q3-discussion_16-06-2026_14-30-00.wav`` instead of
``20260616_143000/audio.wav``.

Tries a short Claude call for a real title; falls back to a local
keyword-frequency heuristic (no API needed) if Claude is unavailable —
e.g. while ANTHROPIC_API_KEY has no credit — so renaming still works.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import config

_TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")

_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "que",
    "qui", "quoi", "est", "ce", "cette", "ces", "on", "il", "elle", "je",
    "tu", "vous", "nous", "ils", "elles", "pas", "ne", "se", "sa", "son",
    "ses", "leur", "leurs", "à", "au", "aux", "en", "dans", "pour", "avec",
    "sur", "donc", "alors", "ben", "euh", "comme", "mais", "plus", "fait",
    "faire", "tout", "tous", "toute", "très", "bien", "y", "là", "ça",
    "être", "avoir", "ok", "oui", "non",
}


def slugify(text: str, max_words: int = 5) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    words = [w for w in words if w not in _STOPWORDS and len(w) > 1]
    words = words[:max_words] or ["reunion"]
    return "-".join(words)


def _heuristic_slug(transcript: str) -> str:
    """No-API fallback: pick the most frequent meaningful words."""
    text = unicodedata.normalize("NFKD", transcript)
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9']+", text.lower())
    counts: dict[str, int] = {}
    for w in words:
        w = w.strip("'")
        if len(w) < 3 or w in _STOPWORDS:
            continue
        counts[w] = counts.get(w, 0) + 1
    top = sorted(counts, key=lambda w: counts[w], reverse=True)[:4]
    return slugify(" ".join(top)) if top else "reunion"


_TITLE_PROMPT = (
    "Tu donnes un titre très court (3 à 5 mots, sans ponctuation, "
    "en français) qui résume le sujet principal d'un extrait de "
    "réunion. Réponds uniquement avec le titre, rien d'autre."
)


def _llm_slug(transcript: str) -> str | None:
    snippet = " ".join(transcript.split()[:800])
    if not snippet.strip():
        return None
    try:
        if config.llm_provider() == "openai":
            import openai

            config.require_env("OPENAI_API_KEY")
            client = openai.OpenAI(timeout=30.0)
            response = client.chat.completions.create(
                model=config.openai_model(),
                max_tokens=30,
                messages=[
                    {"role": "system", "content": _TITLE_PROMPT},
                    {"role": "user", "content": snippet},
                ],
            )
            text = response.choices[0].message.content or ""
            return text.strip() or None

        import anthropic

        config.require_env("ANTHROPIC_API_KEY")
        client = anthropic.Anthropic(timeout=30.0)
        response = client.messages.create(
            model=config.summary_model(),
            max_tokens=30,
            system=_TITLE_PROMPT,
            messages=[{"role": "user", "content": snippet}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return text.strip() or None
    except Exception:
        return None


def derive_slug(transcript: str) -> str:
    """Best-effort short slug: the configured LLM if available, else a
    local heuristic that needs no API access."""
    if not transcript.strip():
        return "reunion"
    title = _llm_slug(transcript)
    if title:
        return slugify(title)
    return _heuristic_slug(transcript)


def finalize_session(session_dir: Path, transcript: str, *, log=print) -> Path:
    """Rename a freshly-recorded session's audio/notes files using context.

    Final name: ``<contexte>_<jour-mois-année>_<heure>``, e.g.
    ``budget-marketing-trimestre_16-06-2026_14-30-00``.

    Idempotent: a session whose folder name no longer matches the bare
    ``YYYYMMDD_HHMMSS`` pattern is assumed already finalized and left as is.
    transcript.txt/json and summary.json keep their fixed names since other
    code (push-tasks, re-summarize, the ✓ indicator) depends on finding them
    there; only the audio and the human-readable notes file get the
    contextual name.
    """
    session_dir = Path(session_dir)
    if not _TIMESTAMP_RE.match(session_dir.name):
        return session_dir

    slug = derive_slug(transcript)
    dt = datetime.strptime(session_dir.name, "%Y%m%d_%H%M%S")
    new_name = f"{slug}_{dt.strftime('%d-%m-%Y')}_{dt.strftime('%H-%M-%S')}"

    audio = session_dir / "audio.wav"
    if audio.is_file():
        audio.rename(session_dir / f"{new_name}.wav")

    notes_md = session_dir / "summary.md"
    if notes_md.is_file():
        notes_md.rename(session_dir / f"{new_name}_notes.md")

    new_dir = session_dir.parent / new_name
    suffix = 2
    while new_dir.exists():
        new_dir = session_dir.parent / f"{new_name}-{suffix}"
        suffix += 1

    session_dir.rename(new_dir)
    log(f"Session renamed: {new_dir.name}")
    return new_dir
