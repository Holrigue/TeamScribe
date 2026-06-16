"""Shared configuration and filesystem helpers.

All secrets come from a local ``.env`` file (loaded here once). Nothing is
read from or written to the Windows registry.
"""

from __future__ import annotations

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
