"""Microsoft Planner task creation via Microsoft Graph (direct access).

Authentication uses MSAL's device-code flow: you enter a one-time code at
https://microsoft.com/devicelogin, and the refresh token is then cached
locally (token_cache.bin, git-ignored) so subsequent runs are silent.

Required delegated Graph permissions: Tasks.ReadWrite, Group.Read.All.
"""

from __future__ import annotations

from dataclasses import dataclass

import msal
import requests

from . import config

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Tasks.ReadWrite", "Group.Read.All"]


class GraphAuthError(RuntimeError):
    """Raised for authentication / permission problems, with actionable text."""


@dataclass
class CreatedTask:
    id: str
    title: str
    web_url: str


# ---- Authentication --------------------------------------------------------

def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if config.TOKEN_CACHE_PATH.exists():
        cache.deserialize(config.TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        config.TOKEN_CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")


def _app(cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    client_id = config.require_env("AZURE_CLIENT_ID")
    tenant = config.env("AZURE_TENANT_ID", "organizations")
    authority = f"https://login.microsoftonline.com/{tenant}"
    return msal.PublicClientApplication(
        client_id, authority=authority, token_cache=cache
    )


def get_token(*, log=print) -> str:
    """Return a valid Graph access token, prompting device login if needed."""
    cache = _load_cache()
    app = _app(cache)

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise GraphAuthError(
                "Could not start device-code login. Check AZURE_CLIENT_ID and "
                "that the app registration allows public client / device flows. "
                f"Details: {flow.get('error_description', flow)}"
            )
        log("")
        log(flow["message"])  # e.g. "To sign in, use a web browser to open ..."
        log("")
        result = app.acquire_token_by_device_flow(flow)

    _save_cache(cache)

    if "access_token" not in result:
        err = result.get("error_description") or result.get("error") or result
        raise GraphAuthError(
            "Microsoft sign-in failed. If you see 'invalid_client' or "
            "'unauthorized_client', verify the app registration and its "
            f"delegated permissions ({', '.join(SCOPES)}).\nDetails: {err}"
        )
    return result["access_token"]


# ---- Graph helpers ---------------------------------------------------------

def _headers(token: str, extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _check(resp: requests.Response, action: str) -> None:
    if resp.status_code in (401, 403):
        raise GraphAuthError(
            f"{action} was denied (HTTP {resp.status_code}). Your token is "
            f"missing a permission or has expired. Ensure the app grants "
            f"{', '.join(SCOPES)} and sign in again "
            f"(delete token_cache.bin to force re-login).\n"
            f"Graph said: {resp.text[:300]}"
        )
    if not resp.ok:
        raise RuntimeError(
            f"{action} failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )


def list_plans(token: str) -> list[dict]:
    resp = requests.get(f"{GRAPH}/me/planner/plans", headers=_headers(token))
    _check(resp, "Listing Planner plans")
    return resp.json().get("value", [])


def list_buckets(token: str, plan_id: str) -> list[dict]:
    resp = requests.get(
        f"{GRAPH}/planner/plans/{plan_id}/buckets", headers=_headers(token)
    )
    _check(resp, "Listing Planner buckets")
    return resp.json().get("value", [])


def _task_web_url(task_id: str) -> str:
    return f"https://tasks.office.com/Home/Task/{task_id}"


def create_task(
    token: str,
    plan_id: str,
    bucket_id: str,
    title: str,
    *,
    due_iso: str | None = None,
    notes: str | None = None,
) -> CreatedTask:
    """Create one Planner task; optionally set a due date and notes."""
    body: dict = {"planId": plan_id, "bucketId": bucket_id, "title": title[:255]}
    if due_iso:
        body["dueDateTime"] = due_iso

    resp = requests.post(
        f"{GRAPH}/planner/tasks", headers=_headers(token), json=body
    )
    _check(resp, "Creating Planner task")
    task = resp.json()
    task_id = task["id"]

    if notes:
        _set_task_notes(token, task_id, notes)

    return CreatedTask(id=task_id, title=body["title"], web_url=_task_web_url(task_id))


def _set_task_notes(token: str, task_id: str, notes: str) -> None:
    """Best-effort: write the transcript context into the task description."""
    details = requests.get(
        f"{GRAPH}/planner/tasks/{task_id}/details", headers=_headers(token)
    )
    if not details.ok:
        return  # notes are non-critical; skip silently if details unavailable
    etag = details.json().get("@odata.etag")
    if not etag:
        return
    requests.patch(
        f"{GRAPH}/planner/tasks/{task_id}/details",
        headers=_headers(token, {"If-Match": etag}),
        json={"description": notes[:32000]},
    )
