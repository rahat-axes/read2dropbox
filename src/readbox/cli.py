"""Command-line entry point — the real CLI (Phase 6).

Subcommands:
* ``readbox auth``   — authorize Read AI + Dropbox and store tokens locally.
* ``readbox sync``   — export new/changed meetings to Dropbox (``--full``, ``--days N``).
* ``readbox list``   — list your Read AI meetings (read-only, no upload).
* ``readbox status`` — show what's authorized and what the last sync tracked.

Business logic lives in ``sync.py`` / ``read_client.py`` / ``auth.py``; this module
is just argument parsing and friendly output.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import typer

from . import auth, config, sync as sync_mod
from .auth import AuthError, TokenStore, read_access_token
from .read_client import ReadAPIError, ReadClient

app = typer.Typer(help="Export Read AI meetings to Dropbox.", no_args_is_help=True)


# -- auth --------------------------------------------------------------------


def auth_(  # registered as `readbox auth` at the bottom of this module
    read: bool = typer.Option(True, help="Authorize Read AI."),
    dropbox: bool = typer.Option(True, help="Authorize Dropbox."),
    reset: bool = typer.Option(False, help="Forget stored credentials first."),
    client_name: str = typer.Option("readbox", help="Read OAuth client name."),
    app_key: str = typer.Option(
        None, help="Dropbox App key (else $READBOX_DROPBOX_APP_KEY)."
    ),
) -> None:
    """Authorize your Read AI and Dropbox accounts and store the tokens locally."""
    store = TokenStore()
    try:
        if reset:
            store.clear("read")
            store.clear("dropbox")
        if read:
            _auth_read(store, client_name)
        if dropbox:
            auth.authorize_dropbox(store, app_key=app_key)
            typer.echo("✓ Dropbox authorized.")
    except AuthError as exc:
        typer.secho(f"Auth failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.echo(f"Credentials stored in {store.path}")


def _auth_read(store: TokenStore, client_name: str) -> None:
    """Rough Read authorization: register a client, then exchange a pasted code."""
    creds = auth.register_read_client(client_name)
    typer.echo(
        "1. Open Read's authorization page and sign in:\n"
        f"\n   {auth.READ_AUTHORIZE_URL}\n\n"
        f"   Use client_id: {creds['client_id']}\n"
        "2. Click Allow Access, then copy the authorization code."
    )
    code = typer.prompt("Paste the Read authorization code").strip()
    updated = auth.exchange_read_code(creds, code)
    store.set("read", updated)
    typer.echo("✓ Read AI authorized.")


# -- sync --------------------------------------------------------------------


@app.command()
def sync(
    full: bool = typer.Option(
        False, "--full", help="Re-upload every meeting, ignoring stored state."
    ),
    days: Optional[int] = typer.Option(
        None, "--days", help="Only sync meetings from the last N days.", min=1
    ),
) -> None:
    """Export new or changed meetings to Dropbox."""
    start_time_ms = None
    if days is not None:
        start_time_ms = int((time.time() - days * 86400) * 1000)
        typer.echo(f"Syncing meetings from the last {days} day(s)…")
    elif full:
        typer.echo("Full sync (re-uploading everything)…")
    else:
        typer.echo("Syncing new/changed meetings…")

    try:
        result = sync_mod.run_sync(
            full=full, start_time_ms=start_time_ms, logger=typer.echo
        )
    except AuthError as exc:
        typer.secho(
            f"{exc}\nRun `readbox auth` first.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)
    except ReadAPIError as exc:
        typer.secho(f"Read API error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    color = typer.colors.RED if result.failed else typer.colors.GREEN
    typer.secho(result.summary_line(), fg=color)
    if result.failed:
        for meeting_id, reason in result.failed:
            typer.secho(f"  ! {meeting_id}: {reason}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


# -- list --------------------------------------------------------------------


@app.command("list")
def list_meetings(
    days: Optional[int] = typer.Option(
        None, "--days", help="Only show meetings from the last N days.", min=1
    ),
) -> None:
    """List your Read AI meetings (read-only — nothing is uploaded)."""
    start_time_ms = None
    if days is not None:
        start_time_ms = int((time.time() - days * 86400) * 1000)

    store = TokenStore()
    try:
        token = read_access_token(store)
        meetings = ReadClient(token).list_meetings(start_time_ms=start_time_ms)
    except AuthError as exc:
        typer.secho(
            f"{exc}\nRun `readbox auth` first.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)
    except ReadAPIError as exc:
        typer.secho(f"Read API error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if not meetings:
        typer.echo("No meetings found.")
        return

    for m in meetings:
        typer.echo(f"{_fmt_date(m.get('start_time_ms'))}  {m.get('title') or 'Untitled'}")
        typer.secho(f"    {m.get('id')}", fg=typer.colors.BRIGHT_BLACK)
    typer.echo(f"\n{len(meetings)} meeting(s).")


# -- status ------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show what's authorized and what the last sync tracked."""
    store = TokenStore()
    read_ok = bool(store.get("read").get("refresh_token"))
    dropbox_ok = bool(store.get("dropbox").get("refresh_token"))

    typer.echo("Authorization:")
    _status_line("Read AI", read_ok)
    _status_line("Dropbox", dropbox_ok)

    state = sync_mod.load_state(config.state_path())
    tracked = state.get("meetings", {})
    typer.echo(f"\nSynced meetings tracked: {len(tracked)}")
    if tracked:
        last = max(m.get("synced_at", 0) for m in tracked.values())
        typer.echo(f"Last sync: {_fmt_epoch(last)}")
    typer.echo(f"Config dir: {config.config_dir()}")


def _status_line(name: str, ok: bool) -> None:
    mark, color = ("✓", typer.colors.GREEN) if ok else ("✗", typer.colors.RED)
    suffix = "" if ok else "  (run `readbox auth`)"
    typer.secho(f"  {mark} {name}{suffix}", fg=color)


# -- helpers -----------------------------------------------------------------


def _fmt_date(start_time_ms: Optional[int]) -> str:
    if not isinstance(start_time_ms, int):
        return "??‑??‑??"
    dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _fmt_epoch(epoch: float) -> str:
    if not epoch:
        return "never"
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# Register `auth` under its name (function is `auth_` to avoid shadowing the module).
app.command("auth")(auth_)


def main() -> None:
    app()
