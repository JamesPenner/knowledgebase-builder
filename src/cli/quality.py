import csv
import logging
from pathlib import Path

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(help="Technical quality metrics (sharpness, exposure)", invoke_without_command=True)


def _resolve_kb(name: str | None) -> tuple:
    from src.db.registry import get_active_kb_path, get_kb_path, open_registry
    reg = open_registry(Path("."))
    try:
        folder = get_kb_path(reg, name) if name else get_active_kb_path(reg)
        if folder is None:
            typer.echo("Error: no active KB. Use --kb <name> or run 'enrich kb create'.", err=True)
            raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    return folder / "corpus.db", folder / "knowledge.db", folder


@app.callback(invoke_without_command=True)
def quality(
    ctx: typer.Context,
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    force: bool = typer.Option(False, "--force", help="Re-score already-scored files"),
    export: bool = typer.Option(False, "--export", help="Write quality.csv after scoring"),
    min_quality: float | None = typer.Option(None, "--min-quality", help="Only export files with quality_rank >= this value"),
) -> None:
    """Score images and videos with technical quality metrics.

    Computes sharpness (Laplacian variance), exposure (mean luminance),
    highlights and shadow clipping. No model download required.
    """
    if ctx.invoked_subcommand is not None:
        return

    from src.config import load_config
    from src.db.corpus import open_corpus, reset_quality_scores
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.quality import run_quality

    corpus_path, kb_path, kb_folder = _resolve_kb(kb)
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if force:
        conn = open_corpus(corpus_path)
        reset_quality_scores(conn)
        conn.close()
        typer.echo("Quality scores reset.")

    typer.echo("Running technical quality scoring…")
    result = run_quality(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo(f"Done. Scored: {result['scored']} files, errors: {result['errors']}.")

    if export:
        _do_export(corpus_path, kb_folder, min_quality)


def _do_export(corpus_path: Path, kb_folder: Path, min_quality: float | None) -> None:
    from src.db.corpus import get_quality_scores_for_export, open_corpus

    export_dir = kb_folder / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    conn = open_corpus(corpus_path)
    rows = get_quality_scores_for_export(conn, min_quality=min_quality)
    conn.close()

    out_path = export_dir / "quality.csv"
    fieldnames = ["file_path", "sharpness", "exposure", "highlights", "shadows",
                  "quality_rank", "frame_count", "scored_at"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    filter_note = f" (min_quality={min_quality})" if min_quality is not None else ""
    typer.echo(f"Exported {len(rows)} rows to {out_path}{filter_note}.")
