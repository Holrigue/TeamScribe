"""Summarize a meeting transcript into structured bullet points with an LLM.

Supports four backends, selected via TEAMSCRIBE_LLM_PROVIDER in .env:
"anthropic" (default, Claude), "openai", "gemini", or "azure_openai".
Anthropic and OpenAI use native structured-output enforcement so the result
is always valid JSON matching the schema below — no manual parsing/repair
needed. Gemini uses response_mime_type + response_schema. Azure OpenAI reuses
the OpenAI SDK pointed at the user's Azure endpoint.

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


# Network calls get an explicit timeout so a stalled provider can't hang
# the recording pipeline indefinitely.
_REQUEST_TIMEOUT_S = 120.0


def _client_and_model() -> tuple[str, object, str]:
    """Return (provider, sdk_client, model) for the configured LLM backend.

    Each provider's API key is read only from the environment (loaded from
    the local, git-ignored .env) and never logged or persisted elsewhere.
    """
    provider = config.llm_provider()

    if provider == "openai":
        import openai
        config.require_env("OPENAI_API_KEY")
        client = openai.OpenAI(timeout=_REQUEST_TIMEOUT_S)
        return provider, client, config.openai_model()

    if provider == "azure_openai":
        import openai
        config.require_env("AZURE_OPENAI_API_KEY")
        client = openai.AzureOpenAI(
            api_key=config.require_env("AZURE_OPENAI_API_KEY"),
            azure_endpoint=config.require_env("AZURE_OPENAI_ENDPOINT"),
            api_version=config.env("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            timeout=_REQUEST_TIMEOUT_S,
        )
        return provider, client, config.env("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    if provider == "gemini":
        import google.generativeai as genai
        config.require_env("GEMINI_API_KEY")
        genai.configure(api_key=config.env("GEMINI_API_KEY"))
        model_name = config.env("TEAMSCRIBE_GEMINI_MODEL", "gemini-1.5-flash")
        client = genai.GenerativeModel(model_name)
        return provider, client, model_name

    import anthropic
    config.require_env("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(timeout=_REQUEST_TIMEOUT_S)
    return provider, client, config.summary_model()


def _extract_json_anthropic(response) -> dict:
    """output_config.format guarantees the first text block is valid JSON."""
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError("Claude returned no text content for the summary.")
    return json.loads(text)


def _summarize_with_anthropic(client, model: str, instruction: str, payload: str) -> dict:
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
    return _extract_json_anthropic(response)


def _summarize_with_openai(client, model: str, instruction: str, payload: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        max_tokens=8000,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "meeting_summary", "schema": _SCHEMA, "strict": True},
        },
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"{instruction}\n\n<<<\n{payload}\n>>>"},
        ],
    )
    text = response.choices[0].message.content
    if text is None:
        raise RuntimeError("OpenAI returned no content for the summary.")
    return json.loads(text)


# Azure OpenAI uses the same SDK and wire format as OpenAI.
_summarize_with_azure_openai = _summarize_with_openai


def _summarize_with_gemini(client, model: str, instruction: str, payload: str) -> dict:
    import google.generativeai as genai

    generation_config = genai.GenerationConfig(
        response_mime_type="application/json",
        response_schema=_SCHEMA,
        max_output_tokens=8000,
    )
    prompt = f"{_SYSTEM}\n\n{instruction}\n\n<<<\n{payload}\n>>>"
    response = client.generate_content(prompt, generation_config=generation_config)
    text = response.text
    if not text:
        raise RuntimeError("Gemini returned no content for the summary.")
    return json.loads(text)


def _summarize_text(provider: str, client, model: str, instruction: str, payload: str) -> dict:
    if provider == "openai":
        return _summarize_with_openai(client, model, instruction, payload)
    if provider == "azure_openai":
        return _summarize_with_azure_openai(client, model, instruction, payload)
    if provider == "gemini":
        return _summarize_with_gemini(client, model, instruction, payload)
    return _summarize_with_anthropic(client, model, instruction, payload)


def _chunk_words(text: str, size: int) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [text]
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)]


def summarize_transcript(transcript: str, *, log=print) -> dict:
    """Summarize a transcript string into the structured dict."""
    provider, client, model = _client_and_model()

    chunks = _chunk_words(transcript, WORD_CHUNK)
    if len(chunks) == 1:
        log(f"Summarizing with {model}…")
        return _summarize_text(provider, client, model, _CHUNK_INSTR, chunks[0])

    log(f"Transcript is long; summarizing in {len(chunks)} chunks with {model}…")
    partials = []
    for i, chunk in enumerate(chunks, 1):
        log(f"  chunk {i}/{len(chunks)}…")
        partials.append(_summarize_text(provider, client, model, _CHUNK_INSTR, chunk))

    log("Synthesizing final summary…")
    merged_payload = json.dumps(partials, ensure_ascii=False, indent=2)
    return _summarize_text(provider, client, model, _FINAL_INSTR, merged_payload)


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
