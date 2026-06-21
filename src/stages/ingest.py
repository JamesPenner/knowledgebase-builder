import os
import threading
import time
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_sources,
    open_corpus,
    update_pipeline_checkpoint,
    update_source_ingested,
    upsert_file,
)
from src.pipeline.progress import ProgressReporter

_IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp", ".raw", ".cr2", ".cr3", ".nef",
    ".arw", ".dng", ".orf", ".rw2",
}
_VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".flv",
    ".webm", ".mts", ".m2ts",
}
_AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".wma", ".opus"}


def detect_file_type(ext: str) -> str | None:
    """Return 'images', 'video', 'audio', or None for unrecognised extensions."""
    e = ext.lower()
    if e in _IMAGE_EXTS:
        return "images"
    if e in _VIDEO_EXTS:
        return "video"
    if e in _AUDIO_EXTS:
        return "audio"
    return None


def run_ingest(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    incremental: bool = False,
) -> None:
    from datetime import datetime

    conn = open_corpus(corpus_path)
    sources = get_sources(conn)

    if not sources:
        progress.done()
        return

    # Build per-source incremental threshold (mtime must be >= threshold to re-process)
    source_thresholds: dict[int, float | None] = {}
    if incremental:
        for src in sources:
            lia = src["last_ingested_at"]
            if lia:
                try:
                    source_thresholds[src["id"]] = datetime.strptime(lia, "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    source_thresholds[src["id"]] = None
            else:
                source_thresholds[src["id"]] = None

    # First pass: collect all eligible files and count per source
    all_files: list[tuple[int, str, str, bool]] = []  # (source_id, filepath, file_type, matched)
    source_file_counts: dict[int, int] = {}
    for source in sources:
        source_id = source["id"]
        source_file_type = source["file_type"]
        recursive = bool(source["recursive"])
        source_path = Path(source["path"])
        source_file_counts[source_id] = 0

        if not source_path.exists():
            continue

        for dirpath, _dirs, filenames in os.walk(str(source_path)):
            for fname in filenames:
                filepath = os.path.join(dirpath, fname)
                ext = Path(fname).suffix
                detected = detect_file_type(ext)

                if source_file_type == "all" or detected == source_file_type:
                    all_files.append((source_id, filepath, detected or source_file_type, True))
                    source_file_counts[source_id] += 1

            if not recursive:
                break

    total = len(all_files)
    processed = 0
    skipped = 0
    start = time.monotonic()

    # Second pass: upsert files
    batch: list[tuple] = []
    BATCH_SIZE = 100

    for source_id, filepath, file_type, _matched in all_files:
        if cancel_event.is_set():
            break

        try:
            stat = os.stat(filepath)
        except OSError:
            continue

        file_size = stat.st_size
        mtime = stat.st_mtime
        p = Path(filepath)
        filename = p.name
        ext = p.suffix.lower()

        # Incremental shortcut: skip DB upsert for old files already in DB
        if incremental:
            threshold = source_thresholds.get(source_id)
            if threshold is not None and mtime < threshold:
                existing = conn.execute(
                    "SELECT id FROM files WHERE path=?", (filepath,)
                ).fetchone()
                if existing:
                    skipped += 1
                    processed += 1
                    progress.update(processed, total)
                    continue

        # Check if unchanged: same path + size + mtime already in DB
        existing = conn.execute(
            "SELECT id FROM files WHERE path=? AND file_size=? AND mtime=?",
            (filepath, file_size, mtime),
        ).fetchone()
        if existing:
            skipped += 1
            processed += 1
            progress.update(processed, total)
            continue

        batch.append((source_id, filepath, filename, ext, file_type, file_size, mtime))

        if len(batch) >= BATCH_SIZE:
            _flush_batch(conn, batch)
            batch.clear()

        processed += 1
        progress.update(processed, total)

    if batch:
        _flush_batch(conn, batch)

    # Always update last_ingested_at for every source (standing fix)
    for src in sources:
        update_source_ingested(conn, src["id"], source_file_counts.get(src["id"], 0))

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        conn,
        stage="ingest",
        files_processed=processed - skipped,
        files_skipped=skipped,
        duration_seconds=duration,
    )
    conn.close()
    progress.done()


def _flush_batch(conn, batch: list[tuple]) -> None:
    for source_id, filepath, filename, ext, file_type, file_size, mtime in batch:
        upsert_file(conn, source_id, filepath, filename, ext, file_type, file_size, mtime)
    conn.commit()
