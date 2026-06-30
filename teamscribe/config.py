"""Shared configuration and filesystem helpers.

All secrets come from a local ``.env`` file (loaded here once). Nothing is
read from or written to the Windows registry.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Project root = the directory that contains this package's parent.
# e.g.  <root>/teamscribe/config.py  ->  ROOT = <root>
ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = ROOT / "sessions"
ENV_PATH = ROOT / ".env"
TOKEN_CACHE_PATH = ROOT / "token_cache.bin"
GUI_SETTINGS_PATH = ROOT / "gui_settings.json"

# Load .env once, on import. override=False so real env vars win.
load_dotenv(ENV_PATH, override=False)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip()
    return value or default


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise RuntimeError(
            f"Missing required setting '{name}'. "
            f"Add it to your .env file (copy .env.example to .env)."
        )
    return value


# ---- Recording / transcription settings -----------------------------------

def max_minutes() -> float:
    try:
        return float(env("TEAMSCRIBE_MAX_MINUTES", "120"))
    except ValueError:
        return 120.0


def whisper_model() -> str:
    return env("TEAMSCRIBE_WHISPER_MODEL", "large-v3-turbo")


def device_preference() -> str:
    return (env("TEAMSCRIBE_DEVICE", "auto") or "auto").lower()


def summary_model() -> str:
    return env("TEAMSCRIBE_SUMMARY_MODEL", "claude-sonnet-4-6")


def llm_provider() -> str:
    """Which LLM backend powers summarize/naming: "anthropic", "openai", "azure_openai", or "gemini".

    GUI setting takes priority over the .env variable so the user can switch
    from the widget without editing files.
    """
    valid = ("anthropic", "openai", "azure_openai", "gemini")
    gui = load_gui_settings().get("llm_provider", "")
    if gui in valid:
        return gui
    value = (env("TEAMSCRIBE_LLM_PROVIDER", "anthropic") or "anthropic").lower()
    return value if value in valid else "anthropic"


def openai_model() -> str:
    return env("TEAMSCRIBE_OPENAI_MODEL", "gpt-4o-mini")


# ---- Session directories ---------------------------------------------------

def new_session_dir(now: datetime | None = None) -> Path:
    """Create and return sessions/{YYYYMMDD_HHMMSS}/."""
    now = now or datetime.now()
    session_id = now.strftime("%Y%m%d_%H%M%S")
    path = SESSIONS_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_dir(session_id: str) -> Path:
    """Resolve an existing session directory by id, raising if absent."""
    path = SESSIONS_DIR / session_id
    if not path.is_dir():
        raise FileNotFoundError(
            f"Session '{session_id}' not found under {SESSIONS_DIR}."
        )
    return path


def latest_session_dir() -> Path:
    """Return the most recently created session directory."""
    candidates = [p for p in SESSIONS_DIR.glob("*") if p.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No sessions found under {SESSIONS_DIR}.")
    return max(candidates, key=lambda p: p.name)


def set_env_value(key: str, value: str) -> None:
    """Create/update a single KEY=value line in the local .env file.

    Used by `teamscribe setup` to persist Planner IDs — never the registry.
    """
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            existing_key = stripped.split("=", 1)[0].strip()
            if existing_key == key:
                lines[i] = new_line
                break
    else:
        lines.append(new_line)

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


# ---- GUI preferences --------------------------------------------------
# Non-secret widget prefs (auto-summarize, always-on-top...), kept separate
# from .env which is reserved for credentials/Planner ids.

_GUI_DEFAULTS = {
    "auto_summarize": True,
    "always_on_top": True,
    "hidden_sessions": [],  # session folder names removed from the widget list only
    "pinned": False,  # when true, the widget can't be dragged/moved
    "theme": "dark",  # "dark" or "light"
    "glass_opacity": 100,  # 0-100; lower = more see-through ("liquid glass")
    "pos_x": None,  # last on-screen position; None = not yet placed
    "pos_y": None,
    "language": "en",  # "en" or "fr"; new installs default to English
    "llm_provider": "",  # "" = follow .env; otherwise overrides TEAMSCRIBE_LLM_PROVIDER
    "keep_audio": False,  # False = delete audio.wav after transcription (privacy mode ON by default)
}


def load_gui_settings() -> dict:
    if not GUI_SETTINGS_PATH.is_file():
        return dict(_GUI_DEFAULTS)
    try:
        data = json.loads(GUI_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_GUI_DEFAULTS)
    return {**_GUI_DEFAULTS, **data}


def save_gui_settings(settings: dict) -> None:
    GUI_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )
