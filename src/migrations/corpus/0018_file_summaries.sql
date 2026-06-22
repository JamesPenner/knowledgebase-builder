CREATE TABLE IF NOT EXISTS file_summaries (
    file_id       INTEGER PRIMARY KEY REFERENCES files(id),
    summary_text  TEXT,
    model         TEXT,
    prompt_version TEXT,
    processed_at  DATETIME,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','done','failed','skipped'))
);
