-- Evolve file_sets from static file-ID snapshots to named dynamic filter specs.
-- Sets now store criteria that are evaluated at query time; new ingest is
-- automatically included. file_set_members is dropped.

ALTER TABLE file_sets ADD COLUMN source_id       INTEGER;
ALTER TABLE file_sets ADD COLUMN folder_prefix   TEXT;
ALTER TABLE file_sets ADD COLUMN file_type       TEXT;
ALTER TABLE file_sets ADD COLUMN date_from       TEXT;
ALTER TABLE file_sets ADD COLUMN date_to         TEXT;
ALTER TABLE file_sets ADD COLUMN name_pattern    TEXT;
ALTER TABLE file_sets ADD COLUMN criteria_summary TEXT NOT NULL DEFAULT '';

DROP TABLE IF EXISTS file_set_members;
