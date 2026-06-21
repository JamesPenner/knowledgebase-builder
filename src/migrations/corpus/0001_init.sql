-- corpus.db initial schema
-- All tables created with IF NOT EXISTS for safe re-application.

CREATE TABLE IF NOT EXISTS _migrations (
    id         TEXT PRIMARY KEY,
    applied_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    path                 TEXT    NOT NULL UNIQUE,
    added_at             DATETIME DEFAULT (datetime('now')),
    file_type            TEXT    NOT NULL DEFAULT 'all',
    recursive            INTEGER NOT NULL DEFAULT 1,
    exclude_patterns     TEXT,
    file_count_ingested  INTEGER,
    last_ingested_at     DATETIME,
    removed_at           DATETIME
);

CREATE TABLE IF NOT EXISTS files (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id            INTEGER NOT NULL REFERENCES sources(id),
    path                 TEXT    NOT NULL UNIQUE,
    filename             TEXT    NOT NULL,
    filename_normalized  TEXT,
    ext                  TEXT,
    file_type            TEXT,
    file_size            INTEGER,
    mtime                DATETIME,
    sha256               TEXT,
    canonical_id         INTEGER REFERENCES files(id),
    ingested_at          DATETIME DEFAULT (datetime('now')),
    writeback_kb_version INTEGER
);

CREATE TABLE IF NOT EXISTS file_captured_fields (
    file_id      INTEGER  NOT NULL REFERENCES files(id),
    field_name   TEXT     NOT NULL,
    value        TEXT,
    captured_at  DATETIME DEFAULT (datetime('now')),
    PRIMARY KEY (file_id, field_name)
);

CREATE TABLE IF NOT EXISTS file_exif (
    file_id       INTEGER  PRIMARY KEY REFERENCES files(id),
    metadata_json TEXT,
    extracted_at  DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS file_metadata_fields (
    file_id        INTEGER  NOT NULL REFERENCES files(id),
    canonical_name TEXT     NOT NULL,
    raw_field_name TEXT,
    value          TEXT,
    value_type     TEXT,
    extracted_at   DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS file_metadata_keywords (
    file_id            INTEGER  NOT NULL REFERENCES files(id),
    canonical_name     TEXT     NOT NULL,
    keyword            TEXT     NOT NULL,
    normalized_keyword TEXT,
    extracted_at       DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS file_hashes (
    file_id         INTEGER  PRIMARY KEY REFERENCES files(id),
    sha256_content  TEXT,
    phash           TEXT,
    dhash           TEXT,
    hashed_at       DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS file_aesthetic (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL REFERENCES files(id),
    model_name TEXT    NOT NULL,
    score      REAL,
    band       TEXT,
    scored_at  DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS descriptions (
    file_id                INTEGER  PRIMARY KEY REFERENCES files(id),
    description_raw        TEXT,
    description_normalized TEXT,
    model                  TEXT,
    processed_at           DATETIME DEFAULT (datetime('now')),
    pass1_status           TEXT     NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS video_frames (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id      INTEGER NOT NULL REFERENCES files(id),
    frame_index  INTEGER,
    timestamp_ms INTEGER,
    frame_phash  TEXT,
    description  TEXT,
    model        TEXT,
    processed_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id      INTEGER REFERENCES files(id),
    term         TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    cluster_id   TEXT,
    notes        TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending',
    corrected_to TEXT,
    created_at   DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transcriptions (
    file_id           INTEGER  PRIMARY KEY REFERENCES files(id),
    transcript_text   TEXT,
    language          TEXT,
    duration_ms       INTEGER,
    model             TEXT,
    processed_at      DATETIME DEFAULT (datetime('now')),
    transcribe_status TEXT     NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL REFERENCES files(id),
    start_ms   INTEGER,
    end_ms     INTEGER,
    text       TEXT,
    avg_logprob REAL
);

CREATE TABLE IF NOT EXISTS retag_output (
    file_id                 INTEGER  PRIMARY KEY REFERENCES files(id),
    tags_json               TEXT,
    refined_description     TEXT,
    new_terms_proposed_json TEXT,
    model                   TEXT,
    processed_at            DATETIME DEFAULT (datetime('now')),
    retag_status            TEXT     NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS file_entity_matches (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER  NOT NULL REFERENCES files(id),
    table_name    TEXT     NOT NULL,
    matched_value TEXT     NOT NULL,
    match_source  TEXT     NOT NULL,
    payload_json  TEXT,
    matched_at    DATETIME DEFAULT (datetime('now')),
    stale         INTEGER  NOT NULL DEFAULT 0,
    UNIQUE (file_id, table_name, matched_value)
);

CREATE TABLE IF NOT EXISTS writeback_log (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER  REFERENCES files(id),
    field      TEXT,
    value      TEXT,
    written_at DATETIME DEFAULT (datetime('now')),
    status     TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
    stage             TEXT     PRIMARY KEY,
    last_run_at       DATETIME,
    files_processed   INTEGER,
    files_skipped     INTEGER,
    errors            INTEGER,
    duration_seconds  REAL,
    kb_version_at_run INTEGER
);

CREATE TABLE IF NOT EXISTS analyse_tokens (
    id                 INTEGER  PRIMARY KEY AUTOINCREMENT,
    token              TEXT     NOT NULL UNIQUE,
    pattern_class      TEXT,
    semantic_type      TEXT,
    frequency          INTEGER,
    file_count         INTEGER,
    depth_position     INTEGER,
    is_cross_source    INTEGER  NOT NULL DEFAULT 0,
    proposed_action    TEXT,
    proposed_extract_as TEXT,
    status             TEXT     NOT NULL DEFAULT 'pending',
    created_at         DATETIME DEFAULT (datetime('now'))
);
