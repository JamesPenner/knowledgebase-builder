import logging
from pathlib import Path

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(help="Face detection and identity matching", invoke_without_command=True)

_SCRFD_REPO = "deepinsight/insightface"
_SCRFD_FILE = "models/buffalo_l/det_10g.onnx"
_ARCFACE_REPO = "deepinsight/insightface"
_ARCFACE_FILE = "models/buffalo_l/w600k_r50.onnx"


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
def face(
    ctx: typer.Context,
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    force: bool = typer.Option(False, "--force", help="Re-process already-processed files"),
) -> None:
    """Detect faces in image files and match to known people.

    Requires models.face_detection and models.face_embedding in config.yaml.
    Run 'enrich face download' to fetch models.
    """
    if ctx.invoked_subcommand is not None:
        return

    from src.config import load_config
    from src.db.corpus import open_corpus, reset_face_regions
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.face import run_face

    corpus_path, kb_path = _resolve_kb(kb)
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if not config.face_detection_model or not config.face_embedding_model:
        typer.echo(
            "Error: face models not configured.\n"
            "Set models.face_detection and models.face_embedding in config.yaml,\n"
            "or run 'enrich face download' to fetch models.",
            err=True,
        )
        raise typer.Exit(1)

    if force:
        conn = open_corpus(corpus_path)
        reset_face_regions(conn)
        conn.close()
        typer.echo("Face regions reset.")

    typer.echo("Running face detection…")
    result = run_face(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo(
        f"Done. Files: {result['files_processed']}, "
        f"faces detected: {result['faces_detected']}, "
        f"matched: {result['faces_matched']}, "
        f"errors: {result['errors']}."
    )


@app.command("download")
def download(
    detection_model: bool = typer.Option(False, "--detection-model", help="Download SCRFD face detection model"),
    embedding_model: bool = typer.Option(False, "--embedding-model", help="Download ArcFace face embedding model"),
    models_dir: str = typer.Option("models", "--models-dir", help="Directory to save models into"),
) -> None:
    """Download SCRFD (detection) and ArcFace (embedding) ONNX models."""
    if not detection_model and not embedding_model:
        typer.echo("Specify --detection-model and/or --embedding-model.", err=True)
        raise typer.Exit(1)

    models_path = Path(models_dir)
    models_path.mkdir(parents=True, exist_ok=True)

    if detection_model:
        _download_scrfd(models_path)
    if embedding_model:
        _download_arcface(models_path)


def _download_scrfd(models_path: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        typer.echo("Error: huggingface_hub not installed. Run: pip install huggingface_hub", err=True)
        raise typer.Exit(1)

    dest_dir = models_path / "face"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "det_10g.onnx"

    if dest.exists():
        typer.echo(f"SCRFD model already present at {dest}.")
    else:
        typer.echo(f"Downloading SCRFD face detection model from {_SCRFD_REPO}…")
        cached = hf_hub_download(repo_id=_SCRFD_REPO, filename=_SCRFD_FILE)
        import shutil
        shutil.copy(cached, dest)
        typer.echo(f"Saved to {dest}.")

    _update_config("face_detection", str(dest))
    typer.echo(f"Config updated: models.face_detection = {dest}")


def _download_arcface(models_path: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        typer.echo("Error: huggingface_hub not installed. Run: pip install huggingface_hub", err=True)
        raise typer.Exit(1)

    dest_dir = models_path / "face"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "w600k_r50.onnx"

    if dest.exists():
        typer.echo(f"ArcFace model already present at {dest}.")
    else:
        typer.echo(f"Downloading ArcFace face embedding model from {_ARCFACE_REPO}…")
        cached = hf_hub_download(repo_id=_ARCFACE_REPO, filename=_ARCFACE_FILE)
        import shutil
        shutil.copy(cached, dest)
        typer.echo(f"Saved to {dest}.")

    _update_config("face_embedding", str(dest))
    typer.echo(f"Config updated: models.face_embedding = {dest}")


def _update_config(key: str, value: str) -> None:
    """Update models.<key> in config.yaml (line-based, preserves comments)."""
    config_path = Path("config.yaml")
    if not config_path.exists():
        config_path.write_text(f"models:\n  {key}: {value}\n", encoding="utf-8")
        return

    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_models = False
    key_line = f"  {key}:"
    updated = False
    result = []

    for line in lines:
        stripped = line.rstrip()
        if stripped == "models:":
            in_models = True
            result.append(line)
            continue
        if in_models and stripped.startswith(key_line):
            result.append(f"  {key}: {value}\n")
            updated = True
            continue
        if in_models and stripped and not stripped.startswith(" ") and not stripped.startswith("#"):
            in_models = False
        result.append(line)

    if not updated:
        out = "".join(result)
        if "models:" in out:
            out = out.replace("models:\n", f"models:\n  {key}: {value}\n", 1)
        else:
            out += f"\nmodels:\n  {key}: {value}\n"
        config_path.write_text(out, encoding="utf-8")
    else:
        config_path.write_text("".join(result), encoding="utf-8")
