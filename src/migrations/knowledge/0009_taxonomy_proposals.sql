CREATE TABLE IF NOT EXISTS taxonomy_proposals (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    tree_json    TEXT     NOT NULL,
    status       TEXT     NOT NULL DEFAULT 'pending',
    generated_at DATETIME DEFAULT (datetime('now')),
    applied_at   DATETIME
);
