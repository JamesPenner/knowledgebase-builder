CREATE TABLE IF NOT EXISTS file_temporal_fields (
    file_id     INTEGER PRIMARY KEY REFERENCES files(id),
    year        INTEGER,
    decade      TEXT,
    month_name  TEXT,
    day_name    TEXT,
    season      TEXT,
    time_of_day TEXT,
    holiday     TEXT,
    derived_at  DATETIME DEFAULT (datetime('now'))
);
