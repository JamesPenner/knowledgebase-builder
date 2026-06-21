-- Add unique constraint on file_aesthetic(file_id, model_name) to support upsert semantics.
-- SQLite cannot ADD CONSTRAINT after table creation, so we recreate the table.
CREATE TABLE IF NOT EXISTS file_aesthetic_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL REFERENCES files(id),
    model_name TEXT    NOT NULL,
    score      REAL,
    band       TEXT,
    scored_at  DATETIME DEFAULT (datetime('now')),
    UNIQUE (file_id, model_name)
);

INSERT OR IGNORE INTO file_aesthetic_new (id, file_id, model_name, score, band, scored_at)
SELECT id, file_id, model_name, score, band, scored_at FROM file_aesthetic;

DROP TABLE file_aesthetic;
ALTER TABLE file_aesthetic_new RENAME TO file_aesthetic;
