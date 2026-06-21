"""Stage 7 — Export: write portable KB bundle to export/ folder."""
import csv
import logging
import shutil
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_VALID_SECTIONS = frozenset({"vocabulary", "corrections", "patterns", "field-map", "entities", "people"})

# Maps section name → list of output file names it produces (for progress reporting)
_SECTION_FILES = {
    "vocabulary":  ["vocabulary.csv", "stopwords.txt"],
    "corrections": ["corrections.yaml"],
    "patterns":    ["patterns.yaml", "reject_tokens.csv"],
    "field-map":   ["field_map.csv"],
    "entities":    ["entities/"],
    "people":      ["people/"],
}


def _write_vocabulary(export_dir: Path, kb_conn) -> None:
    from src.db.kb import get_export_vocabulary, get_export_stopwords

    with open(export_dir / "vocabulary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["term", "synonyms_json", "write_synonyms", "source"])
        writer.writeheader()
        for row in get_export_vocabulary(kb_conn):
            writer.writerow({
                "term": row["term"],
                "synonyms_json": row["synonyms_json"],
                "write_synonyms": row["write_synonyms"],
                "source": row["source"],
            })

    terms = get_export_stopwords(kb_conn)
    (export_dir / "stopwords.txt").write_text("\n".join(terms) + ("\n" if terms else ""), encoding="utf-8")


def _write_corrections(export_dir: Path, kb_conn) -> None:
    import yaml
    from src.db.kb import get_export_corrections_exact

    exact_rows = get_export_corrections_exact(kb_conn)
    corrections_dict = {r["raw_term"]: r["canonical_term"] for r in exact_rows}
    with open(export_dir / "corrections.yaml", "w", encoding="utf-8") as fh:
        yaml.dump(corrections_dict, fh, allow_unicode=True, default_flow_style=False, sort_keys=True)


def _write_patterns(export_dir: Path, kb_conn) -> None:
    import yaml
    from src.db.kb import (
        get_export_capture_rules,
        get_export_corrections_pattern,
        get_export_reject_tokens,
        get_export_substitute_rules,
    )

    capture_rows = get_export_capture_rules(kb_conn)
    capture_list = [
        {
            "pattern": r["pattern"],
            "label": r["label"],
            "extract_as": r["extract_as"],
            "value_type": r["value_type"],
            "format_str": r["format_str"],
            "keep_token": bool(r["keep_token"]),
        }
        for r in capture_rows
    ]

    substitute_rows = get_export_substitute_rules(kb_conn)
    substitute_list = [
        {
            "pattern": r["pattern"],
            "replacement": r["replacement"],
            "label": r["label"],
            "applies_to": r["applies_to"],
        }
        for r in substitute_rows
    ]

    pattern_corr_rows = get_export_corrections_pattern(kb_conn)
    pattern_corr_list = [
        {
            "pattern": r["raw_term"],
            "canonical": r["canonical_term"],
            "correction_kind": r["correction_kind"],
        }
        for r in pattern_corr_rows
    ]

    patterns_data = {
        "capture_rules": capture_list,
        "substitute_rules": substitute_list,
        "pattern_corrections": pattern_corr_list,
    }
    with open(export_dir / "patterns.yaml", "w", encoding="utf-8") as fh:
        yaml.dump(patterns_data, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)

    reject_rows = get_export_reject_tokens(kb_conn)
    with open(export_dir / "reject_tokens.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["pattern", "is_regex", "label", "scope"])
        writer.writeheader()
        for r in reject_rows:
            writer.writerow({
                "pattern": r["pattern"],
                "is_regex": r["is_regex"],
                "label": r["label"],
                "scope": r["scope"],
            })


def _write_field_map(export_dir: Path, kb_path: Path) -> None:
    src = kb_path.parent / "reference" / "field_map.csv"
    if src.exists():
        shutil.copy2(src, export_dir / "field_map.csv")
    else:
        logger.debug("field_map.csv not found at %s — skipping", src)


def _write_entities(export_dir: Path, kb_conn) -> None:
    from src.db.kb import get_export_entity_links, get_export_entity_registry, get_export_entity_rows

    entities_dir = export_dir / "entities"
    entities_dir.mkdir(exist_ok=True)

    registry_rows = get_export_entity_registry(kb_conn)
    reg_fields = ["table_name", "display_name", "trigger_word", "trigger_aliases",
                  "key_column", "match_type", "description", "source_csv"]
    with open(entities_dir / "_registry.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=reg_fields)
        writer.writeheader()
        for r in registry_rows:
            writer.writerow({f: r[f] for f in reg_fields})

    link_rows = get_export_entity_links(kb_conn)
    link_fields = ["parent_table", "parent_column", "linked_table", "linked_key_column",
                   "label", "include_in_text_pool"]
    with open(entities_dir / "_links.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=link_fields)
        writer.writeheader()
        for r in link_rows:
            writer.writerow({f: r[f] for f in link_fields})

    for reg_row in registry_rows:
        table_name = reg_row["table_name"]
        columns, rows = get_export_entity_rows(kb_conn, table_name)
        if not columns:
            continue
        with open(entities_dir / f"{table_name}.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            for r in rows:
                writer.writerow({c: r[c] for c in columns})


def _write_descriptions(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_export_descriptions

    rows = get_export_descriptions(corpus_conn)
    with open(export_dir / "descriptions.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["file_path", "description", "model", "processed_at"]
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "file_path": r["file_path"],
                "description": r["description"],
                "model": r["model"],
                "processed_at": r["processed_at"],
            })


def _write_tags(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_export_tags

    rows = get_export_tags(corpus_conn)
    with open(export_dir / "tags.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["file_path", "tags", "refined_description", "new_terms_proposed"]
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "file_path": r["file_path"],
                "tags": r["tags"],
                "refined_description": r["refined_description"],
                "new_terms_proposed": r["new_terms_proposed"],
            })


def _write_hashes(export_dir: Path, corpus_conn) -> None:
    rows = corpus_conn.execute(
        """
        SELECT f.path, f.sha256,
               fh.sha256_content, fh.phash, fh.dhash, fh.area_hash,
               fh.video_collage_phash, fh.video_frame_phashes
        FROM files f
        LEFT JOIN file_hashes fh ON fh.file_id = f.id
        ORDER BY f.path
        """
    ).fetchall()
    fields = ["path", "sha256", "sha256_content", "phash", "dhash",
              "area_hash", "video_collage_phash", "video_frame_phashes"]
    with open(export_dir / "hashes.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({f: r[f] for f in fields})


def _write_aesthetic_scores(export_dir: Path, corpus_conn) -> None:
    rows = corpus_conn.execute(
        """
        SELECT f.path,
               MAX(CASE WHEN fa.model_name = 'nima_mobilenet' THEN fa.score END) AS nima_score,
               MAX(CASE WHEN fa.model_name = 'nima_mobilenet' THEN fa.band  END) AS nima_band,
               MAX(CASE WHEN fa.model_name = 'clip'           THEN fa.score END) AS clip_score,
               MAX(CASE WHEN fa.model_name = 'clip'           THEN fa.band  END) AS clip_band
        FROM files f
        JOIN file_aesthetic fa ON fa.file_id = f.id
        GROUP BY f.id
        ORDER BY f.path
        """
    ).fetchall()
    if not rows:
        return
    fields = ["path", "nima_score", "nima_band", "clip_score", "clip_band"]
    with open(export_dir / "aesthetic_scores.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({f: r[f] for f in fields})


def _write_temporal_fields(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_export_temporal_fields

    rows = get_export_temporal_fields(corpus_conn)
    if not rows:
        return
    fields = ["path", "year", "decade", "month_name", "day_name", "season", "time_of_day", "holiday"]
    with open(export_dir / "temporal_fields.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({f: r[f] for f in fields})


def _write_search_text(export_dir: Path, corpus_conn) -> None:
    rows = corpus_conn.execute(
        """
        SELECT
            f.path,
            f.filename,
            GROUP_CONCAT(DISTINCT fdt.tag)      AS tags,
            GROUP_CONCAT(DISTINCT fem.matched_value) AS entities,
            d.description_normalized             AS description
        FROM files f
        LEFT JOIN file_derived_tags fdt ON fdt.file_id = f.id
        LEFT JOIN file_entity_matches fem ON fem.file_id = f.id AND fem.stale = 0
        LEFT JOIN descriptions d ON d.file_id = f.id
        GROUP BY f.id
        ORDER BY f.path
        """
    ).fetchall()
    with open(export_dir / "search_text.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["path", "search_text"])
        writer.writeheader()
        for r in rows:
            parts = [r["filename"] or ""]
            if r["tags"]:
                parts.append(r["tags"].replace(",", " "))
            if r["entities"]:
                parts.append(r["entities"].replace(",", " "))
            if r["description"]:
                parts.append(r["description"])
            writer.writerow({"path": r["path"], "search_text": " ".join(p for p in parts if p)})


def _write_coverage(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_coverage_per_file

    rows = get_coverage_per_file(corpus_conn)
    fieldnames = [
        "path", "has_description", "has_tags", "has_entities", "has_gps",
        "has_aesthetic_score", "has_asset_date", "has_quality_score",
        "has_transcript", "has_face", "has_voice", "tag_count", "entity_count",
    ]
    with open(export_dir / "coverage.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)


def _group_near_duplicates(rows, threshold: int) -> list[dict]:
    """Greedy Hamming-distance grouping over pHash hex strings. Singletons excluded."""
    if not rows:
        return []
    items = []
    for r in rows:
        try:
            phash_int = int(r["phash"], 16)
        except (ValueError, TypeError):
            continue
        items.append((r["id"], r["path"], phash_int, float(r["score"] or 0.0)))
    ungrouped = list(range(len(items)))
    result: list[dict] = []
    group_id = 0

    while ungrouped:
        seed_idx = ungrouped.pop(0)
        _, seed_path, seed_int, seed_score = items[seed_idx]
        members = [(seed_path, 0, seed_score)]
        remaining = []
        for idx in ungrouped:
            _, path, h_int, score = items[idx]
            dist = bin(seed_int ^ h_int).count("1")
            if dist <= threshold:
                members.append((path, dist, score))
            else:
                remaining.append(idx)
        ungrouped = remaining

        if len(members) < 2:
            continue
        group_id += 1
        members.sort(key=lambda m: (-m[2], m[0]))
        for rank, (path, dist, score) in enumerate(members, 1):
            result.append({
                "group_id": group_id,
                "path": path,
                "rank": rank,
                "nima_score": round(score, 4) if score else None,
                "hamming_distance": dist,
                "confidence": round(1.0 - dist / 64, 4),
            })
    return result


def _write_near_duplicates(export_dir: Path, corpus_conn, hamming_threshold: int) -> None:
    rows = corpus_conn.execute(
        """
        SELECT f.id, f.path, fh.phash,
               MAX(CASE WHEN fa.model_name = 'combined_rank' THEN fa.score ELSE NULL END) AS score
        FROM files f
        JOIN file_hashes fh ON fh.file_id = f.id
        LEFT JOIN file_aesthetic fa ON fa.file_id = f.id
        WHERE f.canonical_id IS NULL AND fh.phash IS NOT NULL
        GROUP BY f.id
        ORDER BY score DESC NULLS LAST, f.id
        """
    ).fetchall()

    groups = _group_near_duplicates(rows, hamming_threshold)

    with open(export_dir / "near_duplicate_groups.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["group_id", "path", "rank", "nima_score", "hamming_distance", "confidence"],
        )
        writer.writeheader()
        writer.writerows(groups)


def _write_gps_clusters(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_gps_cluster_assignments_for_export

    rows = get_gps_cluster_assignments_for_export(corpus_conn)
    if not rows:
        return
    with open(export_dir / "gps_clusters.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["path", "cluster_id", "cluster_label", "centroid_lat", "centroid_lon", "distance_m"],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "path": r["path"],
                "cluster_id": r["cluster_id"],
                "cluster_label": r["cluster_label"] or "",
                "centroid_lat": r["centroid_lat"],
                "centroid_lon": r["centroid_lon"],
                "distance_m": r["distance_m"],
            })


def _write_validation_report(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_validation_results_for_export

    rows = get_validation_results_for_export(corpus_conn)
    if not rows:
        return
    with open(export_dir / "validation_report.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["path", "status", "detail", "checked_at"])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "path": r["path"],
                "status": r["status"],
                "detail": r["detail"] or "",
                "checked_at": r["checked_at"],
            })


def _write_geolabels(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_geolabels_for_export

    rows = get_geolabels_for_export(corpus_conn)
    with open(export_dir / "geolabels.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["path", "country", "country_code", "state", "custom_region",
                        "method", "confidence", "resolved_at"],
        )
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)


def _write_transcripts(export_dir: Path, corpus_conn) -> None:
    from src.db.corpus import get_transcript_segments_for_export

    rows = get_transcript_segments_for_export(corpus_conn)
    with open(export_dir / "transcript_segments.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["path", "start_ms", "end_ms", "text", "speaker_label", "avg_logprob"],
        )
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)


def _write_people(export_dir: Path, kb_conn, corpus_conn, export_biometric: bool) -> None:
    from src.db.corpus import get_face_regions_for_export, get_voice_embeddings_for_export, get_voice_segments_for_export
    from src.db.kb import (
        get_life_events_for_export,
        get_people_face_centroids_for_export,
        get_people_for_export,
        get_people_names_for_export,
        get_people_voice_centroids_for_export,
    )

    people_dir = export_dir / "people"
    people_dir.mkdir(exist_ok=True)

    people = get_people_for_export(kb_conn)
    with open(people_dir / "people.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["id", "preferred_name", "title", "first_name", "middle_name", "last_name", "notes"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(dict(r) for r in people)

    names = get_people_names_for_export(kb_conn)
    with open(people_dir / "people_names.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["person_id", "preferred_name", "name"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(r) for r in names)

    events = get_life_events_for_export(kb_conn)
    with open(people_dir / "life_events.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["person_id", "preferred_name", "event_type", "event_date", "partner_id", "notes"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(dict(r) for r in events)

    regions = get_face_regions_for_export(corpus_conn)
    with open(people_dir / "face_regions.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["file_path", "region_index", "person_id", "similarity", "bbox"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(dict(r) for r in regions)

    if export_biometric:
        import base64
        centroids = get_people_face_centroids_for_export(kb_conn)
        with open(people_dir / "face_centroids.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["person_id", "preferred_name", "face_samples", "face_centroid_b64"],
            )
            writer.writeheader()
            for row in centroids:
                writer.writerow({
                    "person_id": row["person_id"],
                    "preferred_name": row["preferred_name"],
                    "face_samples": row["face_samples"],
                    "face_centroid_b64": base64.b64encode(bytes(row["face_centroid"])).decode(),
                })

        voice_centroids = get_people_voice_centroids_for_export(kb_conn)
        with open(people_dir / "voice_centroids.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["person_id", "preferred_name", "voice_samples", "voice_centroid_b64"],
            )
            writer.writeheader()
            for row in voice_centroids:
                writer.writerow({
                    "person_id": row["person_id"],
                    "preferred_name": row["preferred_name"],
                    "voice_samples": row["voice_samples"],
                    "voice_centroid_b64": base64.b64encode(bytes(row["voice_centroid"])).decode(),
                })

    voice_rows = get_voice_embeddings_for_export(corpus_conn)
    with open(people_dir / "voice_embeddings.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["path", "duration_ms", "model"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(dict(r) for r in voice_rows)

    segments = get_voice_segments_for_export(corpus_conn)
    with open(people_dir / "voice_segments.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["path", "start_ms", "end_ms", "speaker_label", "cluster_id", "person_id", "similarity"],
        )
        writer.writeheader()
        writer.writerows(dict(r) for r in segments)


def run_export(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    section: str | None = None,
) -> None:
    from src.db.corpus import open_corpus, update_pipeline_checkpoint
    from src.db.kb import open_kb

    if section is not None and section not in _VALID_SECTIONS:
        logger.error("Unknown export section: %r. Valid: %s", section, ", ".join(sorted(_VALID_SECTIONS)))
        return

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        export_dir = kb_path.parent / "export"
        export_dir.mkdir(exist_ok=True)

        # Determine which sections to write
        sections = [section] if section else list(_SECTION_FILES)
        total = len(sections)
        processed = 0

        for i, sec in enumerate(sections):
            if cancel_event.is_set():
                break
            progress.update(i, total, f"Export: {sec}")

            if sec == "vocabulary":
                _write_vocabulary(export_dir, kb_conn)
            elif sec == "corrections":
                _write_corrections(export_dir, kb_conn)
            elif sec == "patterns":
                _write_patterns(export_dir, kb_conn)
            elif sec == "field-map":
                _write_field_map(export_dir, kb_path)
            elif sec == "entities":
                _write_entities(export_dir, kb_conn)
            elif sec == "people":
                _write_people(export_dir, kb_conn, corpus_conn, config.export_biometric)

            processed += 1

        # descriptions.csv, tags.csv, hashes.csv, aesthetic_scores.csv, search_text.csv
        # are only written on full export
        if not cancel_event.is_set() and section is None:
            _write_descriptions(export_dir, corpus_conn)
            _write_tags(export_dir, corpus_conn)
            _write_hashes(export_dir, corpus_conn)
            _write_aesthetic_scores(export_dir, corpus_conn)
            _write_search_text(export_dir, corpus_conn)
            _write_temporal_fields(export_dir, corpus_conn)
            _write_transcripts(export_dir, corpus_conn)
            _write_coverage(export_dir, corpus_conn)
            _write_near_duplicates(export_dir, corpus_conn, config.near_duplicate_hamming_threshold)
            _write_geolabels(export_dir, corpus_conn)
            _write_gps_clusters(export_dir, corpus_conn)
            _write_validation_report(export_dir, corpus_conn)
            _write_people(export_dir, kb_conn, corpus_conn, config.export_biometric)

        if not cancel_event.is_set():
            update_pipeline_checkpoint(corpus_conn, "export", processed, 0, 0)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()
        kb_conn.close()
