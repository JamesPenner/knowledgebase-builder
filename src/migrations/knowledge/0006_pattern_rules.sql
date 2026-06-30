CREATE TABLE IF NOT EXISTS pattern_rules (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    pattern        TEXT     NOT NULL,
    is_regex       INTEGER  NOT NULL DEFAULT 1,
    action         TEXT     NOT NULL,
    label          TEXT,
    replace_with   TEXT,
    replace_type   TEXT,
    extract_as     TEXT,
    format_str     TEXT,
    value_type     TEXT,
    keep_token     INTEGER  NOT NULL DEFAULT 0,
    date_precision TEXT,
    scope          TEXT     NOT NULL DEFAULT 'both',
    added_at       DATETIME DEFAULT (datetime('now'))
);

INSERT INTO pattern_rules
    (pattern, is_regex, action, label, extract_as, format_str,
     value_type, keep_token, date_precision, scope, added_at)
SELECT pattern, 1,
    CASE WHEN is_ignore=1 THEN 'ignore' ELSE 'capture' END,
    label, extract_as, format_str, value_type, keep_token, date_precision, 'both', added_at
FROM capture_rules;

INSERT INTO pattern_rules
    (pattern, is_regex, action, label, scope, added_at)
SELECT pattern, is_regex, 'reject', label, scope, added_at
FROM reject_tokens;

INSERT INTO pattern_rules
    (pattern, is_regex, action, replace_with, replace_type, added_at)
SELECT raw_term, 0, 'replace', canonical_term, 'correction', added_at
FROM corrections WHERE type='exact';

INSERT INTO pattern_rules
    (pattern, is_regex, action, replace_with, added_at)
SELECT raw_term, 1, 'replace', canonical_term, added_at
FROM corrections WHERE type='pattern';

DROP TABLE IF EXISTS capture_rules;
DROP TABLE IF EXISTS reject_tokens;
DROP TABLE IF EXISTS corrections;
