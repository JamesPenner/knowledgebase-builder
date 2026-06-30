import json
import threading
import time
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_files_without_exif,
    open_corpus,
    update_pipeline_checkpoint,
    upsert_file_exif,
)
from src.pipeline.progress import ProgressReporter

_BATCH_SIZE = 50


def run_extract_meta(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    scope=None,
) -> None:
    from src.exiftool import ExifTool
    from src.stages.field_registry import generate_field_map

    conn = open_corpus(corpus_path)
    kb_folder = kb_path.parent

    files = get_files_without_exif(conn, scope=scope)
    total = len(files)

    if not files:
        update_pipeline_checkpoint(conn, "extract_meta", files_processed=0)
        conn.close()
        progress.done()
        return

    start = time.monotonic()
    processed = 0

    exiftool_config = kb_folder / "reference" / "ExifTool_Config"
    config_arg = str(exiftool_config) if exiftool_config.exists() else None
    with ExifTool(config.exiftool, config_path=config_arg) as et:
        for batch_start in range(0, total, _BATCH_SIZE):
            if cancel_event.is_set():
                break

            batch = files[batch_start : batch_start + _BATCH_SIZE]
            batch_paths = [Path(r["path"]) for r in batch]
            path_to_id = {r["path"]: r["id"] for r in batch}

            results = et.get_metadata(batch_paths)

            for meta in results:
                source_file = meta.get("SourceFile") or meta.get("File:FileName")
                if source_file is None:
                    continue
                file_id = path_to_id.get(source_file)
                if file_id is None:
                    # ExifTool normalises separators on Windows; also guard against
                    # drive-letter case differences (e.g. D:/ vs d:/).
                    source_norm = source_file.replace("\\", "/").lower()
                    for orig_path, fid in path_to_id.items():
                        if orig_path.replace("\\", "/").lower() == source_norm:
                            file_id = fid
                            break
                if file_id is not None:
                    upsert_file_exif(conn, file_id, json.dumps(meta))
                    processed += 1

            conn.commit()
            progress.update(min(batch_start + _BATCH_SIZE, total), total)

    generate_field_map(conn, kb_folder)

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        conn,
        "extract_meta",
        files_processed=processed,
        duration_seconds=duration,
    )
    conn.close()
    progress.done()
