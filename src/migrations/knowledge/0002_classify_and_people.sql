-- Add date_precision column to capture_rules and create classify + people tables.

ALTER TABLE capture_rules ADD COLUMN date_precision TEXT;

CREATE TABLE IF NOT EXISTS classify_rules (
    id                INTEGER  PRIMARY KEY AUTOINCREMENT,
    label             TEXT     NOT NULL,
    result_tag        TEXT     NOT NULL,
    category          TEXT     NOT NULL,
    source            TEXT     NOT NULL,
    field_name        TEXT,
    match_type        TEXT     NOT NULL,
    match_config      TEXT     NOT NULL DEFAULT '{}',
    minimum_precision TEXT,
    is_builtin        INTEGER  NOT NULL DEFAULT 0,
    enabled           INTEGER  NOT NULL DEFAULT 1,
    added_at          DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS people (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    preferred_name TEXT     NOT NULL,
    title          TEXT,
    first_name     TEXT,
    middle_name    TEXT,
    last_name      TEXT,
    notes          TEXT,
    family         INTEGER  NOT NULL DEFAULT 0,
    voice_centroid BLOB,
    voice_samples  INTEGER  NOT NULL DEFAULT 0,
    face_centroid  BLOB,
    face_samples   INTEGER  NOT NULL DEFAULT 0,
    created_at     DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS people_names (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    person_id        INTEGER  NOT NULL REFERENCES people(id),
    name             TEXT     NOT NULL,
    is_metadata_form INTEGER  NOT NULL DEFAULT 0,
    UNIQUE (person_id, name)
);

CREATE TABLE IF NOT EXISTS life_events (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    person_id  INTEGER  NOT NULL REFERENCES people(id),
    event_type TEXT     NOT NULL,
    event_date TEXT,
    partner_id INTEGER  REFERENCES people(id),
    notes      TEXT,
    added_at   DATETIME DEFAULT (datetime('now'))
);
