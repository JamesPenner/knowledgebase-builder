CREATE TABLE IF NOT EXISTS file_quality (
    file_id      INTEGER PRIMARY KEY REFERENCES files(id),
    sharpness    REAL,
    exposure     REAL,
    highlights   REAL,
    shadows      REAL,
    quality_rank REAL,
    frame_count  INTEGER,
    scored_at    DATETIME DEFAULT (datetime('now'))
);
