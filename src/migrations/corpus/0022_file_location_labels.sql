CREATE TABLE IF NOT EXISTS file_location_labels (
    file_id      INTEGER  PRIMARY KEY REFERENCES files(id),
    location     TEXT,
    city         TEXT,
    state        TEXT,
    country      TEXT,
    country_code TEXT,
    distance_m   REAL,
    matched_table TEXT NOT NULL DEFAULT 'locations',
    matched_at   DATETIME DEFAULT (datetime('now'))
);
