import logging
from pathlib import Path

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(help="Voice embedding and speaker identity matching", invoke_without_command=True)


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
    return folder / "corpus.db", folder / "knowledge.db"


@app.callback(invoke_without_command=True)
def voice(
    ctx: typer.Context,
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    force: bool = typer.Option(False, "--force", help="Re-process already-processed files"),
) -> None:
    """Embed speaker voice in audio/video files and match to known people.

    Uses Resemblyzer to compute 256D d-vector embeddings.
    Run 'enrich voice download' to install the required package.
    """
    if ctx.invoked_subcommand is not None:
        return

    from src.config import load_config
    from src.db.corpus import open_corpus, reset_voice_embeddings
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.voice import run_voice

    corpus_path, kb_path = _resolve_kb(kb)
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if force:
        conn = open_corpus(corpus_path)
        reset_voice_embeddings(conn)
        conn.close()
        typer.echo("Voice embeddings reset.")

    typer.echo("Running voice embedding…")
    result = run_voice(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo(
        f"Done. Files processed: {result['files_processed']}, "
        f"matched: {result['files_matched']}, "
        f"skipped (no audio): {result['files_skipped']}, "
        f"errors: {result['errors']}."
    )


@app.command("download")
def download(
    models_dir: str = typer.Option("models", "--models-dir", help="Directory to install into (informational)"),
) -> None:
    """Install the Resemblyzer voice embedding package via pip."""
    import subprocess
    import sys

    typer.echo("Installing resemblyzer and librosa…")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "resemblyzer", "librosa"],
        capture_output=False,
    )
    if result.returncode != 0:
        typer.echo("Installation failed. Run manually: pip install resemblyzer librosa", err=True)
        raise typer.Exit(1)
    typer.echo("Done. Resemblyzer and librosa are installed.")


@app.command("diarize")
def diarize(
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    force: bool = typer.Option(False, "--force", help="Re-process already-processed files"),
) -> None:
    """Diarize audio/video files by speaker and match segments to known people.

    Requires pyannote.audio. Run 'enrich voice diarize-download' first.
    Set HF_TOKEN environment variable or use --hf-token with diarize-download.
    """
    from src.config import load_config
    from src.db.corpus import open_corpus, reset_voice_segments
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.voice import run_voice_diarize

    corpus_path, kb_path = _resolve_kb(kb)
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if force:
        conn = open_corpus(corpus_path)
        reset_voice_segments(conn)
        conn.close()
        typer.echo("Voice segments reset.")

    typer.echo("Running speaker diarization…")
    result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo(
        f"Done. Files: {result['files_processed']}, "
        f"segments found: {result['segments_found']}, "
        f"matched: {result['segments_matched']}, "
        f"errors: {result['errors']}."
    )


@app.command("diarize-download")
def diarize_download(
    hf_token: str | None = typer.Option(None, "--hf-token", help="HuggingFace token (falls back to HF_TOKEN env var)"),
    model_id: str = typer.Option("pyannote/speaker-diarization-3.1", "--model-id", help="pyannote model ID"),
) -> None:
    """Download the pyannote speaker diarization model from HuggingFace.

    Requires accepting the pyannote licence at hf.co/pyannote/speaker-diarization-3.1.
    Provide your HuggingFace token via --hf-token or the HF_TOKEN environment variable.
    """
    import os

    try:
        from pyannote.audio import Pipeline
    except ImportError:
        typer.echo("Error: pyannote.audio not installed. Run: pip install pyannote.audio", err=True)
        raise typer.Exit(1)

    token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        typer.echo(
            "Error: no HuggingFace token found.\n"
            "Provide --hf-token TOKEN or set the HF_TOKEN environment variable.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Downloading {model_id} from HuggingFace…")
    try:
        Pipeline.from_pretrained(model_id, use_auth_token=token)
        typer.echo("Done. Model cached locally by HuggingFace Hub.")
    except Exception as exc:
        typer.echo(f"Download failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command("attribute")
def attribute(
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    force: bool = typer.Option(False, "--force", help="Re-attribute all segments (clear existing labels first)"),
) -> None:
    """Attribute speaker labels to transcript segments via time-overlap matching.

    Requires both 'enrich voice diarize' and the Transcribe stage to have run.
    """
    from src.config import load_config
    from src.db.corpus import open_corpus, reset_transcript_speaker_labels
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.attribute_speakers import run_attribute_speakers

    corpus_path, kb_path = _resolve_kb(kb)
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if force:
        conn = open_corpus(corpus_path)
        n = reset_transcript_speaker_labels(conn)
        conn.commit()
        conn.close()
        typer.echo(f"Cleared {n} speaker label(s).")

    typer.echo("Attributing speakers to transcript segments…")
    result = run_attribute_speakers(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo(
        f"Done. Files: {result['files_processed']}, "
        f"attributed: {result['segments_attributed']}, "
        f"skipped (no overlap): {result['segments_skipped']}, "
        f"errors: {result['errors']}."
    )
