import csv
import logging
import math
from pathlib import Path

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(help="Aesthetic scoring (NIMA + CLIP)", invoke_without_command=True)

_NIMA_MODEL_REPO = "cromsc/nima-mobilenet-aesthetic"
_NIMA_MODEL_FILE = "nima_mobilenet_aesthetic.onnx"
_CLIP_VISUAL_URL = "https://huggingface.co/immich-app/ViT-B-32__openai/resolve/main/visual/model.onnx"
_CLIP_WEIGHTS_URL = "https://github.com/LAION-AI/aesthetic-predictor/blob/main/sa_0_4_vit_b_32_linear.pth?raw=true"
_CLIP_MODEL_DIR = "clip_b32_laion"


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
def aesthetic(
    ctx: typer.Context,
    kb: str | None = typer.Option(None, "--kb", help="KB name (defaults to active KB)"),
    force: bool = typer.Option(False, "--force", help="Re-score already-scored files"),
    writeback: bool = typer.Option(False, "--writeback", help="Write combined_rank as XMP:Rating via ExifTool"),
    export: bool = typer.Option(False, "--export", help="Write aesthetic.csv after scoring"),
    model: str = typer.Option("combined_rank", "--model", help="Model to filter on when --export is used"),
    min_score: float | None = typer.Option(None, "--min-score", help="Only export files with model score >= this value"),
) -> None:
    """Score images with NIMA and/or CLIP aesthetic models.

    Requires models.aesthetic_nima and/or models.aesthetic_clip in config.yaml.
    Run 'enrich aesthetic download --nima-model' or '--clip-model' to fetch models.
    """
    if ctx.invoked_subcommand is not None:
        return

    from src.config import load_config
    from src.db.corpus import (
        open_corpus,
        reset_aesthetic_scores,
    )
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.aesthetic import run_aesthetic

    corpus_path, kb_path = _resolve_kb(kb)
    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if not config.aesthetic_nima and not config.aesthetic_clip:
        typer.echo(
            "Error: no aesthetic models configured.\n"
            "Set models.aesthetic_nima and/or models.aesthetic_clip in config.yaml,\n"
            "or run 'enrich aesthetic download --nima-model' to fetch models.",
            err=True,
        )
        raise typer.Exit(1)

    if force:
        conn = open_corpus(corpus_path)
        reset_aesthetic_scores(conn)
        conn.close()
        typer.echo("Aesthetic scores reset.")

    typer.echo("Running aesthetic scoring…")
    result = run_aesthetic(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
    typer.echo(
        f"Done. NIMA: {result['nima_scored']} scored, "
        f"CLIP: {result['clip_scored']} scored, "
        f"combined_rank: {result['combined_computed']} computed, "
        f"errors: {result['errors']}."
    )

    if writeback and result["combined_computed"] > 0:
        _do_writeback(corpus_path, config)

    if export:
        _do_export(corpus_path, kb_path, model, min_score)


def _do_writeback(corpus_path: Path, config) -> None:
    """Write combined_rank scores as XMP:Rating (1–5 stars) via ExifTool."""
    from src.db.corpus import get_aesthetic_scores_for_export, open_corpus
    from src.exiftool import ExifTool

    conn = open_corpus(corpus_path)
    rows = get_aesthetic_scores_for_export(conn, model_name="combined_rank")
    conn.close()

    if not rows:
        typer.echo("No combined_rank scores to write back.")
        return

    typer.echo(f"Writing XMP:Rating to {len(rows)} files…")
    et = ExifTool(executable=config.exiftool)
    written = 0
    for row in rows:
        score = row.get("combined_rank")
        if score is None:
            continue
        rating = max(1, math.ceil(score * 5))
        try:
            et.write_metadata(row["file_path"], {"XMP:Rating": str(rating)})
            written += 1
        except Exception as exc:
            logger.warning("Writeback failed for %s: %s", row["file_path"], exc)
    typer.echo(f"XMP:Rating written to {written} files.")


def _do_export(
    corpus_path: Path,
    kb_path: Path,
    model: str,
    min_score: float | None,
) -> None:
    """Write export/aesthetic.csv."""
    from src.db.corpus import get_aesthetic_scores_for_export, open_corpus
    from src.db.kb import open_kb

    kb_conn = open_kb(kb_path)
    kb_folder = kb_path.parent
    export_dir = kb_folder / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    kb_conn.close()

    conn = open_corpus(corpus_path)
    rows = get_aesthetic_scores_for_export(conn, model_name=model if min_score is not None else None, min_score=min_score)
    conn.close()

    out_path = export_dir / "aesthetic.csv"
    fieldnames = ["file_path", "nima_score", "clip_score", "combined_rank", "band", "scored_at"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    filter_note = f" (model={model}, min_score={min_score})" if min_score is not None else ""
    typer.echo(f"Exported {len(rows)} rows to {out_path}{filter_note}.")


@app.command("download")
def download(
    nima_model: bool = typer.Option(False, "--nima-model", help="Download NIMA MobileNet aesthetic model"),
    clip_model: bool = typer.Option(False, "--clip-model", help="Download CLIP ViT-B/32 + LAION weights"),
    models_dir: str = typer.Option("models", "--models-dir", help="Directory to save models into"),
) -> None:
    """Download aesthetic model weights and update config.yaml."""
    if not nima_model and not clip_model:
        typer.echo("Specify --nima-model and/or --clip-model.", err=True)
        raise typer.Exit(1)

    models_path = Path(models_dir)
    models_path.mkdir(parents=True, exist_ok=True)

    if nima_model:
        _download_nima(models_path)
    if clip_model:
        _download_clip(models_path)


def _download_nima(models_path: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        typer.echo("Error: huggingface_hub not installed. Run: pip install huggingface_hub", err=True)
        raise typer.Exit(1)

    dest_dir = models_path / "aesthetic"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _NIMA_MODEL_FILE

    if dest.exists():
        typer.echo(f"NIMA model already present at {dest}.")
    else:
        typer.echo(f"Downloading NIMA model from {_NIMA_MODEL_REPO}…")
        cached = hf_hub_download(repo_id=_NIMA_MODEL_REPO, filename=_NIMA_MODEL_FILE)
        import shutil
        shutil.copy(cached, dest)
        typer.echo(f"Saved to {dest}.")

    _update_config("aesthetic_nima", str(dest))
    typer.echo(f"Config updated: models.aesthetic_nima = {dest}")


def _download_clip(models_path: Path) -> None:
    import urllib.request

    dest_dir = models_path / _CLIP_MODEL_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    visual_dest = dest_dir / "visual.onnx"
    linear_dest = dest_dir / "linear.npz"

    if not visual_dest.exists():
        typer.echo("Downloading CLIP visual encoder (~352 MB)…")
        urllib.request.urlretrieve(_CLIP_VISUAL_URL, visual_dest)
        typer.echo(f"Saved to {visual_dest}.")
    else:
        typer.echo(f"CLIP visual model already present at {visual_dest}.")

    if not linear_dest.exists():
        typer.echo("Downloading LAION linear weights…")
        pth_tmp = dest_dir / "sa_0_4_vit_b_32_linear.pth"
        urllib.request.urlretrieve(_CLIP_WEIGHTS_URL, pth_tmp)
        _extract_laion_weights(pth_tmp, linear_dest)
        pth_tmp.unlink(missing_ok=True)
        typer.echo(f"Saved to {linear_dest}.")
    else:
        typer.echo(f"CLIP linear weights already present at {linear_dest}.")

    _update_config("aesthetic_clip", str(dest_dir))
    typer.echo(f"Config updated: models.aesthetic_clip = {dest_dir}")


def _extract_laion_weights(pth_path: Path, out_npz: Path) -> None:
    """Read PyTorch zip, extract weight [512] and bias [1] tensors, save as npz."""
    import re
    import struct
    import zipfile

    import numpy as np

    weight = None
    bias = None

    with zipfile.ZipFile(pth_path) as zf:
        for name in zf.namelist():
            # Support both old *.storage format and new archive/data/<int> format
            if not (name.endswith(".storage") or re.match(r"archive/data/\d+$", name)):
                continue
            data = zf.read(name)
            floats = struct.unpack(f"{len(data) // 4}f", data[: (len(data) // 4) * 4])
            if len(floats) == 512:
                weight = np.array(floats, dtype=np.float32)
            elif len(floats) == 1:
                bias = np.array(floats, dtype=np.float32)

    if weight is None or bias is None:
        raise RuntimeError("Could not extract weight/bias tensors from LAION .pth file.")

    np.savez(str(out_npz), weight=weight, bias=bias)


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
        # Match "models:" with optional trailing comment/whitespace
        if stripped == "models:" or stripped.startswith("models:") and (stripped[7:].lstrip().startswith("#") or stripped[7:].strip() == ""):
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
        # Insert after the models: line (handles trailing comments)
        import re as _re
        out = _re.sub(r"(models:[^\n]*\n)", rf"\g<1>  {key}: {value}\n", out, count=1)
        if f"  {key}:" not in out:
            out += f"\nmodels:\n  {key}: {value}\n"
        config_path.write_text(out, encoding="utf-8")
    else:
        config_path.write_text("".join(result), encoding="utf-8")


