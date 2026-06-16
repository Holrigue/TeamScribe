"""Summarize a meeting transcript into structured bullet points with Claude.

Output JSON shape:

    {
      "sujets":    ["...", "..."],
      "decisions": ["...", "..."],
      "actions":   [{"description": "...", "responsable": "...", "echeance": "..."}]
    }

Long transcripts (> ~15 000 words) are split into chunks; each chunk is
summarized, then a final synthesis pass merges them so nothing is lost.
"""

from __future__ import annotations

import json
from pathlib import Path

import anthropic

from . import config

WORD_CHUNK = 15_000

# JSON schema enforced via structured outputs (output_config.format).
_SCHEMA = {
    "type": "object",
    "properties": {
        "sujets": {
            "type": "array",
            "items": {"type": "string"},
        },
        "decisions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "responsable": {"type": "string"},
                    "echeance": {"type": "string"},
                },
                "required": ["description", "responsable", "echeance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sujets", "decisions", "actions"],
    "additionalProperties": False,
}

_SYSTEM = (
    "Tu es un assistant qui analyse des transcriptions de réunions Microsoft "
    "Teams en français québécois. Tu extrais fidèlement, sans inventer : les "
    "sujets discutés, les décisions prises, et les actions à faire. Pour "
    "chaque action, identifie le responsable et l'échéance UNIQUEMENT s'ils "
    "sont mentionnés dans la discussion ; sinon laisse une chaîne vide. "
    "Repère en particulier les actions formulées par « il faut que… », "
    "« je vais… », « on devrait… », « tu pourrais… ». Réponds toujours en "
    "français."
)

_CHUNK_INSTR = (
    "Voici une PARTIE d'une transcription de réunion. Résume cette partie "
    "selon le schéma demandé."
)

_FINAL_INSTR = (
    "Voici plusieurs résumés partiels d'une même réunion, en JSON. Fusionne-les "
    "en un seul résumé cohérent selon le schéma demandé : déduplique les sujets "
    "et décisions, et regroupe les actions identiques."
)


def _client() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY from the environment (loaded from .env).
    config.require_env("ANTHROPIC_API_KEY")
    return anthropic.Anthropic()


def _extract_json(response) -> dict:
    """output_config.format guarantees the first text block is valid JSON."""
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError("Claude returned no text content for the summary.")
    return json.loads(text)


def _summarize_text(client, model: str, instruction: str, payload: str) -> dict:
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": f"{instruction}\n\n<<<\n{payload}\n>>>",
            }
        ],
    )
    return _extract_json(response)


def _chunk_words(text: str, size: int) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [text]
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)]


def summarize_transcript(transcript: str, *, log=print) -> dict:
    """Summarize a transcript string into the structured dict."""
    client = _client()
    model = config.summary_model()

    chunks = _chunk_words(transcript, WORD_CHUNK)
    if len(chunks) == 1:
        log(f"Summarizing with {model}…")
        return _summarize_text(client, model, _CHUNK_INSTR, chunks[0])

    log(f"Transcript is long; summarizing in {len(chunks)} chunks with {model}…")
    partials = []
    for i, chunk in enumerate(chunks, 1):
        log(f"  chunk {i}/{len(chunks)}…")
        partials.append(_summarize_text(client, model, _CHUNK_INSTR, chunk))

    log("Synthesizing final summary…")
    merged_payload = json.dumps(partials, ensure_ascii=False, indent=2)
    return _summarize_text(client, model, _FINAL_INSTR, merged_payload)


def _to_markdown(summary: dict) -> str:
    lines = ["# Résumé de la réunion", ""]

    lines.append("## Sujets discutés")
    for s in summary.get("sujets", []) or ["(aucun)"]:
        lines.append(f"- {s}")
    lines.append("")

    lines.append("## Décisions prises")
    for d in summary.get("decisions", []) or ["(aucune)"]:
        lines.append(f"- {d}")
    lines.append("")

    lines.append("## Actions à faire")
    actions = summary.get("actions", [])
    if not actions:
        lines.append("- (aucune)")
    for a in actions:
        desc = a.get("description", "").strip()
        resp = a.get("responsable", "").strip()
        ech = a.get("echeance", "").strip()
        suffix = []
        if resp:
            suffix.append(f"**{resp}**")
        if ech:
            suffix.append(f"_échéance : {ech}_")
        tail = f" ({' — '.join(suffix)})" if suffix else ""
        lines.append(f"- {desc}{tail}")
    lines.append("")
    return "\n".join(lines)


def summarize_session(session_path: Path, *, log=print) -> dict:
    """Summarize a session's transcript.txt, writing summary.json / summary.md."""
    from .transcribe import load_transcript

    session_path = Path(session_path)
    transcript = load_transcript(session_path)
    summary = summarize_transcript(transcript, log=log)

    (session_path / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (session_path / "summary.md").write_text(_to_markdown(summary), encoding="utf-8")
    n = len(summary.get("actions", []))
    log(f"Wrote summary.json and summary.md ({n} action(s) identified).")
    return summary
