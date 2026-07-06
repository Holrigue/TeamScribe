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
from .i18n import tr

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

# English names, keyed by UI language code — used to steer the LLM's output
# language explicitly. English instructions are more reliably followed by
# most models regardless of target language than instructions written in
# that target language itself.
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "de": "German",
    "it": "Italian",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "hi": "Hindi",
    "ar": "Arabic",
    "bn": "Bengali",
    "ur": "Urdu",
}


def _system_prompt(lang: str) -> str:
    language = _LANGUAGE_NAMES.get(lang, "French")
    return (
        "You are an assistant analyzing Microsoft Teams meeting transcripts. "
        "Faithfully extract, without inventing anything: the topics "
        "discussed, the decisions made, and the action items. For each "
        "action item, identify the owner and the due date ONLY if they are "
        "mentioned in the discussion; otherwise leave an empty string. Watch "
        "especially for action items phrased as \"we need to...\", \"I "
        "will...\", \"we should...\", \"could you...\" (in whichever "
        "language the transcript itself is in). "
        "Keep the JSON field names exactly as specified in the schema "
        "(sujets, decisions, actions, description, responsable, echeance) "
        f"but write every text value in {language}, regardless of the "
        "transcript's own language."
    )


_CHUNK_INSTR = (
    "Here is ONE PART of a meeting transcript. Summarize this part "
    "according to the requested schema."
)

_FINAL_INSTR = (
    "Here are several partial summaries of the same meeting, as JSON. Merge "
    "them into a single coherent summary following the requested schema: "
    "deduplicate topics and decisions, and group identical action items."
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


def _summarize_with_anthropic(client, model: str, system: str, instruction: str, payload: str) -> dict:
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": f"{instruction}\n\n<<<\n{payload}\n>>>",
            }
        ],
    )
    return _extract_json_anthropic(response)


def _summarize_with_openai(client, model: str, system: str, instruction: str, payload: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        max_tokens=8000,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "meeting_summary", "schema": _SCHEMA, "strict": True},
        },
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"{instruction}\n\n<<<\n{payload}\n>>>"},
        ],
    )
    text = response.choices[0].message.content
    if text is None:
        raise RuntimeError("OpenAI returned no content for the summary.")
    return json.loads(text)


# Azure OpenAI uses the same SDK and wire format as OpenAI.
_summarize_with_azure_openai = _summarize_with_openai


def _summarize_with_gemini(client, model: str, system: str, instruction: str, payload: str) -> dict:
    import google.generativeai as genai

    generation_config = genai.GenerationConfig(
        response_mime_type="application/json",
        response_schema=_SCHEMA,
        max_output_tokens=8000,
    )
    prompt = f"{system}\n\n{instruction}\n\n<<<\n{payload}\n>>>"
    response = client.generate_content(prompt, generation_config=generation_config)
    text = response.text
    if not text:
        raise RuntimeError("Gemini returned no content for the summary.")
    return json.loads(text)


def _summarize_text(
    provider: str, client, model: str, system: str, instruction: str, payload: str
) -> dict:
    if provider == "openai":
        return _summarize_with_openai(client, model, system, instruction, payload)
    if provider == "azure_openai":
        return _summarize_with_azure_openai(client, model, system, instruction, payload)
    if provider == "gemini":
        return _summarize_with_gemini(client, model, system, instruction, payload)
    return _summarize_with_anthropic(client, model, system, instruction, payload)


def _chunk_words(text: str, size: int) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [text]
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)]


