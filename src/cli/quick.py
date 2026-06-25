import csv
import json
import sys
from pathlib import Path

import typer

app = typer.Typer(help="Stateless quick commands (no KB required)")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".m2ts"}


def _collect_files(path_str: str, exts: set[str], recursive: bool) -> list[Path]:
    root = Path(path_str)
    if root.is_file():
        return [root] if root.suffix.lower() in exts else []
    if not root.is_dir():
        typer.echo(f"Error: path does not exist: {path_str}", err=True)
        raise typer.Exit(1)
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() in exts)


def _write_output(rows: list[dict], output: Path | None, fmt: str, fieldnames: list[str]) -> None:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            output.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            with output.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        typer.echo(f"Output written to {output}")
    else:
        if fmt == "json":
            typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


@app.command("describe")
def quick_describe(
    path: str = typer.Argument(help="File or directory to describe"),
    focus: str = typer.Option("", "--focus", help="Domain guidance string injected into prompts"),
    model_override: str | None = typer.Option(None, "--model", help="Override vision model path"),
    kb: str | None = typer.Option(None, "--kb", help="Path to KB directory; loads its active describe prompt"),
    output: Path | None = typer.Option(None, "--output", help="Write results to file (CSV or JSON)"),
    fmt: str = typer.Option("csv", "--format", help="Output format: csv or json"),
    recursive: bool = typer.Option(False, "--recursive", help="Recurse into subdirectories"),
) -> None:
    """Vision describe — uses the active KB prompt when --kb is given."""
    from src.config import load_config
    from src.stages.describe import ModelLoadError, run_describe_file

    describe_exts = _IMAGE_EXTS | _VIDEO_EXTS
    files = _collect_files(path, describe_exts, recursive)
    if not files:
        typer.echo("No image or video files found.")
        raise typer.Exit(0)

    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if model_override:
        import dataclasses
        config = dataclasses.replace(config, vision_model=model_override)

    if not config.vision_model:
        typer.echo("Error: no vision_model configured. Set models.vision in config.yaml or use --model.", err=True)
        raise typer.Exit(1)

    kb_path: Path | None = None
    if kb:
        kb_path = Path(kb)
        if not (kb_path / "knowledge.db").exists():
            typer.echo(f"Warning: no knowledge.db found at {kb_path} — using default prompt.", err=True)
            kb_path = None

    rows: list[dict] = []
    for file_path in files:
        typer.echo(f"Describing {file_path}…", err=True)
        try:
            description = run_describe_file(file_path, config, focus=focus, kb_path=kb_path)
            import datetime
            rows.append({
                "path": str(file_path),
                "description": description or "",
                "model": config.vision_model,
                "processed_at": datetime.datetime.now().isoformat(),
            })
        except ModelLoadError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
        except Exception as exc:
            typer.echo(f"Warning: {file_path} failed: {exc}", err=True)
            rows.append({"path": str(file_path), "description": "", "model": config.vision_model, "processed_at": ""})

    _write_output(rows, output, fmt, ["path", "description", "model", "processed_at"])


@app.command("transcribe")
def quick_transcribe(
    path: str = typer.Argument(help="Audio/video file or directory to transcribe"),
    model_override: str | None = typer.Option(None, "--model", help="Override audio model path"),
    output: Path | None = typer.Option(None, "--output", help="Write results to file (CSV or JSON)"),
    fmt: str = typer.Option("csv", "--format", help="Output format: csv or json"),
    recursive: bool = typer.Option(False, "--recursive", help="Recurse into subdirectories"),
) -> None:
    """Stateless Whisper transcription — no KB or corpus.db required."""
    from src.config import load_config
    from src.stages.transcribe import ModelLoadError, run_transcribe_file

    transcribe_exts = _AUDIO_EXTS | _VIDEO_EXTS
    files = _collect_files(path, transcribe_exts, recursive)
    if not files:
        typer.echo("No audio or video files found.")
        raise typer.Exit(0)

    config_path = Path("config.yaml") if Path("config.yaml").exists() else None
    config = load_config(config_path)

    if model_override:
        import dataclasses
        config = dataclasses.replace(config, audio_model=model_override)

    if not config.audio_model:
        typer.echo("Error: no audio_model configured. Set models.audio in config.yaml or use --model.", err=True)
        raise typer.Exit(1)

    rows: list[dict] = []
    for file_path in files:
        typer.echo(f"Transcribing {file_path}…", err=True)
        try:
            result = run_transcribe_file(file_path, config)
            if result:
                rows.append({
                    "path": result["path"],
                    "transcript": result["transcript"] or "",
                    "language": result["language"] or "",
                    "duration_ms": result["duration_ms"] or "",
                    "model": result["model"],
                    "processed_at": result["processed_at"],
                })
            else:
                rows.append({"path": str(file_path), "transcript": "", "language": "", "duration_ms": "", "model": config.audio_model, "processed_at": ""})
        except ModelLoadError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
        except Exception as exc:
            typer.echo(f"Warning: {file_path} failed: {exc}", err=True)
            rows.append({"path": str(file_path), "transcript": "", "language": "", "duration_ms": "", "model": config.audio_model, "processed_at": ""})

    _write_output(rows, output, fmt, ["path", "transcript", "language", "duration_ms", "model", "processed_at"])
