-- GPS cluster assignments from DBSCAN analysis.

CREATE TABLE IF NOT EXISTS gps_clusters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT    NOT NULL,
    centroid_lat REAL    NOT NULL,
    centroid_lon REAL    NOT NULL,
    file_count   INTEGER NOT NULL DEFAULT 0,
    eps_km       REAL    NOT NULL,
    min_samples  INTEGER NOT NULL,
    created_at   DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS file_gps_cluster_assignments (
    file_id    INTEGER PRIMARY KEY REFERENCES files(id),
    cluster_id INTEGER REFERENCES gps_clusters(id),
    distance_m REAL
);
