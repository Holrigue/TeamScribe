"""Check for and apply updates from the project's git remote.

Uses the user's own git credentials (whatever they used to clone the repo)
rather than the GitHub API, so this works the same whether the repo is
public or private and needs no token bundled with the app.
"""

from __future__ import annotations

import subprocess
import sys

from . import config


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(config.ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def is_git_checkout() -> bool:
    return (config.ROOT / ".git").is_dir()


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def has_local_changes() -> bool:
    return bool(_git("status", "--porcelain"))


def check_for_update() -> dict:
    """Fetch the remote and report whether the local checkout is behind.

    Returns ``{"available": bool, "behind": int, "error": str | None}``.
    Never raises: any failure (no git, no network, not a git checkout,
    e.g. the project was downloaded as a zip) is reported via "error"
    rather than propagated, since this runs unattended on startup.
    """
    if not is_git_checkout():
        return {"available": False, "behind": 0, "error": "not-a-git-checkout"}
    try:
        branch = current_branch()
        _git("fetch", "origin", branch, "--quiet")
        local = _git("rev-parse", "HEAD")
        remote = _git("rev-parse", f"origin/{branch}")
        if local == remote:
            return {"available": False, "behind": 0, "error": None}
        behind = int(_git("rev-list", "--count", f"{local}..{remote}"))
        return {"available": behind > 0, "behind": behind, "error": None}
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return {"available": False, "behind": 0, "error": str(exc)}


def apply_update(*, log=print) -> None:
    """Pull the latest commits and reinstall dependencies.

    Refuses if there are uncommitted local changes, to avoid clobbering
    in-progress work or causing a merge conflict.
    """
    if not is_git_checkout():
        raise RuntimeError(
            "Ce projet n'a pas été installé via git clone — impossible de le "
            "mettre à jour automatiquement. Télécharge la dernière version "
            "depuis GitHub."
        )
    if has_local_changes():
        raise RuntimeError(
            "Des modifications locales non commitées existent dans le projet ; "
            "mets-les de côté avant de mettre à jour."
        )
    branch = current_branch()
    log(f"Récupération des changements ({branch})…")
    _git("pull", "origin", branch)
    log("Réinstallation des dépendances…")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "-e", "."],
        cwd=str(config.ROOT),
        check=True,
    )
    log("Mise à jour terminée.")
