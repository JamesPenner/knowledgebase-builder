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
from src.db.kb import get_capture_rule_type, get_vocabulary_terms


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


def build_file_context(
    corpus_conn: sqlite3.Connection,
    kb_conn: sqlite3.Connection | None,
    file_id: int,
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
            lines = []
            for r in seg_rows:
                label = r["speaker_label"] or "Speaker"
                lines.append(f"{label}: {r['text']}")
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

    # derived tags
    derived_tags = get_file_derived_tags(corpus_conn, file_id)

    # entity names (deduplicated, non-stale)
    entity_rows = get_entity_matches_for_file(corpus_conn, file_id)
    entity_names = list({r["matched_value"] for r in entity_rows})

    # captured fields with value_type resolved from KB capture_rules
    captured_field_rows = get_file_captured_fields(corpus_conn, file_id)
    captured_fields: list[dict] = []
    for row in captured_field_rows:
        value_type = get_capture_rule_type(kb_conn, row["field_name"]) if kb_conn is not None else "text"
        captured_fields.append({
            "field_name": row["field_name"],
            "value": row["value"],
            "value_type": value_type,
        })

    # metadata date
    metadata_date = get_file_metadata_date(corpus_conn, file_id)

    # metadata location
    metadata_location: str | None = None
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
