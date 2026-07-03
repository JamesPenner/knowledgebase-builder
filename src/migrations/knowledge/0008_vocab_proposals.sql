CREATE TABLE IF NOT EXISTS vocab_proposals (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    terms_json    TEXT     NOT NULL,
    source        TEXT     NOT NULL,
    source_detail TEXT,
    status        TEXT     NOT NULL DEFAULT 'pending',
    canonical     TEXT,
    added_at      DATETIME DEFAULT (datetime('now'))
);
