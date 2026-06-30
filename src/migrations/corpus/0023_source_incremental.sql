-- Per-source incremental scan flag.
-- When true, ingest skips files whose mtime predates last_ingested_at for that source.

ALTER TABLE sources ADD COLUMN incremental INTEGER NOT NULL DEFAULT 0;
