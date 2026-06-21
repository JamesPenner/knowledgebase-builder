import typer

app = typer.Typer(help="Review queue commands")


@app.command("normalise")
def review_normalise(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Open the Normalization Review in the browser."""
    import webbrowser
    from pathlib import Path
    from src.config import load_config

    cfg = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)
    url = f"http://{cfg.host}:{cfg.port}/review/normalise"
    typer.echo(f"Opening Normalization Review: {url}")
    webbrowser.open(url)


@app.command("suggest")
def review_suggest(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Open the Suggestion Review in the browser."""
    import webbrowser
    from pathlib import Path
    from src.config import load_config

    cfg = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)
    url = f"http://{cfg.host}:{cfg.port}/review/suggest"
    typer.echo(f"Opening Suggestion Review: {url}")
    webbrowser.open(url)


@app.command("new-terms")
def review_new_terms(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Open the New Terms Review in the browser."""
    import webbrowser
    from pathlib import Path
    from src.config import load_config

    cfg = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)
    url = f"http://{cfg.host}:{cfg.port}/review/new-terms"
    typer.echo(f"Opening New Terms Review: {url}")
    webbrowser.open(url)
