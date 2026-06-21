-- Stage 1.7/1.8 output tables: derived tags and GPS proposals.

CREATE TABLE IF NOT EXISTS file_derived_tags (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER  NOT NULL REFERENCES files(id),
    tag        TEXT     NOT NULL,
    category   TEXT     NOT NULL,
    source     TEXT     NOT NULL,
    rule_id    INTEGER,
    derived_at DATETIME DEFAULT (datetime('now')),
    UNIQUE (file_id, tag, category)
);

CREATE TABLE IF NOT EXISTS gps_proposals (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER  NOT NULL REFERENCES files(id),
    location_name TEXT     NOT NULL,
    proposed_lat  REAL     NOT NULL,
    proposed_lon  REAL     NOT NULL,
    threshold_m   REAL,
    source_text   TEXT,
    status        TEXT     NOT NULL DEFAULT 'pending',
    proposed_at   DATETIME DEFAULT (datetime('now')),
    UNIQUE (file_id, location_name)
);
