-- Add is_ignore flag to capture_rules.
-- When is_ignore=1, extract_as is stored as '' to satisfy the NOT NULL constraint.
ALTER TABLE capture_rules ADD COLUMN is_ignore INTEGER NOT NULL DEFAULT 0;
