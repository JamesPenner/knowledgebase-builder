"""Stage 6 — Write-back: ExifTool syncs descriptions + keyword tags to file XMP metadata."""
import csv
import json
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50


def _read_writeback_fields(kb_folder: Path, config: Config) -> list[dict]:
    """Return rows from field_map.csv where write_back=true; fallback to config fields."""
    field_map = kb_folder / "reference" / "field_map.csv"
    if field_map.exists():
        fields = []
        with open(field_map, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("write_back", "").strip().lower() in ("true", "1", "yes"):
                    fields.append(row)
        if fields:
            return fields

    # Fallback: config.writeback_fields; treat all as keyword_list
    return [
        {"field_name": f, "canonical_name": f, "value_type": "keyword_list"}
        for f in config.writeback_fields
    ]


def _resolve_keywords(
    tags: list[str],
    kb_conn,
    include_synonyms: bool,
) -> list[str]:
    """Expand tags with synonyms per vocabulary settings; return sorted deduplicated list."""
    result: set[str] = set(tags)
    for term in tags:
        row = kb_conn.execute(
            "SELECT synonyms_json, write_synonyms FROM vocabulary WHERE term=?", (term,)
        ).fetchone()
        if row is None:
            continue
        use_synonyms = row["write_synonyms"]
        if use_synonyms is None:
            use_synonyms = include_synonyms
        else:
            use_synonyms = bool(use_synonyms)
        if use_synonyms:
            try:
                syns = json.loads(row["synonyms_json"]) or []
                result.update(syns)
            except (ValueError, TypeError):
                pass
    return sorted(result)


def _resolve_summarize_field(kb_folder: Path, canonical_name: str) -> dict | None:
    """Look up ExifTool field_name for canonical_name in field_map.csv."""
    if not canonical_name:
        return None
    field_map = kb_folder / "reference" / "field_map.csv"
    if not field_map.exists():
        return None
    with open(field_map, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("canonical_name", "").strip() == canonical_name:
                return {
                    "field_name": row["field_name"],
                    "canonical_name": row["canonical_name"],
                    "value_type": "text",
                }
    return None


def _build_tag_set(
    corpus_conn,
    kb_conn,
    file_id: int,
    include_synonyms: bool,
) -> tuple[list[str], str | None]:
    """Return (keywords, description_text) for a file."""
    retag_row = corpus_conn.execute(
        "SELECT tags_json, refined_description FROM retag_output WHERE file_id=?",
        (file_id,),
    ).fetchone()

    retag_tags: list[str] = []
    description: str | None = None

    if retag_row:
        try:
            retag_tags = json.loads(retag_row["tags_json"]) or []
        except (ValueError, TypeError):
            retag_tags = []
        description = retag_row["refined_description"]

    if not description:
        desc_row = corpus_conn.execute(
            "SELECT description_normalized, description_raw FROM descriptions WHERE file_id=?",
            (file_id,),
        ).fetchone()
        if desc_row:
            description = desc_row["description_normalized"] or desc_row["description_raw"]

    derived = [
        r["tag"]
        for r in corpus_conn.execute(
            "SELECT tag FROM file_derived_tags WHERE file_id=?", (file_id,)
        ).fetchall()
    ]

    all_tags = list(dict.fromkeys(retag_tags + derived))
    keywords = _resolve_keywords(all_tags, kb_conn, include_synonyms)
    return keywords, description


def run_writeback(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    force: bool = False,
) -> None:
    from src.db.corpus import (
        log_writeback,
        open_corpus,
        update_pipeline_checkpoint,
        update_writeback_kb_version,
        upsert_gps_mask,
    )
    from src.db.kb import open_kb
    from src.exiftool import ExifTool
    from src.privacy import apply_gps_mask, find_matching_zone, load_privacy_zones
    from src.stages.sync import get_current_kb_version, get_stale_files

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        current_version = get_current_kb_version(kb_conn)

        if force:
            stale = corpus_conn.execute(
                "SELECT id, path FROM files ORDER BY id"
            ).fetchall()
        else:
            stale = get_stale_files(corpus_conn, kb_conn)

        if not stale:
            update_pipeline_checkpoint(corpus_conn, "writeback", 0, 0, 0)
            progress.done()
            return

        kb_folder = kb_path.parent
        privacy_zones = load_privacy_zones(kb_folder)
        write_fields = _read_writeback_fields(kb_folder, config)
        keyword_fields = [f for f in write_fields if f.get("value_type") == "keyword_list"]
        desc_fields = [
            f for f in write_fields
            if f.get("value_type") == "text" and f.get("canonical_name") == "description"
        ]
        summary_field = _resolve_summarize_field(kb_folder, config.summarize_output_field) if config.summarize_output_field else None
        desc_field_names = {f["field_name"] for f in desc_fields}

        total = len(stale)
        processed = skipped = errors = 0

        try:
            et = ExifTool(config.exiftool)
        except RuntimeError as exc:
            logger.error("Writeback: %s", exc)
            update_pipeline_checkpoint(corpus_conn, "writeback", 0, 0, 1)
            return

        with et:
            for i, row in enumerate(stale):
                if cancel_event.is_set():
                    break

                progress.update(i, total, f"Write-back: {i + 1}/{total}")
                file_id = row["id"]
                file_path = Path(row["path"])

                if not file_path.exists():
                    skipped += 1
                    continue

                keywords, description = _build_tag_set(
                    corpus_conn, kb_conn, file_id, config.include_synonyms
                )

                # Handle accepted GPS proposals
                gps_row = corpus_conn.execute(
                    "SELECT proposed_lat, proposed_lon FROM gps_proposals"
                    " WHERE file_id=? AND status='accepted' ORDER BY id LIMIT 1",
                    (file_id,),
                ).fetchone()

                # Fetch original extracted GPS (for masking even without a proposal)
                orig_gps = corpus_conn.execute(
                    "SELECT CAST(lat.value AS REAL) AS lat, CAST(lon2.value AS REAL) AS lon"
                    " FROM file_metadata_fields lat"
                    " JOIN file_metadata_fields lon2 ON lon2.file_id=lat.file_id"
                    "   AND lon2.canonical_name='exif_gps_lon'"
                    " WHERE lat.file_id=? AND lat.canonical_name='exif_gps_lat'",
                    (file_id,),
                ).fetchone()

                tags: list[tuple[str, str]] = []

                for kf in keyword_fields:
                    field_name = kf["field_name"]
                    for kw in keywords:
                        tags.append((field_name, kw))

                for df in desc_fields:
                    if description:
                        tags.append((df["field_name"], description))

                if summary_field:
                    sum_row = corpus_conn.execute(
                        "SELECT summary_text FROM file_summaries WHERE file_id=? AND status='done'",
                        (file_id,),
                    ).fetchone()
                    if sum_row and sum_row["summary_text"]:
                        sf_name = summary_field["field_name"]
                        if sf_name in desc_field_names:
                            tags = [(fn, v) for fn, v in tags if fn != sf_name]
                        tags.append((sf_name, sum_row["summary_text"]))

                # Determine GPS source: proposal takes precedence over original
                if gps_row:
                    gps_lat, gps_lon = gps_row["proposed_lat"], gps_row["proposed_lon"]
                elif orig_gps:
                    gps_lat, gps_lon = orig_gps["lat"], orig_gps["lon"]
                else:
                    gps_lat, gps_lon = None, None

                if gps_lat is not None and privacy_zones:
                    zone = find_matching_zone(gps_lat, gps_lon, privacy_zones)
                    if zone:
                        masked = apply_gps_mask(gps_lat, gps_lon, zone)
                        if masked is None:
                            # strip: delete all GPS EXIF tags
                            for gps_tag in (
                                "EXIF:GPSLatitude",
                                "EXIF:GPSLongitude",
                                "EXIF:GPSLatitudeRef",
                                "EXIF:GPSLongitudeRef",
                            ):
                                tags.append((gps_tag, ""))
                        else:
                            tags.append(("EXIF:GPSLatitude", str(masked[0])))
                            tags.append(("EXIF:GPSLongitude", str(masked[1])))
                        upsert_gps_mask(
                            corpus_conn,
                            file_id,
                            zone.name,
                            zone.mode,
                            None if masked is None else masked[0],
                            None if masked is None else masked[1],
                        )
                    elif gps_row:
                        # No zone match — write proposal GPS as-is
                        tags.append(("EXIF:GPSLatitude", str(gps_row["proposed_lat"])))
                        tags.append(("EXIF:GPSLongitude", str(gps_row["proposed_lon"])))
                    # original GPS with no zone: already in file, nothing to write
                elif gps_row:
                    # No privacy zones configured — write proposal GPS as-is
                    tags.append(("EXIF:GPSLatitude", str(gps_row["proposed_lat"])))
                    tags.append(("EXIF:GPSLongitude", str(gps_row["proposed_lon"])))

                if not tags:
                    skipped += 1
                    continue

                success = et.write_metadata(file_path, tags)
                status = "success" if success else "failed"

                for field_name, value in tags:
                    log_writeback(corpus_conn, file_id, field_name, value, status)

                if success:
                    if current_version is not None:
                        update_writeback_kb_version(corpus_conn, [file_id], current_version)
                    processed += 1
                else:
                    errors += 1

                if (processed + skipped + errors) % _BATCH_SIZE == 0:
                    corpus_conn.commit()

        corpus_conn.commit()

        if not cancel_event.is_set():
            update_pipeline_checkpoint(corpus_conn, "writeback", processed, skipped, errors)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()
        kb_conn.close()
