import typer

app = typer.Typer(help="Corpus commands")


def _resolve_kb(name: str | None) -> tuple:
    from pathlib import Path
    from src.db.registry import get_active_kb_path, get_kb_path, open_registry

    reg = open_registry(Path("."))
    try:
        if name:
            folder = get_kb_path(reg, name)
        else:
            folder = get_active_kb_path(reg)
            if folder is None:
                typer.echo("Error: no active KB. Use --kb <name> or run 'enrich kb create'.", err=True)
                raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    return folder / "corpus.db", folder / "knowledge.db"


@app.command("stats")
def stats(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Show corpus coverage statistics for each pipeline stage."""
    from src.db.corpus import get_corpus_stats, open_corpus
    from src.db.kb import open_kb

    corpus_path, kb_path = _resolve_kb(kb)
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    try:
        s = get_corpus_stats(corpus_conn, kb_conn)
    finally:
        corpus_conn.close()
        kb_conn.close()

    files = s["files"]
    typer.echo("Files")
    typer.echo(f"  Total:       {files['total']}")
    for ftype, count in sorted(files["by_type"].items()):
        typer.echo(f"  {ftype.capitalize():<12} {count}")
    typer.echo(f"  Duplicates:  {files['duplicates']}")
    typer.echo(f"  Sources:     {files['sources']}")
    typer.echo("")

    typer.echo(f"{'Stage':<12} {'Covered':>8} {'Eligible':>9} {'Elig%':>7} {'Total':>7} {'Total%':>7}  Last Run")
    typer.echo("-" * 72)

    def _fmt_run(ts: str | None) -> str:
        return ts if ts else "—"

    ingest = s["stages"]["ingest"]
    typer.echo(
        f"{'ingest':<12} {'—':>8} {'—':>9} {'—':>7} {'—':>7} {'—':>7}  {_fmt_run(ingest['last_run_at'])}"
    )

    for stage_name in ("hash", "describe", "transcribe", "retag"):
        st = s["stages"][stage_name]
        typer.echo(
            f"{stage_name:<12} {st['covered']:>8} {st['eligible']:>9}"
            f" {st['eligible_pct']:>6.1f}% {st['total']:>7} {st['total_pct']:>6.1f}%"
            f"  {_fmt_run(st['last_run_at'])}"
        )

    typer.echo("")
    vocab = s["vocabulary"]
    typer.echo(f"Vocabulary:  {vocab['terms']} terms, {vocab['with_synonyms']} with synonyms")
    ae = s["aesthetic"]
    typer.echo(f"Aesthetic:   NIMA: {ae['nima_scored']}  CLIP: {ae['clip_scored']}  Combined: {ae['combined_rank']}")


@app.command("validate")
def validate(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    export: bool = typer.Option(False, "--export", help="Write export/validation_report.csv"),
) -> None:
    """Check all corpus files for existence and content changes."""
    import threading

    from src.pipeline.progress import NullProgressReporter
    from src.stages.validate import run_validate

    corpus_path, _ = _resolve_kb(kb)
    kb_folder = corpus_path.parent

    cancel = threading.Event()
    result = run_validate(corpus_path, kb_folder, NullProgressReporter(), cancel, export=export)

    typer.echo(
        f"{result['ok']} ok, {result['changed']} changed,"
        f" {result['moved']} moved, {result['missing']} missing"
    )
    if export:
        typer.echo(f"Report written to {kb_folder / 'export' / 'validation_report.csv'}")
