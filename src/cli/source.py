import typer

app = typer.Typer(help="Source directory management")


@app.command("add")
def source_add(
    path: str = typer.Argument(..., help="Path to source directory"),
    kb: str = typer.Option(..., "--kb", help="KB name"),
    file_type: str = typer.Option("all", "--type", help="File type filter: images | video | audio | all"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Walk subdirectories"),
) -> None:
    """Add a source directory to a KB."""
    from pathlib import Path
    from src.db.corpus import add_source, open_corpus
    from src.db.registry import open_registry, get_kb_path

    reg = open_registry(Path("."))
    try:
        kb_folder = get_kb_path(reg, kb)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    corpus_path = kb_folder / "corpus.db"
    conn = open_corpus(corpus_path)
    source_id = add_source(conn, path, file_type, recursive)
    typer.echo(f"Source added (id={source_id}): {path}")


@app.command("list")
def source_list(
    kb: str = typer.Option(..., "--kb", help="KB name"),
) -> None:
    """List source directories for a KB."""
    from pathlib import Path
    from src.db.corpus import get_sources, open_corpus
    from src.db.registry import open_registry, get_kb_path

    reg = open_registry(Path("."))
    try:
        kb_folder = get_kb_path(reg, kb)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    conn = open_corpus(kb_folder / "corpus.db")
    sources = get_sources(conn)
    if not sources:
        typer.echo("No sources configured.")
        return
    for s in sources:
        active = "" if s["removed_at"] is None else " [removed]"
        typer.echo(f"  {s['id']:>3}  {s['path']}  ({s['file_type']}, recursive={bool(s['recursive'])}){active}")
