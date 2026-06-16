"""TeamScribe command-line interface.

    teamscribe record                 capture -> transcribe -> summarize
    teamscribe summarize [SESSION]    (re)summarize a session's transcript
    teamscribe setup                  choose the target Planner plan + bucket
    teamscribe push-tasks [SESSION]   create Planner tasks from the summary
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from . import config

if sys.platform == "win32":
    # Windows terminals (cmd.exe, legacy PowerShell) default to a codepage
    # like cp1252 that can't encode the emoji/ellipsis rich prints (e.g. the
    # ⏱ timer), crashing mid-recording with UnicodeEncodeError. Force UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    add_completion=False,
    help="Record, transcribe and summarize Teams meetings; push actions to Planner.",
)
console = Console()


def _resolve_session(session: str | None) -> Path:
    if session:
        return config.session_dir(session)
    path = config.latest_session_dir()
    console.print(f"[dim]Using latest session: {path.name}[/dim]")
    return path


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------

@app.command()
def record(
    summarize: bool = typer.Option(
        True, help="Automatically summarize after transcription."
    ),
):
    """Capture loopback + mic, then transcribe (and summarize) on stop."""
    from . import capture, summarize as summarize_mod, transcribe

    session = config.new_session_dir()
    audio_path = session / "audio.wav"
    console.print(f"[bold]Session:[/bold] {session.name}")
    console.print("[bold green]Recording…[/bold green] press [bold]Ctrl+C[/bold] to stop.")
    console.print(
        "[yellow]Reminder:[/yellow] make sure participants consent to recording "
        "per your company's policy."
    )

    shown = {"info": False}

    def on_tick(elapsed: float, mic_name: str, speaker_name: str) -> None:
        if not shown["info"]:
            console.print(f"[dim]  mic: {mic_name}[/dim]")
            console.print(f"[dim]  speakers (loopback): {speaker_name}[/dim]")
            shown["info"] = True
        console.print(f"\r  ⏱  {_fmt_elapsed(elapsed)}", end="")

    capture.record(audio_path, config.max_minutes(), on_tick=on_tick)
    console.print()  # newline after the timer
    console.print(f"[green]Saved audio:[/green] {audio_path}")

    transcribe.transcribe(audio_path, session, log=console.print)

    if summarize:
        try:
            summarize_mod.summarize_session(session, log=console.print)
        except Exception as exc:
            console.print(f"[red]Summarization skipped:[/red] {exc}")

    console.print(f"[bold green]Done.[/bold green] Session id: {session.name}")


@app.command()
def devices():
    """List the audio devices TeamScribe will capture (speakers + mic)."""
    from . import capture

    try:
        info = capture.list_devices()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    sp = info["default_speakers"]
    mic = info["default_mic"]
    console.print("[bold]Default speakers (loopback source):[/bold]")
    console.print(f"  {sp['name']}  @ {sp['rate']} Hz")
    console.print("[bold]Default microphone (your voice):[/bold]")
    console.print(f"  {mic['name']}  @ {mic['rate']} Hz")
    console.print(f"[dim]WASAPI loopback devices found: {len(info['loopback_devices'])}[/dim]")


@app.command()
def selftest(
    seconds: float = typer.Option(6.0, help="How long to sample each stream."),
):
    """Record loopback + mic separately and report each stream's signal level.

    Play a sound through your speakers AND speak into your mic during the
    sample window, then check that BOTH streams show a non-zero RMS.
    """
    from . import capture

    console.print(
        f"[bold green]Sampling {seconds:.0f}s…[/bold green] "
        "play a sound through your speakers AND speak into your mic now."
    )
    try:
        result = capture.probe(seconds)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    def verdict(rms: float) -> str:
        if rms >= 0.01:
            return "[green]signal OK[/green]"
        if rms > 0.0005:
            return "[yellow]very quiet[/yellow]"
        return "[red]SILENT[/red]"

    for key in ("loopback", "microphone"):
        s = result[key]
        console.print(
            f"[bold]{key}[/bold] ({s['name']}): rms={s['rms']:.4f} "
            f"peak={s['peak']:.3f}  -> {verdict(s['rms'])}"
        )

    if result["loopback"]["rms"] < 0.0005 or result["microphone"]["rms"] < 0.0005:
        console.print(
            "\n[yellow]One stream looks silent.[/yellow] Check that audio was "
            "actually playing (loopback) and that the right mic is the default."
        )


@app.command(name="transcribe")
def transcribe_cmd(
    session: str = typer.Argument(None, help="Session id (default: latest)."),
):
    """Transcribe a session's audio.wav (rarely needed; record does it)."""
    from . import transcribe

    path = _resolve_session(session)
    transcribe.transcribe(path / "audio.wav", path, log=console.print)


