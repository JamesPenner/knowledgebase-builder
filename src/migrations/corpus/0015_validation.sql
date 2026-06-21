-- Validation run tracking tables.

CREATE TABLE IF NOT EXISTS validation_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         DATETIME NOT NULL DEFAULT (datetime('now')),
    files_checked  INTEGER  NOT NULL DEFAULT 0,
    ok_count       INTEGER  NOT NULL DEFAULT 0,
    changed_count  INTEGER  NOT NULL DEFAULT 0,
    moved_count    INTEGER  NOT NULL DEFAULT 0,
    missing_count  INTEGER  NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS validation_results (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL REFERENCES validation_runs(id),
    file_id  INTEGER NOT NULL REFERENCES files(id),
    status   TEXT    NOT NULL,
    detail   TEXT
);
