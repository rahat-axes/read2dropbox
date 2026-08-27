import typer

app = typer.Typer(help="Export Read AI meetings to Dropbox.")


@app.command()
def sync():
    """Placeholder — implemented in Phase 5/6."""
    typer.echo("not implemented yet")


def main() -> None:
    app()