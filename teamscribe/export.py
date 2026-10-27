"""Portable meeting export (``teamscribe.meeting/1``).

Turns a session's ``summary.json`` into one self-describing JSON document
that any task manager can import: paste it, drop the file, or (later) POST
it. Nothing here is tied to a specific app.

    {
      "format": "teamscribe.meeting/1",
      "session": "<session folder name, stable id for de-duplication>",
      "title": "...", "date": "<ISO 8601>", "kind": "online" | "in-person",
      "language": "fr", "participants": [],
      "topics": [...], "decisions": [...],
      "actions": [{"description", "owner", "due", "steps": [...]}]
    }

Only the summary is exported: the audio and the full transcript never
leave the session folder.
"""

from __future__ import annotations

import ctypes
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, config

FORMAT = "teamscribe.meeting/1"
FILENAME = "meeting.json"

# "<slug>_DD-MM-YYYY_HH-MM-SS" (finalized) or "YYYYMMDD_HHMMSS" (raw).
_FINAL_RE = re.compile(r"^(?P<slug>.+)_(?P<d>\d{2}-\d{2}-\d{4})_(?P<t>\d{2}-\d{2}-\d{2})$")
_RAW_RE = re.compile(r"^\d{8}_\d{6}$")


def session_date(session: Path) -> datetime:
    """When the meeting was recorded (from the folder name, else mtime)."""
    return _session_date(session)


def _session_date(session: Path) -> datetime:
    name = session.name
    m = _FINAL_RE.match(name)
    if m:
        return datetime.strptime(f"{m['d']} {m['t']}", "%d-%m-%Y %H-%M-%S")
    if _RAW_RE.match(name):
        return datetime.strptime(name, "%Y%m%d_%H%M%S")
    return datetime.fromtimestamp(session.stat().st_mtime)


def title_from_name(session: Path) -> str:
    """Readable title from the session folder name (falls back to the name)."""
    m = _FINAL_RE.match(Path(session).name)
    if m:
        words = m["slug"].replace("-", " ").strip()
        if words:
            return words[:1].upper() + words[1:]
    return Path(session).name


def _session_title(session: Path, summary: dict) -> str:
    m = _FINAL_RE.match(session.name)
    if m:
        words = m["slug"].replace("-", " ").strip()
        if words:
            return words[:1].upper() + words[1:]
    topics = summary.get("sujets") or []
    return (topics[0] if topics else "Meeting")[:120]


def _strings(values) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def build_meeting(session: Path) -> dict:
    """Build the portable meeting document from ``session/summary.json``."""
    session = Path(session)
    summary_path = session / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"No summary.json in {session.name}. Summarize it first.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    actions = []
    for a in summary.get("actions") or []:
        description = str(a.get("description") or "").strip()
        if not description:
            continue
        actions.append(
            {
                "description": description,
                "owner": str(a.get("responsable") or "").strip(),
                "due": str(a.get("echeance") or "").strip(),
                "steps": _strings(a.get("etapes")),
            }
        )
    return {
        "format": FORMAT,
        "generator": f"TeamScribe {__version__}",
        "session": session.name,
        "title": _session_title(session, summary),
        "date": _session_date(session).astimezone().isoformat(timespec="seconds"),
        "kind": summary.get("kind") or "online",
        "language": config.ui_language(),
        "participants": _strings(summary.get("participants")),
        "topics": _strings(summary.get("sujets")),
        "decisions": _strings(summary.get("decisions")),
        "actions": actions,
    }


def export_session(session: Path) -> str:
    """Write ``meeting.json`` in the session and return its JSON text."""
    text = json.dumps(build_meeting(session), ensure_ascii=False, indent=2)
    (Path(session) / FILENAME).write_text(text, encoding="utf-8")
    return text


def copy_to_clipboard(text: str) -> bool:
    """Put ``text`` on the Windows clipboard (Unicode). Returns False elsewhere
    or on failure; the GUI uses Qt's clipboard instead."""
    if sys.platform != "win32":
        return False
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
    finally:
        user32.CloseClipboard()
