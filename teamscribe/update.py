"""Check for and apply updates, based on the project's official GitHub Releases.

The check asks the public GitHub API for the latest Release and compares its
version with the running one, so it works the same for a ``git clone`` and
for a folder unzipped from the Releases page, and only official Releases
(not every push to main) are offered.

Applying an update depends on how TeamScribe was installed:

* git checkout: ``git pull`` with the user's own git setup;
* zip download: the Release's source zip is downloaded and unpacked over the
  install folder, leaving the user's own files (``.env`` keys, recorded
  sessions, preferences, Microsoft token cache, log) untouched.

Either way the dependencies are then reinstalled with pip.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from . import __version__, config

REPO = "Holrigue/TeamScribe"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": f"TeamScribe/{__version__}"}

# Top-level names in the install folder that belong to the user, never to a
# release: a zip update must not overwrite or delete them.
_USER_FILES = {
    ".env", "gui_settings.json", "token_cache.bin", "teamscribe.log", "teamscribe.log.old",
    "sessions", "models", ".git",
}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(config.ROOT),
        capture_output=True,
        text=True,
        check=True,
        creationflags=config.NO_WINDOW,
    )
    return result.stdout.strip()


def is_git_checkout() -> bool:
    return (config.ROOT / ".git").is_dir()


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def has_local_changes() -> bool:
    return bool(_git("status", "--porcelain"))


def _version_tuple(version: str) -> tuple[int, ...]:
    """"v2.3.10" -> (2, 3, 10); anything unparsable sorts lowest."""
    return tuple(int(n) for n in re.findall(r"\d+", version or "")[:3])


def latest_release() -> dict:
    """The latest published GitHub Release (raises on network/API errors)."""
    import requests

    response = requests.get(_LATEST_RELEASE_API, headers=_HEADERS, timeout=15)
    response.raise_for_status()
    data = response.json()
    tag = data.get("tag_name") or ""
    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "name": data.get("name") or tag,
        "notes": data.get("body") or "",
        "url": data.get("html_url") or f"https://github.com/{REPO}/releases",
        "zip_url": data.get("zipball_url") or "",
    }


def check_for_update() -> dict:
    """Report whether a newer official Release than the running version exists.

    Returns ``{"available", "current", "latest", "name", "notes", "url",
    "zip_url", "method", "error"}`` where ``method`` is "git" or "zip".
    Never raises: a failure (e.g. no network) is reported via "error", since
    this runs unattended in the background.
    """
    info = {
        "available": False, "current": __version__, "latest": "", "name": "", "notes": "",
        "url": f"https://github.com/{REPO}/releases", "zip_url": "",
        "method": "git" if is_git_checkout() else "zip", "error": None,
    }
    try:
        release = latest_release()
    except Exception as exc:
        info["error"] = str(exc)[:200]
        return info
    info.update(
        latest=release["version"], name=release["name"], notes=release["notes"],
        url=release["url"], zip_url=release["zip_url"],
        available=_version_tuple(release["version"]) > _version_tuple(__version__),
    )
    return info


def _reinstall_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "-e", "."],
        cwd=str(config.ROOT),
        capture_output=True,
        text=True,
        creationflags=config.NO_WINDOW,
    )
    if result.returncode != 0:
        # No console to read it in: surface pip's own error message instead.
        raise RuntimeError((result.stderr or result.stdout).strip()[-800:] or "pip install failed")


def _apply_zip(zip_url: str, log) -> None:
    """Download a Release's source zip and unpack it over the install folder."""
    import requests

    root = config.ROOT
    with tempfile.TemporaryDirectory(prefix="teamscribe-update-") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "release.zip"
        with requests.get(zip_url, headers=_HEADERS, timeout=60, stream=True) as response:
            response.raise_for_status()
            with open(archive, "wb") as fh:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)

        log("Décompression…")
        unpacked = tmp_dir / "src"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(unpacked)  # zipfile strips absolute and ".." paths
        # GitHub source zips hold one top folder, e.g. Holrigue-TeamScribe-<sha>/.
        tops = [p for p in unpacked.iterdir() if p.is_dir()]
        if len(tops) != 1 or not (tops[0] / "teamscribe" / "__init__.py").is_file():
            raise RuntimeError("The downloaded release doesn't look like TeamScribe; nothing was changed.")
        source = tops[0]

        log("Remplacement des fichiers…")
        for item in source.iterdir():
            if item.name in _USER_FILES:
                continue
            target = root / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        # Drop modules the new release no longer ships, so a stale one can't
        # shadow or break the new code.
        for old in (root / "teamscribe").rglob("*.py"):
            if not (source / old.relative_to(root)).is_file():
                old.unlink()


def apply_update(info: dict | None = None, *, log=print) -> None:
    """Install the latest Release (git pull or zip download), then reinstall
    dependencies. A git checkout with uncommitted changes is left alone."""
    if is_git_checkout():
        if has_local_changes():
            raise RuntimeError(
                "Des modifications locales non commitées existent dans le projet ; "
                "mets-les de côté avant de mettre à jour."
            )
        branch = current_branch()
        log(f"Récupération des changements ({branch})…")
        _git("pull", "origin", branch)
    else:
        zip_url = (info or {}).get("zip_url") or latest_release()["zip_url"]
        if not zip_url:
            raise RuntimeError("No downloadable release was found on GitHub.")
        log("Téléchargement de la nouvelle version…")
        _apply_zip(zip_url, log)
    log("Réinstallation des dépendances…")
    _reinstall_dependencies()
    log("Mise à jour terminée.")
