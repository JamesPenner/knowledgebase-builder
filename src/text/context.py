"""Per-file text assembly — FileContext dataclass and build_file_context() factory."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from src.db.corpus import (
    get_description_for_file,
    get_entity_matches_for_file,
    get_enrichment_text_for_file,
    get_file_captured_fields,
    get_file_derived_tags,
    get_file_filename,
    get_file_geolabel,
    get_file_metadata_date,
    get_file_summary,
    get_file_transcript_segments,
    get_file_transcription,
)
from src.db.kb import get_pattern_rule_type, get_vocabulary_terms
from src.pipeline.knowledge_gates import ALL_CATEGORIES, excluded_entity_tables, tag_category_is_enabled


@dataclass
class FileContext:
    file_id: int
    filename: str
    description: str | None
    transcript: str | None
    transcript_attributed: bool
    summary_text: str | None
    derived_tags: list[str] = field(default_factory=list)
    entity_names: list[str] = field(default_factory=list)
    captured_fields: list[dict] = field(default_factory=list)
    metadata_date: str | None = None
    metadata_location: str | None = None
    enrichment_text: str = ""
    vocab_terms: list[str] = field(default_factory=list)


def _generic_speaker_labels(
    corpus_conn: sqlite3.Connection, file_id: int, seg_rows: list[sqlite3.Row]
) -> list[str | None]:
    """Re-derive each segment's speaker label without person-name resolution.

    Reuses attribute_speakers' overlap-matching and fallback-label logic with
    an empty people_map, so a toggled-off People domain suppresses resolved
    names even for segments already attributed while People was on.
    """
    from src.db.corpus import get_voice_segments_for_file, get_voice_speaker_clusters
    from src.stages.attribute_speakers import _best_overlap, _resolve_label

    voice_segs = get_voice_segments_for_file(corpus_conn, file_id)
    cluster_map = {r["id"]: (r["label"] or "") for r in get_voice_speaker_clusters(corpus_conn)}
    labels: list[str | None] = []
    for r in seg_rows:
        best = _best_overlap(r["start_ms"], r["end_ms"], voice_segs)
        labels.append(_resolve_label(best, {}, cluster_map) if best is not None else r["speaker_label"])
    return labels


def build_file_context(
    corpus_conn: sqlite3.Connection,
    kb_conn: sqlite3.Connection | None,
    file_id: int,
    *,
    enabled_categories: frozenset[str] = ALL_CATEGORIES,
) -> FileContext:
    """Assemble all available enrichment data for a file in one pass."""
    filename = get_file_filename(corpus_conn, file_id)

    # description
    desc_row = get_description_for_file(corpus_conn, file_id)
    description: str | None = None
    if desc_row:
        description = desc_row["description_normalized"] or desc_row["description_raw"]

    # transcript — attributed segments preferred, plain fallback
    transcript: str | None = None
    transcript_attributed = False
    seg_rows = get_file_transcript_segments(corpus_conn, file_id)
    if seg_rows:
        has_speaker = any(r["speaker_label"] for r in seg_rows)
        if has_speaker:
            if "people" in enabled_categories:
                labels = [r["speaker_label"] for r in seg_rows]
            else:
                labels = _generic_speaker_labels(corpus_conn, file_id, seg_rows)
            lines = [f"{label or 'Speaker'}: {r['text']}" for label, r in zip(labels, seg_rows)]
            transcript = "\n".join(lines)
            transcript_attributed = True
        else:
            transcript = " ".join(r["text"] for r in seg_rows)
    else:
        tr_row = get_file_transcription(corpus_conn, file_id)
        if tr_row:
            transcript = tr_row["transcript_text"]

    # summary (done status only)
    summary_text: str | None = None
    summary_row = get_file_summary(corpus_conn, file_id)
    if summary_row and summary_row["status"] == "done":
        summary_text = summary_row["summary_text"]

    # derived tags — filtered by category at read time, independent of what
    # classify last wrote (a domain toggled off after classify ran still
    # suppresses stale calendar/life-event tags)
    tag_rows = get_file_derived_tags(corpus_conn, file_id)
    derived_tags = [
        r["tag"] for r in tag_rows if tag_category_is_enabled(r["category"], enabled_categories)
    ]

    # entity names (deduplicated, non-stale, filtered by table domain)
    excluded_tables = excluded_entity_tables(enabled_categories)
    entity_rows = get_entity_matches_for_file(corpus_conn, file_id)
    entity_names = list({
        r["matched_value"] for r in entity_rows if r["table_name"] not in excluded_tables
    })

    # captured fields with value_type resolved from KB capture_rules
    captured_field_rows = get_file_captured_fields(corpus_conn, file_id)
    captured_fields: list[dict] = []
    for row in captured_field_rows:
        value_type = get_pattern_rule_type(kb_conn, row["field_name"]) if kb_conn is not None else "text"
        captured_fields.append({
            "field_name": row["field_name"],
            "value": row["value"],
            "value_type": value_type,
        })

    # metadata date — deliberately NOT gated by the "dates" toggle; a bare
    # capture timestamp is structural metadata, not "knowledge" content
    metadata_date = get_file_metadata_date(corpus_conn, file_id)

    # metadata location
    metadata_location: str | None = None
    if "places" in enabled_categories:
        geo_row = get_file_geolabel(corpus_conn, file_id)
        if geo_row:
            parts = [p for p in (geo_row["custom_region"], geo_row["state"], geo_row["country"]) if p]
            if parts:
                metadata_location = ", ".join(parts)

    # enrichment text (metadata fields + keywords + captured fields as text)
    enrichment_text = get_enrichment_text_for_file(corpus_conn, file_id)

    # vocabulary terms from knowledge.db
    vocab_terms: list[str] = []
    if kb_conn is not None:
        vocab_terms = [r["term"] for r in get_vocabulary_terms(kb_conn)]

    return FileContext(
        file_id=file_id,
        filename=filename,
        description=description,
        transcript=transcript,
        transcript_attributed=transcript_attributed,
        summary_text=summary_text,
        derived_tags=derived_tags,
        entity_names=entity_names,
        captured_fields=captured_fields,
        metadata_date=metadata_date,
        metadata_location=metadata_location,
        enrichment_text=enrichment_text,
        vocab_terms=vocab_terms,
    )
