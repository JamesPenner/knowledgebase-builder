import csv
import json
import threading
import time
from collections import defaultdict
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_files_with_exif,
    open_corpus,
    update_pipeline_checkpoint,
    upsert_metadata_field,
    upsert_metadata_keyword,
)
from src.pipeline.progress import ProgressReporter

_BATCH_SIZE = 100


def run_extract_fields(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    scope=None,
) -> None:
    from src.pipeline.stage_runner import run_stage_loop

    kb_folder = kb_path.parent
    csv_path = kb_folder / "reference" / "field_map.csv"

    if not csv_path.exists():
        run_stage_loop([], lambda row: None, progress, cancel_event, label="extract_fields")
        return

    with open(csv_path, newline="", encoding="utf-8") as fh:
        raw_map = list(csv.DictReader(fh))

    canonical_groups: dict[str, list[dict]] = defaultdict(list)
    for row in raw_map:
        try:
            row["Priority"] = int(row.get("Priority", 1) or 1)
        except (ValueError, TypeError):
            row["Priority"] = 1
        canonical_groups[row["CanonicalName"]].append(row)
    for group in canonical_groups.values():
        group.sort(key=lambda r: r["Priority"])

    conn = open_corpus(corpus_path)
    try:
        files = get_files_with_exif(conn, scope=scope)
        start = time.monotonic()
        batch_count = [0]

        def _process(file_row):
            meta = json.loads(file_row["metadata_json"])
            for canonical_name, group_rows in canonical_groups.items():
                data_type = group_rows[0].get("DataType", "str")
                if data_type == "keyword_list":
                    field_tag = group_rows[0]["ExifTool_Tag"]
                    raw_value = meta.get(field_tag)
                    if raw_value is None:
                        continue
                    items = raw_value if isinstance(raw_value, list) else [raw_value]
                    for kw in items:
                        kw_str = str(kw).strip()
                        if kw_str:
                            upsert_metadata_keyword(conn, file_row["id"], canonical_name, kw_str)
                else:
                    for field_row in group_rows:
                        raw_value = meta.get(field_row["ExifTool_Tag"])
                        if raw_value is not None:
                            upsert_metadata_field(
                                conn,
                                file_row["id"],
                                canonical_name,
                                field_row["ExifTool_Tag"],
                                str(raw_value),
                                data_type,
                            )
                            break
            batch_count[0] += 1
            if batch_count[0] % _BATCH_SIZE == 0:
                conn.commit()

        processed, errors = run_stage_loop(files, _process, progress, cancel_event, label="extract_fields")
        conn.commit()
        duration = time.monotonic() - start
        update_pipeline_checkpoint(
            conn,
            "extract_fields",
            files_processed=processed,
            duration_seconds=duration,
        )
    finally:
        conn.close()
