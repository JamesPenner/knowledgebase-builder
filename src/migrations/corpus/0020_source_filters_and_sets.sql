-- Source filter criteria and named file sets.

ALTER TABLE sources ADD COLUMN filters_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS file_sets (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS file_set_members (
    set_id  INTEGER NOT NULL REFERENCES file_sets(id)  ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id)      ON DELETE CASCADE,
    PRIMARY KEY (set_id, file_id)
);