def summarize_transcript(transcript: str, *, lang: str | None = None, log=print) -> dict:
    """Summarize a transcript string into the structured dict, in ``lang``
    (the UI language code) — defaults to the app's configured UI language."""
    provider, client, model = _client_and_model()
    system = _system_prompt(lang or config.ui_language())

    chunks = _chunk_words(transcript, WORD_CHUNK)
    if len(chunks) == 1:
        log(f"Summarizing with {model}…")
        return _summarize_text(provider, client, model, system, _CHUNK_INSTR, chunks[0])

    log(f"Transcript is long; summarizing in {len(chunks)} chunks with {model}…")
    partials = []
    for i, chunk in enumerate(chunks, 1):
        log(f"  chunk {i}/{len(chunks)}…")
        partials.append(_summarize_text(provider, client, model, system, _CHUNK_INSTR, chunk))

    log("Synthesizing final summary…")
    merged_payload = json.dumps(partials, ensure_ascii=False, indent=2)
    return _summarize_text(provider, client, model, system, _FINAL_INSTR, merged_payload)


def _to_markdown(summary: dict, lang: str = "fr") -> str:
    lines = [f"# {tr('summary_title', lang)}", ""]
    none = tr("summary_none", lang)

    lines.append(f"## {tr('summary_topics', lang)}")
    for s in summary.get("sujets", []) or [none]:
        lines.append(f"- {s}")
    lines.append("")

    lines.append(f"## {tr('summary_decisions', lang)}")
    for d in summary.get("decisions", []) or [none]:
        lines.append(f"- {d}")
    lines.append("")

    lines.append(f"## {tr('summary_actions', lang)}")
    actions = summary.get("actions", [])
    if not actions:
        lines.append(f"- {none}")
    for a in actions:
        desc = a.get("description", "").strip()
        resp = a.get("responsable", "").strip()
        ech = a.get("echeance", "").strip()
        suffix = []
        if resp:
            suffix.append(f"**{resp}**")
        if ech:
            suffix.append(f"_{tr('summary_due', lang)} : {ech}_")
        tail = f" ({' — '.join(suffix)})" if suffix else ""
        lines.append(f"- {desc}{tail}")
    lines.append("")
    return "\n".join(lines)


def _to_plain_text(summary: dict, lang: str = "fr") -> str:
    none = tr("summary_none", lang)

    title = tr("summary_title", lang).upper()
    lines = [title, "=" * len(title), ""]

    topics = tr("summary_topics", lang).upper()
    lines.append(topics)
    lines.append("-" * len(topics))
    for s in summary.get("sujets", []) or [none]:
        lines.append(f"  - {s}")
    lines.append("")

    decisions = tr("summary_decisions", lang).upper()
    lines.append(decisions)
    lines.append("-" * len(decisions))
    for d in summary.get("decisions", []) or [none]:
        lines.append(f"  - {d}")
    lines.append("")

    actions_title = tr("summary_actions", lang).upper()
    lines.append(actions_title)
    lines.append("-" * len(actions_title))
    actions = summary.get("actions", [])
    if not actions:
        lines.append(f"  - {none}")
    for a in actions:
        desc = a.get("description", "").strip()
        resp = a.get("responsable", "").strip()
        ech = a.get("echeance", "").strip()
        suffix = []
        if resp:
            suffix.append(f"{tr('summary_responsible', lang)} : {resp}")
        if ech:
            suffix.append(f"{tr('summary_due', lang)} : {ech}")
        tail = f" ({', '.join(suffix)})" if suffix else ""
        lines.append(f"  - {desc}{tail}")
    lines.append("")
    return "\n".join(lines)


def summarize_session(session_path: Path, *, log=print) -> dict:
    """Summarize a session's transcript.txt, writing summary.json / summary.md / summary.txt."""
    from .transcribe import load_transcript

    session_path = Path(session_path)
    transcript = load_transcript(session_path)
    lang = config.ui_language()
    summary = summarize_transcript(transcript, lang=lang, log=log)

    (session_path / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (session_path / "summary.md").write_text(_to_markdown(summary, lang), encoding="utf-8")
    (session_path / "summary.txt").write_text(_to_plain_text(summary, lang), encoding="utf-8")
    n = len(summary.get("actions", []))
    log(f"Wrote summary.json, summary.md and summary.txt ({n} action(s) identified).")
    return summary
