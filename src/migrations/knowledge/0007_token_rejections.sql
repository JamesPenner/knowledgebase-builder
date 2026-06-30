CREATE TABLE IF NOT EXISTS token_rejections (
    id       INTEGER  PRIMARY KEY AUTOINCREMENT,
    token    TEXT     NOT NULL UNIQUE,
    added_at DATETIME DEFAULT (datetime('now'))
);

-- Migrate review rejections (exact, non-regex) from pattern_rules
INSERT OR IGNORE INTO token_rejections (token, added_at)
SELECT pattern, added_at FROM pattern_rules
WHERE action = 'reject' AND is_regex = 0;

DELETE FROM pattern_rules WHERE action = 'reject' AND is_regex = 0;
