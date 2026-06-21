CREATE TABLE IF NOT EXISTS file_face_regions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    region_index  INTEGER NOT NULL,
    bbox          TEXT,
    embedding     BLOB NOT NULL,
    person_id     INTEGER,
    similarity    REAL,
    detected_at   DATETIME DEFAULT (datetime('now')),
    UNIQUE(file_id, region_index)
);

CREATE TABLE IF NOT EXISTS face_clusters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    centroid     BLOB NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    spread       REAL,
    created_at   DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS face_cluster_members (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id   INTEGER NOT NULL REFERENCES face_clusters(id),
    file_id      INTEGER NOT NULL REFERENCES files(id),
    region_index INTEGER NOT NULL,
    similarity   REAL,
    UNIQUE(file_id, region_index)
);
