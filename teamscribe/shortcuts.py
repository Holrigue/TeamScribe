"""Windows shortcut (.lnk) helpers for the GUI: startup + desktop launchers.

Uses the WScript.Shell COM object via a one-off PowerShell call rather than
adding a pywin32 dependency just for this. Targets ``pythonw.exe`` (no
console window) running ``-m teamscribe.cli gui`` from the project root.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import config

SHORTCUT_NAME = "TeamScribe.lnk"


def _python_target() -> Path:
    """Prefer pythonw.exe (no console flash) next to the running interpreter."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw if pythonw.is_file() else exe


def _known_folder(name: str) -> Path:
    # Don't assume standard %USERPROFILE% paths: OneDrive Known Folder Move
    # (common in managed/business OneDrive setups, as here) can redirect
    # Desktop/Startup elsewhere (e.g. "...\OneDrive - <tenant>\Bureau").
    # Ask Windows for the real path instead of hardcoding one.
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"[Environment]::GetFolderPath('{name}')"],
        check=True, capture_output=True, text=True,
    )
    return Path(result.stdout.strip())


def _startup_folder() -> Path:
    return _known_folder("Startup")


def _desktop_folder() -> Path:
    return _known_folder("Desktop")


def _write_shortcut(shortcut_path: Path) -> None:
    target = _python_target()
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{shortcut_path}'); "
        f"$s.TargetPath = '{target}'; "
        "$s.Arguments = '-m teamscribe.cli gui'; "
        f"$s.WorkingDirectory = '{config.ROOT}'; "
        "$s.Description = 'TeamScribe - capture et resume de reunions'; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
    )


def is_startup_enabled() -> bool:
    return (_startup_folder() / SHORTCUT_NAME).is_file()


def set_startup_enabled(enabled: bool) -> None:
    path = _startup_folder() / SHORTCUT_NAME
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_shortcut(path)
    elif path.is_file():
        path.unlink()


def create_desktop_shortcut() -> Path:
    path = _desktop_folder() / SHORTCUT_NAME
    _write_shortcut(path)
    return path
