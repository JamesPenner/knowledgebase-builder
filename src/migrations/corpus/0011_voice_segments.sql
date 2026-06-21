CREATE TABLE IF NOT EXISTS file_voice_segments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL REFERENCES files(id),
    segment_index  INTEGER NOT NULL,
    start_ms       INTEGER NOT NULL,
    end_ms         INTEGER NOT NULL,
    speaker_label  TEXT    NOT NULL,
    embedding      BLOB,
    cluster_id     INTEGER,
    person_id      INTEGER,
    similarity     REAL,
    processed_at   DATETIME DEFAULT (datetime('now')),
    UNIQUE(file_id, segment_index)
);

CREATE TABLE IF NOT EXISTS voice_speaker_clusters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    centroid     BLOB    NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    spread       REAL,
    label        TEXT,
    person_id    INTEGER,
    created_at   DATETIME DEFAULT (datetime('now'))
);
