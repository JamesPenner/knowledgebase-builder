"""Stage: Geolocate — resolve GPS coordinates to place hierarchies via point-in-polygon."""
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)


def run_geolocate(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    from src.db.corpus import (
        get_files_with_gps,
        get_geolocated_file_ids,
        open_corpus,
        parse_gps_value,
        update_pipeline_checkpoint,
        upsert_geolabel,
    )
    from src.geo.loader import load_all_regions
    from src.geo.resolver import resolve_point

    kb_folder = kb_path.parent
    regions = load_all_regions(kb_folder)
    if not regions:
        logger.warning(
            "geolocate: no region data found in %s/reference/geo/ — "
            "run 'enrich geolocate download' to fetch Natural Earth data",
            kb_folder,
        )
        progress.done()
        return

    corpus_conn = open_corpus(corpus_path)
    try:
        files = get_files_with_gps(corpus_conn)
        already_done = get_geolocated_file_ids(corpus_conn)

        pending = [r for r in files if r["id"] not in already_done]
        total = len(pending)
        processed = 0

        for i, row in enumerate(pending):
            if cancel_event.is_set():
                break
            progress.update(i + 1, total, f"Geolocate: {Path(row['path']).name}")

            label = resolve_point(parse_gps_value(row["lat"]), parse_gps_value(row["lon"]), regions)
            if label is not None:
                upsert_geolabel(corpus_conn, row["id"], label)
                processed += 1

            if i % 100 == 0:
                corpus_conn.commit()

        corpus_conn.commit()
        update_pipeline_checkpoint(corpus_conn, "geolocate", processed, 0, 0)
        corpus_conn.commit()
        progress.done()
        logger.info("geolocate: resolved %d / %d GPS files", processed, total)
    finally:
        corpus_conn.close()
