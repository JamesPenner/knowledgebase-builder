-- knowledge.db initial schema
-- All tables created with IF NOT EXISTS for safe re-application.
-- Builtin stopwords are seeded by open_kb() in Python (INSERT OR IGNORE),
-- not here, to avoid SQL apostrophe escaping complexity.

CREATE TABLE IF NOT EXISTS _migrations (
    id         TEXT PRIMARY KEY,
    applied_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vocabulary (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    term           TEXT     NOT NULL UNIQUE,
    synonyms_json  TEXT     NOT NULL DEFAULT '[]',
    source         TEXT     NOT NULL,
    added_at       DATETIME DEFAULT (datetime('now')),
    write_synonyms INTEGER
);

CREATE TABLE IF NOT EXISTS stoplist (
    term     TEXT NOT NULL,
    scope    TEXT NOT NULL DEFAULT 'global',
    source   TEXT NOT NULL,
    added_at DATETIME DEFAULT (datetime('now')),
    PRIMARY KEY (term, scope)
);

CREATE TABLE IF NOT EXISTS corrections (
    id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    raw_term        TEXT     NOT NULL,
    canonical_term  TEXT     NOT NULL,
    type            TEXT     NOT NULL DEFAULT 'exact',
    pattern_str     TEXT,
    correction_kind TEXT,
    added_at        DATETIME DEFAULT (datetime('now')),
    UNIQUE (raw_term, type)
);

CREATE TABLE IF NOT EXISTS capture_rules (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT     NOT NULL,
    label       TEXT,
    extract_as  TEXT     NOT NULL,
    format_str  TEXT,
    keep_token  INTEGER  NOT NULL DEFAULT 0,
    value_type  TEXT,
    added_at    DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS substitute_rules (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT     NOT NULL,
    replacement TEXT     NOT NULL,
    label       TEXT,
    applies_to  TEXT     NOT NULL DEFAULT 'both',
    added_at    DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reject_tokens (
    id       INTEGER  PRIMARY KEY AUTOINCREMENT,
    pattern  TEXT     NOT NULL,
    is_regex INTEGER  NOT NULL DEFAULT 0,
    label    TEXT,
    scope    TEXT     NOT NULL DEFAULT 'both',
    added_at DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kb_version (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    changed_at  DATETIME DEFAULT (datetime('now')),
    change_type TEXT
);

CREATE TABLE IF NOT EXISTS ignored_fields (
    field_name  TEXT     PRIMARY KEY,
    namespace   TEXT,
    ignored_at  DATETIME DEFAULT (datetime('now')),
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS entity_table_registry (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    table_name       TEXT     NOT NULL UNIQUE,
    display_name     TEXT,
    trigger_word     TEXT,
    trigger_aliases  TEXT,
    key_column       TEXT,
    match_type       TEXT,
    description      TEXT,
    source_csv       TEXT,
    created_at       DATETIME DEFAULT (datetime('now')),
    updated_at       DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entity_table_links (
    id                   INTEGER  PRIMARY KEY AUTOINCREMENT,
    parent_table         TEXT     NOT NULL,
    parent_column        TEXT     NOT NULL,
    linked_table         TEXT     NOT NULL,
    linked_key_column    TEXT     NOT NULL,
    label                TEXT,
    include_in_text_pool INTEGER  NOT NULL DEFAULT 1,
    added_at             DATETIME DEFAULT (datetime('now'))
);
