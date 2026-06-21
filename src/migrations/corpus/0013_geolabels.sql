CREATE TABLE IF NOT EXISTS file_geolabels (
    file_id        INTEGER  PRIMARY KEY REFERENCES files(id),
    country        TEXT,
    country_code   TEXT,
    state          TEXT,
    custom_region  TEXT,
    method         TEXT     NOT NULL,
    confidence     TEXT     NOT NULL,
    resolved_at    DATETIME DEFAULT (datetime('now'))
);
