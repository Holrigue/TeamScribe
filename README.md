<p align="center"><img src="assets/logo.png" alt="TeamScribe logo" width="160"></p>

# TeamScribe

[![ko-fi](https://img.shields.io/badge/Support%20me%20on-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/gabrielhoule)

A Windows 11 tool that automates a 3-stage pipeline for your Microsoft Teams
meetings — available as a **desktop widget** (GUI) or a **command-line tool**.

1. **Record** — capture what comes out of your speakers (the other
   participants) *and* your microphone (your voice), mixed into a single
   16 kHz mono WAV, and transcribe it in **French (Québécois)**.
2. **Summarize** — split and summarize the discussion into bullet points
   (topics discussed, decisions, action items) with Claude.
3. **Push tasks** — create a Microsoft Planner task for each action item via
   the Microsoft Graph API (direct access — no Power Automate / Zapier).

> Warning: **Consent reminder.** Make sure all participants consent to being
> recorded, per your company policy and local law, before you start a
> recording. TeamScribe does not enforce this technically — it is on you.

---

## What's new in v2.0.0

- **Minimized mode** — collapse the widget to a slim title bar (down/up toggle);
  state is persisted so the widget reopens as you left it.
- **Mini record button** — record/stop button in the title bar lets you start/stop
  recording without expanding the widget.
- **Processing feedback** — after stopping a recording, each phase is shown
  in real time: *Transcribing audio... -> Summarizing... -> Saving...*
- **Auto-format before Planner push** — "Push to Planner" now auto-generates
  the local summary first if none exists, so formatted notes are always saved
  to disk even when Planner is unreachable.
- **Faster settings dialog** — Windows known-folder paths are cached after
  the first lookup; the Settings dialog opens instantly on every subsequent click.
- **Faster transcription start** — the Whisper model is pre-loaded at app
  startup so transcription begins immediately after stopping a recording.
- **Version number** shown in the Settings dialog.

---

## Why this works without local admin rights

TeamScribe is designed to install and run from a normal Windows 11 terminal
**without any admin elevation (no UAC prompt, no system-level install)**:

- Python runs in user mode — `pip install --user` or a venv in your profile.
- Audio capture uses **WASAPI loopback** via
  [`PyAudioWPatch`](https://github.com/s0d3s/PyAudioWPatch): a pip-only package
  that needs **no signed driver and no "Stereo Mix"** to be enabled in the
  sound control panel.
- All secrets (API keys, Azure identifiers, Planner IDs) live in a local
  `.env` file — **never** the Windows registry.

> **Separate concern — the Azure app registration.** Creating the Entra ID
> (Azure AD) app registration that gives you a `Client ID` is a *tenant-level*
> action, unrelated to admin rights on your own PC. If self-service app
> registration is disabled in your tenant, you will need a one-off request to
> your IT department (see [Microsoft setup](#microsoft-setup)).

---

## Installation

### Easiest: guided installer

New to this project? Right-click **`install.ps1`** and choose *Run with PowerShell*.
It asks for your language first (English or Français), then walks you
through everything step by step (Python check, installing TeamScribe, your
Anthropic key, optional Planner setup, desktop shortcut, launch-at-startup)
and asks before doing anything — no admin rights needed. The widget itself
defaults to English for new installs; switch it to Français anytime in Settings.

### Manual

```powershell
# From the project folder, in a normal (non-admin) terminal:
pip install --user -e .

# Then copy the example config and fill it in:
copy .env.example .env
notepad .env
```

`teamscribe` is then on your PATH (you may need to add your user Scripts
directory to PATH, e.g. `%APPDATA%\Python\Python311\Scripts`).

### GPU vs CPU transcription

`faster-whisper` auto-detects an NVIDIA GPU (CUDA + float16) and otherwise
falls back to CPU (int8). Force it with `TEAMSCRIBE_DEVICE=cuda|cpu` in
`.env`. CUDA needs the matching cuBLAS/cuDNN runtime on your machine; if it is
missing, set `TEAMSCRIBE_DEVICE=cpu`.

### Choosing an LLM provider (summarization)

By default TeamScribe summarizes with Claude (Anthropic). To use OpenAI
instead, set in `.env`:

```
TEAMSCRIBE_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
TEAMSCRIBE_OPENAI_MODEL=gpt-4o-mini   # optional override
```

Only the official OpenAI API is supported (no custom base URL), so your key
is never sent anywhere but `api.openai.com`. Both providers use native
structured-output enforcement, so the summary JSON shape is guaranteed
either way.

---

## Usage

### Desktop widget (recommended)

```powershell
teamscribe gui
```

The widget is a small always-on-top window with one-click recording,
a live timer, a recent-sessions list, and buttons to summarize and push
tasks to Planner — all without opening a terminal.

**Key widget features:**
- **Minimize** — collapse to a slim title bar; the record button stays
  accessible so you can start/stop recording even while minimized.
- **Pin** — lock the window position so it cannot be accidentally dragged.
- **Audio kept / Privacy mode** — toggle whether the raw audio file is kept
  after transcription.
- **Summarize** — (re)generate the summary for any selected session.
- **Push to Planner** — push action items to Microsoft Planner; if no summary
  exists yet, one is generated automatically first so notes are always saved
  locally even if Planner is unreachable.
- Settings include: theme (dark/light), transparency, language (EN/FR),
  always-on-top, launch at startup, audio check, and one-click updates.

### Command line

```powershell
# 0. (Optional) Check your audio setup before a real meeting.
teamscribe devices     # list the speakers + mic TeamScribe will capture
teamscribe selftest    # sample loopback + mic separately, report signal levels

# 1. Record a meeting (Ctrl+C to stop). Transcribes + summarizes on stop.
teamscribe record

# 2. (Re)summarize a session transcript if needed.
teamscribe summarize 20260616_134500      # or omit id to use the latest

# 3a. One-time: choose the Planner plan + bucket to target.
teamscribe setup

# 3b. Preview the tasks WITHOUT creating them (always do this first):
teamscribe push-tasks --dry-run

# 3c. Create the tasks in Planner.
teamscribe push-tasks
```

Each run creates `sessions/{date}_{time}/` containing `audio.wav`,
`transcript.txt`, `transcript.json`, `summary.json`, and `summary.md`.

### Summary format

`summary.json`:

```json
{
  "sujets": ["...", "..."],
  "decisions": ["...", "..."],
  "actions": [
    {"description": "...", "responsable": "...", "echeance": "..."}
  ]
}
```

`responsable` and `echeance` are filled only when explicitly stated in the
discussion; otherwise they are empty strings.

---

## Microsoft setup

To push tasks you need an **Entra ID (Azure AD) app registration**:

1. In the [Azure portal](https://entra.microsoft.com) go to *App registrations*
   then *New registration*. Name it (e.g. "TeamScribe"). For "Supported account
   types" pick your org. No redirect URI is needed.
2. On the app *Authentication* page, enable **Allow public client flows**
   (this lets the device-code login work).
3. On *API permissions*, add **delegated** Microsoft Graph permissions:
   - `Tasks.ReadWrite`
   - `Group.Read.All`
   Grant admin consent if your tenant requires it.
4. Copy the **Application (client) ID** into `AZURE_CLIENT_ID` in `.env`.
   Set `AZURE_TENANT_ID` to `organizations` (or your tenant GUID).

The first `teamscribe setup` / `push-tasks` prints a code to enter at
<https://microsoft.com/devicelogin>. After that, the refresh token is cached
in `token_cache.bin` (git-ignored) and login is silent.

### Auth errors

TeamScribe surfaces actionable messages instead of raw stack traces. If you
see a permission/expiry error, the usual fixes are: confirm the two delegated
permissions above are granted, and delete `token_cache.bin` to force a fresh
login.

---

## Why not a French fine-tuned model?

We intentionally use the standard multilingual `large-v3-turbo` (or
`large-v3`) with `language="fr"`, **not** a French fine-tuned variant
(e.g. `whisper-large-v3-french`). A study on Québécois French
(Zhang et al., *Canadian Acoustics* / arXiv 2508.21193) found that hexagonal
("France") French fine-tunes perform *worse* on the Québec accent than the
base multilingual model.

If base accuracy disappoints in real use, a future option is LoRA fine-tuning
on Diabolocom's Québec French telephone subset
([`diabolocom/talkbank_4_stt`](https://huggingface.co/datasets/diabolocom/talkbank_4_stt)).

---

## Project layout

```
teamscribe/
  teamscribe/
    __init__.py      # version
    config.py        # .env loading, session paths (no registry)
    capture.py       # PyAudioWPatch dual-stream capture -> 16 kHz mono wav
    transcribe.py    # faster-whisper (fr, VAD, GPU/CPU autodetect, model cache)
    summarize.py     # Anthropic/OpenAI API -> structured JSON + markdown
    naming.py        # contextual session folder rename from transcript
    planner.py       # MSAL device-code auth + Microsoft Graph
    gui.py           # PySide6 desktop widget (teamscribe gui)
    i18n.py          # bilingual strings (EN/FR) for the widget
    shortcuts.py     # Windows startup + desktop shortcut helpers (cached)
    update.py        # git-based update check and apply
    cli.py           # Typer entry point
  sessions/          # per-meeting output (git-ignored)
  assets/            # logo (png, svg, ico)
  .env.example
  pyproject.toml
  README.md
```

---

## Roadmap

- Speaker diarization ("who said what") with
  [`pyannote-audio`](https://github.com/pyannote/pyannote-audio).
- Real-time transcription during the meeting
  (`Whisper-Streaming` / `WhisperLive`).
- LoRA fine-tuning on Québécois French if base accuracy disappoints.

---

## Security

- `.env` and `token_cache.bin` are git-ignored from the first commit; no
  secret is ever committed.
- Nothing is written to the Windows registry.
- Verify with `git status` before committing that neither file is staged.
