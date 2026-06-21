CREATE TABLE IF NOT EXISTS file_gps_masks (
    file_id    INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    zone_name  TEXT    NOT NULL,
    mode       TEXT    NOT NULL,
    masked_lat REAL,
    masked_lon REAL,
    masked_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