@app.command()
def summarize(
    session: str = typer.Argument(None, help="Session id (default: latest)."),
):
    """Summarize a session's transcript into summary.json / summary.md."""
    from . import summarize as summarize_mod

    path = _resolve_session(session)
    summary = summarize_mod.summarize_session(path, log=console.print)
    console.print_json(json.dumps(summary, ensure_ascii=False))


@app.command()
def setup():
    """Pick the target Planner plan + bucket and save them to .env."""
    from . import planner

    try:
        token = planner.get_token(log=console.print)
        plans = planner.list_plans(token)
    except planner.GraphAuthError as exc:
        console.print(f"[red]Auth error:[/red] {exc}")
        raise typer.Exit(1)

    if not plans:
        console.print(
            "[yellow]No Planner plans found for your account.[/yellow] "
            "Create one in Planner first, or check the Group.Read.All permission."
        )
        raise typer.Exit(1)

    console.print("\n[bold]Available plans:[/bold]")
    for i, p in enumerate(plans):
        console.print(f"  [{i}] {p.get('title', '(untitled)')}  ({p['id']})")
    idx = typer.prompt("Choose a plan number", type=int)
    plan = plans[idx]

    buckets = planner.list_buckets(token, plan["id"])
    if not buckets:
        console.print("[yellow]That plan has no buckets.[/yellow] Add one in Planner.")
        raise typer.Exit(1)

    console.print("\n[bold]Buckets in that plan:[/bold]")
    for i, b in enumerate(buckets):
        console.print(f"  [{i}] {b.get('name', '(unnamed)')}  ({b['id']})")
    bidx = typer.prompt("Choose a bucket number", type=int)
    bucket = buckets[bidx]

    config.set_env_value("PLANNER_PLAN_ID", plan["id"])
    config.set_env_value("PLANNER_BUCKET_ID", bucket["id"])
    console.print(
        f"\n[green]Saved[/green] plan '{plan.get('title')}' / bucket "
        f"'{bucket.get('name')}' to .env."
    )


def _iso_due(echeance: str) -> str | None:
    """Best-effort: turn an ISO-ish date (YYYY-MM-DD) into a Graph dueDateTime."""
    echeance = (echeance or "").strip()
    if len(echeance) == 10 and echeance[4] == "-" and echeance[7] == "-":
        return f"{echeance}T00:00:00Z"
    return None


@app.command()
def push_tasks(
    session: str = typer.Argument(None, help="Session id (default: latest)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show tasks that would be created; create nothing."
    ),
):
    """Create a Planner task for each action in the session's summary."""
    from . import planner

    path = _resolve_session(session)
    summary_path = path / "summary.json"
    if not summary_path.is_file():
        console.print(f"[red]No summary.json in {path}.[/red] Run summarize first.")
        raise typer.Exit(1)

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    actions = summary.get("actions", [])
    if not actions:
        console.print("[yellow]No actions in the summary — nothing to push.[/yellow]")
        raise typer.Exit(0)

    if dry_run:
        console.print(f"[bold]Dry run — {len(actions)} task(s) would be created:[/bold]")
        for a in actions:
            resp = a.get("responsable") or "—"
            ech = a.get("echeance") or "—"
            console.print(f"  • {a.get('description', '').strip()}")
            console.print(f"      responsable: {resp}   échéance: {ech}")
        return

    plan_id = config.require_env("PLANNER_PLAN_ID")
    bucket_id = config.require_env("PLANNER_BUCKET_ID")

    try:
        token = planner.get_token(log=console.print)
    except planner.GraphAuthError as exc:
        console.print(f"[red]Auth error:[/red] {exc}")
        raise typer.Exit(1)

    created = []
    for a in actions:
        title = (a.get("description") or "").strip()
        if not title:
            continue
        resp = (a.get("responsable") or "").strip()
        ech = (a.get("echeance") or "").strip()
        notes = (
            "Créé automatiquement par TeamScribe à partir de la réunion "
            f"{path.name}.\n"
            f"Responsable mentionné : {resp or '—'}\n"
            f"Échéance mentionnée : {ech or '—'}"
        )
        try:
            task = planner.create_task(
                token, plan_id, bucket_id, title,
                due_iso=_iso_due(ech), notes=notes,
            )
            created.append(task)
            console.print(f"[green]✓[/green] {task.title}\n    {task.web_url}")
        except planner.GraphAuthError as exc:
            console.print(f"[red]Auth error:[/red] {exc}")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]✗ Failed:[/red] {title} — {exc}")

    console.print(f"\n[bold green]Created {len(created)} task(s).[/bold green]")


if __name__ == "__main__":
    app()
