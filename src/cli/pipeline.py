import typer

app = typer.Typer(help="Pipeline stage commands")


def _resolve_kb(name: str | None) -> tuple:
    """Resolve KB name to (corpus_path, kb_path). Raises typer.Exit on failure."""
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


@app.command("ingest")
def ingest(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    sources: list[str] = typer.Option([], "--sources", help="Source paths to add before ingesting"),
    incremental: bool = typer.Option(False, "--incremental", help="Skip re-checking files older than last ingest run"),
) -> None:
    """Walk source directories and populate the files table (Stage 0)."""
    from pathlib import Path
    from src.config import load_config
    from src.db.corpus import add_source, open_corpus
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.ingest import run_ingest

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    if sources:
        conn = open_corpus(corpus_path)
        for src in sources:
            add_source(conn, src)
            typer.echo(f"Source registered: {src}")
        conn.close()

    typer.echo("Running ingest…")
    run_ingest(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event(), incremental=incremental)
    typer.echo("Ingest complete.")


@app.command("analyse")
def analyse(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Tokenize filenames/paths and classify patterns (Stage 0.5)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.analyse import run_analyse

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running analyse…")
    run_analyse(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Analyse complete.")


@app.command("normalize")
def normalize(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Apply knowledge.db rules to corpus files (Stage 1)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.normalize import run_normalize

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running normalize…")
    run_normalize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Normalize complete.")


@app.command("extract-meta")
def extract_meta(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Run ExifTool on all files and store raw metadata JSON (Stage 1.5)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.extract_meta import run_extract_meta

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running extract-meta…")
    run_extract_meta(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Extract-meta complete.")


@app.command("extract-fields")
def extract_fields(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Parse stored EXIF JSON into metadata fields via field_map.csv (Stage 1.6)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.extract_fields import run_extract_fields

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running extract-fields…")
    run_extract_fields(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Extract-fields complete.")


@app.command("hash")
def hash_files(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Compute SHA-256 and perceptual hashes; mark duplicates via canonical_id (Stage 2)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.hash import run_hash

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running hash…")
    run_hash(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Hash complete.")


@app.command("describe")
def describe(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    force: bool = typer.Option(False, "--force", help="Reset all pending descriptions and re-run"),
) -> None:
    """Run vision model on canonical images and videos (Stage 3a)."""
    from pathlib import Path
    from src.config import load_config
    from src.db.corpus import open_corpus, reset_describe_to_pending
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.describe import run_describe

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    if force:
        conn = open_corpus(corpus_path)
        reset_describe_to_pending(conn)
        conn.close()

    typer.echo("Running describe…")
    run_describe(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Describe complete.")


@app.command("transcribe")
def transcribe(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    force: bool = typer.Option(False, "--force", help="Reset all transcriptions and re-run"),
    retranscribe_model: str | None = typer.Option(
        None, "--retranscribe-model",
        help="Re-transcribe only files previously processed with this model name",
    ),
) -> None:
    """Transcribe audio/video files using Whisper (Stage 3b)."""
    from pathlib import Path
    from src.config import load_config
    from src.db.corpus import open_corpus, reset_transcribe_to_pending
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.transcribe import run_transcribe

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    if force or retranscribe_model:
        conn = open_corpus(corpus_path)
        reset_transcribe_to_pending(conn, model_name=retranscribe_model)
        conn.close()

    typer.echo("Running transcribe…")
    run_transcribe(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Transcribe complete.")


@app.command("entity-match")
def entity_match(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Match files against entity tables (GPS + text) and record results (Stage 1.7)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.entity_match import run_entity_match

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running entity-match…")
    run_entity_match(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Entity-match complete.")


@app.command("classify")
def classify(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Apply classify rules to produce derived tags (Stage 1.8)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.classify import run_classify

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running classify…")
    run_classify(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Classify complete.")


@app.command("temporal")
def temporal(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Derive temporal fields (year/decade/season/time-of-day/holiday) from EXIF dates."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.temporal import run_temporal

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running temporal…")
    run_temporal(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Temporal complete.")


@app.command("suggest")
def suggest(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    level: list[str] = typer.Option(None, "--level", help="Levels to run: a, b (default: a b)"),
    force: bool = typer.Option(False, "--force", help="Delete pending candidates before running"),
) -> None:
    """Extract vocabulary candidates (Stage 4): Level A (spaCy) + Level B (NPMI graph)."""
    from pathlib import Path
    from src.config import load_config
    from src.db.corpus import delete_pending_candidates, open_corpus
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.suggest import run_suggest

    levels = list(level) if level else ["a", "b"]
    invalid = [lv for lv in levels if lv not in ("a", "b")]
    if invalid:
        typer.echo(f"Error: unknown level(s): {', '.join(invalid)}. Only 'a' and 'b' are supported.", err=True)
        raise typer.Exit(1)

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    if force:
        conn = open_corpus(corpus_path)
        deleted = delete_pending_candidates(conn)
        conn.commit()
        conn.close()
        typer.echo(f"Cleared {deleted} pending candidates.")

    typer.echo(f"Running suggest (levels: {', '.join(levels)})…")
    run_suggest(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event(), levels=levels)
    typer.echo("Suggest complete.")


@app.command("retag")
def retag(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    force: bool = typer.Option(False, "--force", help="Reset all retag output to pending before running"),
) -> None:
    """Re-tag descriptions against the vocabulary using a text LLM (Stage 5)."""
    from pathlib import Path
    from src.config import load_config
    from src.db.corpus import open_corpus, reset_retag_to_pending
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.retag import run_retag

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    if force:
        typer.echo(
            "Warning: Re-running Retag will not automatically clear Write-back results. "
            "Run 'enrich pipeline writeback --force' afterward if needed.",
            err=True,
        )
        conn = open_corpus(corpus_path)
        reset_retag_to_pending(conn)
        conn.close()

    typer.echo("Running retag…")
    run_retag(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo("Retag complete.")


@app.command("summarize")
def summarize_cmd(
    kb: str = typer.Option(..., "--kb", help="KB name"),
    force: bool = typer.Option(False, "--force", help="Reset done summaries to pending and re-run"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress progress output"),
) -> None:
    """Stage 3c: synthesise describe + transcribe outputs into per-file summaries."""
    from pathlib import Path
    from src.config import load_config
    from src.db.corpus import open_corpus, reset_summarize_to_pending
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.summarize import run_summarize

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    if force:
        conn = open_corpus(corpus_path)
        reset_summarize_to_pending(conn)
        conn.close()

    if not quiet:
        typer.echo("Running summarize…")
    run_summarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    if not quiet:
        typer.echo("Summarize complete.")


@app.command("writeback")
def writeback(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    force: bool = typer.Option(False, "--force", help="Write to all files regardless of sync status"),
) -> None:
    """Sync descriptions and keyword tags to file XMP metadata via ExifTool (Stage 6)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.writeback import run_writeback

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running writeback…")
    run_writeback(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event(), force=force)
    typer.echo("Writeback complete.")


@app.command("export")
def export(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
    section: str | None = typer.Option(
        None, "--section",
        help="Export a single section: vocabulary|corrections|patterns|field-map|entities",
    ),
) -> None:
    """Export portable KB bundle to export/ folder (Stage 7)."""
    from pathlib import Path
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.export import run_export

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    typer.echo("Running export…")
    run_export(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event(), section=section)
    export_dir = corpus_path.parent / "export"
    typer.echo(f"Export complete → {export_dir}")


@app.command("run")
def run(
    kb: str | None = typer.Option(None, "--kb", help="KB name"),
) -> None:
    """Run all pending pipeline stages in order, pausing at touchpoints."""
    from pathlib import Path
    from src.config import load_config
    from src.db.corpus import get_completed_stages, open_corpus
    from src.pipeline.dag import resolve_plan

    corpus_path, kb_path = _resolve_kb(kb)
    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)

    conn = open_corpus(corpus_path)
    completed = get_completed_stages(conn)
    conn.close()

    try:
        plan = resolve_plan("export", completed)
    except ValueError:
        plan = []

    if not plan:
        typer.echo("Nothing to run — all stages complete.")
        return

    _stage_runners = {
        "ingest": _run_ingest_stage,
        "analyse": _run_analyse_stage,
        "normalize": _run_normalize_stage,
        "extract_meta": _run_extract_meta_stage,
        "extract_fields": _run_extract_fields_stage,
        "hash": _run_hash_stage,
        "describe": _run_describe_stage,
        "transcribe": _run_transcribe_stage,
        "entity_match": _run_entity_match_stage,
        "classify": _run_classify_stage,
        "suggest": _run_suggest_stage,
        "retag": _run_retag_stage,
        "writeback": _run_writeback_stage,
        "export": _run_export_stage,
    }

    for step in plan:
        if isinstance(step, dict) and "touchpoint" in step:
            tp = step["touchpoint"]
            typer.echo(f"\n[→] Paused: {tp} required")
            cfg = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)
            typer.echo(f"    Open http://{cfg.host}:{cfg.port}/review/normalise")
            typer.echo("    Run 'enrich pipeline run' again after review to continue.")
            return
        elif isinstance(step, str):
            runner = _stage_runners.get(step)
            if runner is None:
                typer.echo(f"[!] Stage '{step}' not yet implemented — stopping.")
                return
            typer.echo(f"[→] Running: {step}")
            runner(corpus_path, kb_path, config)
            typer.echo(f"[✓] {step}")


def _run_ingest_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.ingest import run_ingest
    run_ingest(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_analyse_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.analyse import run_analyse
    run_analyse(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_normalize_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.normalize import run_normalize
    run_normalize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_extract_meta_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.extract_meta import run_extract_meta
    run_extract_meta(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_extract_fields_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.extract_fields import run_extract_fields
    run_extract_fields(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_hash_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.hash import run_hash
    run_hash(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_describe_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.describe import run_describe
    run_describe(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_transcribe_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.transcribe import run_transcribe
    run_transcribe(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_entity_match_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.entity_match import run_entity_match
    run_entity_match(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_classify_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.classify import run_classify
    run_classify(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_suggest_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.suggest import run_suggest
    run_suggest(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_retag_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.retag import run_retag
    run_retag(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_writeback_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.writeback import run_writeback
    run_writeback(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())


def _run_export_stage(corpus_path, kb_path, config) -> None:
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.export import run_export
    run_export(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
