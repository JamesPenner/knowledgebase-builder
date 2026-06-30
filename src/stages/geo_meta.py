import logging

logger = logging.getLogger(__name__)


def run_geo_meta(corpus_path, kb_path, config, progress, cancel, *, scope=None) -> dict:
    """Match file GPS coordinates against registered location entities and write labels."""
    import time as _time
    from src.db.corpus import (
        get_gps_files_without_location_label,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_location_label,
    )
    from src.db.kb import get_entity_table_rows, get_gps_entity_tables, open_kb
    from src.stages.classify_rules import _haversine_m

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    files_processed = 0
    files_matched = 0
    files_unmatched = 0
    error_count = 0
    _start = _time.monotonic()

    try:
        gps_tables = get_gps_entity_tables(kb_conn)
        if not gps_tables:
            update_pipeline_checkpoint(corpus_conn, "geo_meta", 0, 0, 0, 0.0)
            corpus_conn.commit()
            progress.done()
            return {
                "files_processed": 0,
                "files_matched": 0,
                "files_unmatched": 0,
                "errors": 0,
            }

        entity_rows: list[tuple[str, dict]] = []
        for tbl in gps_tables:
            table_name = tbl["table_name"]
            for row in get_entity_table_rows(kb_conn, table_name):
                entity_rows.append((table_name, dict(row)))

        pending = get_gps_files_without_location_label(corpus_conn, scope=scope)
        total = len(pending)
        progress.update(0, total, "Matching GPS to location register…")

        for i, file_row in enumerate(pending):
            if cancel.is_set():
                break

            file_id = file_row["id"]
            file_lat = file_row["lat"]
            file_lon = file_row["lon"]

            if file_lat is None or file_lon is None:
                files_unmatched += 1
                progress.update(i + 1, total)
                continue

            best_dist: float | None = None
            best_row: dict | None = None
            best_table: str | None = None

            for table_name, erow in entity_rows:
                try:
                    elat = float(erow.get("latitude") or 0)
                    elon = float(erow.get("longitude") or 0)
                except (TypeError, ValueError):
                    continue

                threshold = config.geo_meta_default_threshold_m
                try:
                    t = erow.get("threshold_m")
                    if t is not None:
                        threshold = float(t)
                except (TypeError, ValueError):
                    pass

                dist = _haversine_m(file_lat, file_lon, elat, elon)
                if dist <= threshold:
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        best_row = erow
                        best_table = table_name

            if best_row is not None and best_dist is not None:
                upsert_location_label(
                    corpus_conn,
                    file_id,
                    location=best_row.get("location") or best_row.get("name"),
                    city=best_row.get("city"),
                    state=best_row.get("state"),
                    country=best_row.get("country"),
                    country_code=best_row.get("country_code"),
                    distance_m=best_dist,
                    matched_table=best_table or "locations",
                )
                corpus_conn.commit()
                files_matched += 1
            else:
                files_unmatched += 1

            files_processed += 1
            progress.update(i + 1, total)

        update_pipeline_checkpoint(
            corpus_conn, "geo_meta", files_processed, 0, error_count,
            _time.monotonic() - _start,
        )
        corpus_conn.commit()
        progress.done()
    finally:
        corpus_conn.close()
        kb_conn.close()

    return {
        "files_processed": files_processed,
        "files_matched": files_matched,
        "files_unmatched": files_unmatched,
        "errors": error_count,
    }
