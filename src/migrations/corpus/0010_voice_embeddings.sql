CREATE TABLE IF NOT EXISTS file_voice_embeddings (
    file_id        INTEGER  PRIMARY KEY REFERENCES files(id),
    embedding      BLOB     NOT NULL,
    model          TEXT     NOT NULL,
    duration_ms    INTEGER,
    processed_at   DATETIME DEFAULT (datetime('now'))
);
