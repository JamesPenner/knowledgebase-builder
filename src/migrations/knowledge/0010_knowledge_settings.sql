CREATE TABLE IF NOT EXISTS knowledge_settings (
    category TEXT PRIMARY KEY,
    enabled  INTEGER NOT NULL DEFAULT 1
);

INSERT INTO knowledge_settings (category, enabled) VALUES ('people', 1);
INSERT INTO knowledge_settings (category, enabled) VALUES ('places', 1);
INSERT INTO knowledge_settings (category, enabled) VALUES ('dates', 1);
