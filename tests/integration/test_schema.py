import sqlite3

import pytest

from src.db.corpus import open_corpus
from src.db.kb import open_kb, _BUILTIN_STOPWORDS

_CORPUS_TABLES = {
    "_migrations", "sources", "files", "file_captured_fields", "file_exif",
    "file_metadata_fields", "file_metadata_keywords", "file_hashes",
    "file_aesthetic", "file_quality", "descriptions", "video_frames", "candidates",
    "transcriptions", "transcript_segments", "retag_output",
    "file_entity_matches", "writeback_log", "pipeline_checkpoints",
    "analyse_tokens", "file_derived_tags", "gps_proposals",
    "file_temporal_fields",
    "file_face_regions", "face_clusters", "face_cluster_members",
    "file_voice_embeddings",
    "file_voice_segments", "voice_speaker_clusters",
    "file_geolabels",
    "file_gps_masks",
    "validation_runs",
    "validation_results",
    "gps_clusters",
    "file_gps_cluster_assignments",
    "file_summaries",
}

_KB_TABLES = {
    "_migrations", "vocabulary", "stoplist", "corrections", "capture_rules",
    "substitute_rules", "reject_tokens", "kb_version", "ignored_fields",
    "entity_table_registry", "entity_table_links",
    "classify_rules", "people", "people_names", "life_events",
    "stage_prompts",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not r[0].startswith("sqlite_")
    }


def test_corpus_all_tables_present(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    assert _CORPUS_TABLES == _table_names(conn)


def test_corpus_wal_mode_enabled(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_corpus_foreign_keys_enforced(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO files (source_id, path, filename) VALUES (999, '/fake/path.jpg', 'path.jpg')"
        )
        conn.commit()


def test_kb_all_tables_present(tmp_path):
    conn = open_kb(tmp_path / "knowledge.db")
    assert _KB_TABLES == _table_names(conn)


def test_kb_builtin_stopwords_seeded(tmp_path):
    conn = open_kb(tmp_path / "knowledge.db")
    count = conn.execute("SELECT COUNT(*) FROM stoplist WHERE source='builtin'").fetchone()[0]
    assert count >= 50
    assert count == len(_BUILTIN_STOPWORDS)


def test_kb_stoplist_unique_constraint(tmp_path):
    conn = open_kb(tmp_path / "knowledge.db")
    conn.execute("INSERT INTO stoplist (term, scope, source) VALUES ('xyzunique', 'global', 'user')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO stoplist (term, scope, source) VALUES ('xyzunique', 'global', 'user')")
        conn.commit()


def test_migrations_idempotent_on_reopen(tmp_path):
    db_path = tmp_path / "corpus.db"
    conn1 = open_corpus(db_path)
    count1 = conn1.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    conn1.close()

    conn2 = open_corpus(db_path)
    count2 = conn2.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count2 == count1


def test_face_clusters_has_person_columns(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(face_clusters)")}
    assert "person_id" in cols
    assert "label" in cols


def test_file_summaries_schema(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(file_summaries)")}
    assert cols == {"file_id", "summary_text", "model", "prompt_version", "processed_at", "status"}
