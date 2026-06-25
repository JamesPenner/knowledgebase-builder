-- Per-KB prompt library: stores built-in and user-defined prompts for LLM stages.

CREATE TABLE IF NOT EXISTS stage_prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT    NOT NULL,
    prompt_key  TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0,
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE (stage, prompt_key, name)
);
