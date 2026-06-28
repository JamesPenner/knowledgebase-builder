CREATE TABLE file_face_regions_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id      INTEGER NOT NULL REFERENCES files(id),
    region_index INTEGER NOT NULL,
    source       TEXT    NOT NULL DEFAULT 'ml',
    bbox         TEXT,
    embedding    BLOB    NOT NULL,
    person_id    INTEGER,
    similarity   REAL,
    detected_at  DATETIME DEFAULT (datetime('now')),
    UNIQUE(file_id, source, region_index)
);
INSERT INTO file_face_regions_new
    SELECT id, file_id, region_index, 'ml', bbox, embedding, person_id, similarity, detected_at
    FROM file_face_regions;
DROP TABLE file_face_regions;
ALTER TABLE file_face_regions_new RENAME TO file_face_regions;
