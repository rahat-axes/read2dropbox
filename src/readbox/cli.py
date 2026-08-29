"""Command-line entry point.

Phase 4 wires a *rough* ``auth`` command (register Read + authorize Dropbox and
store the tokens). ``sync`` remains a placeholder until Phase 5; the polished CLI
(friendly output, ``list``/``status``, ``--days``/``--full``) lands in Phase 6.
"""

from __future__ import annotations

import typer

from . import auth
from .auth import AuthError, TokenStore

app = typer.Typer(help="Export Read AI meetings to Dropbox.")


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


@app.command()
def sync() -> None:
    """Placeholder — implemented in Phase 5/6."""
    typer.echo("not implemented yet")


# Register the auth command under the name `auth` (the function is `auth_` to avoid
# shadowing the imported `auth` module).
app.command("auth")(auth_)


def main() -> None:
    app()
