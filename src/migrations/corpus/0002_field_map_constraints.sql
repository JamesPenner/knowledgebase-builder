-- Add UNIQUE constraints to file_metadata_fields and file_metadata_keywords.
-- These tables are empty at this point in the pipeline so DROP+CREATE is safe.
-- UNIQUE constraints are required for INSERT ... ON CONFLICT UPSERT semantics.

DROP TABLE IF EXISTS file_metadata_fields;
CREATE TABLE IF NOT EXISTS file_metadata_fields (
    file_id        INTEGER  NOT NULL REFERENCES files(id),
    canonical_name TEXT     NOT NULL,
    raw_field_name TEXT,
    value          TEXT,
    value_type     TEXT,
    extracted_at   DATETIME DEFAULT (datetime('now')),
    UNIQUE(file_id, canonical_name)
);

DROP TABLE IF EXISTS file_metadata_keywords;
CREATE TABLE IF NOT EXISTS file_metadata_keywords (
    file_id            INTEGER  NOT NULL REFERENCES files(id),
    canonical_name     TEXT     NOT NULL,
    keyword            TEXT     NOT NULL,
    normalized_keyword TEXT,
    extracted_at       DATETIME DEFAULT (datetime('now')),
    UNIQUE(file_id, canonical_name, keyword)
);
