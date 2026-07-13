"""Unit tests for pattern-rule staleness tracking: get_pattern_rules_changed_at
and is_pattern_rules_stale — in-memory SQLite, no filesystem."""
import pytest

from src.db.corpus import is_pattern_rules_stale, open_corpus, update_pipeline_checkpoint
from src.db.kb import bump_kb_version, get_pattern_rules_changed_at, open_kb


@pytest.fixture
def mem_dbs(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    yield corpus_conn, kb_conn
    corpus_conn.close()
    kb_conn.close()


class TestGetPatternRulesChangedAt:
    def test_no_changes_returns_none(self, mem_dbs):
        _, kb_conn = mem_dbs
        assert get_pattern_rules_changed_at(kb_conn) is None

    def test_unrelated_kb_version_entry_ignored(self, mem_dbs):
        _, kb_conn = mem_dbs
        bump_kb_version(kb_conn, "vocabulary_term_added")
        assert get_pattern_rules_changed_at(kb_conn) is None

    def test_pattern_rule_change_types_tracked(self, mem_dbs):
        _, kb_conn = mem_dbs
        for change_type in ("pattern_rule_added", "pattern_rule_updated", "pattern_rule_deleted"):
            bump_kb_version(kb_conn, change_type)
            assert get_pattern_rules_changed_at(kb_conn) is not None


class TestIsPatternRulesStale:
    def test_never_changed_is_not_stale(self, mem_dbs):
        conn, kb_conn = mem_dbs
        update_pipeline_checkpoint(conn, "normalize", files_processed=5)
        assert is_pattern_rules_stale(conn, kb_conn, "normalize") is False

    def test_never_run_is_not_stale(self, mem_dbs):
        conn, kb_conn = mem_dbs
        bump_kb_version(kb_conn, "pattern_rule_added")
        assert is_pattern_rules_stale(conn, kb_conn, "normalize") is False

    def test_changed_after_last_run_is_stale(self, mem_dbs):
        conn, kb_conn = mem_dbs
        update_pipeline_checkpoint(conn, "normalize", files_processed=5)
        # Force a distinct, later timestamp so ordering is unambiguous regardless of clock resolution.
        kb_conn.execute(
            "INSERT INTO kb_version (change_type, changed_at) VALUES (?, datetime('now', '+1 hour'))",
            ("pattern_rule_added",),
        )
        kb_conn.commit()
        assert is_pattern_rules_stale(conn, kb_conn, "normalize") is True

    def test_changed_before_last_run_is_not_stale(self, mem_dbs):
        conn, kb_conn = mem_dbs
        kb_conn.execute(
            "INSERT INTO kb_version (change_type, changed_at) VALUES (?, datetime('now', '-1 hour'))",
            ("pattern_rule_added",),
        )
        kb_conn.commit()
        update_pipeline_checkpoint(conn, "normalize", files_processed=5)
        assert is_pattern_rules_stale(conn, kb_conn, "normalize") is False

    def test_stale_check_is_per_stage(self, mem_dbs):
        conn, kb_conn = mem_dbs
        update_pipeline_checkpoint(conn, "normalize", files_processed=5)
        update_pipeline_checkpoint(conn, "suggest", files_processed=5)
        kb_conn.execute(
            "INSERT INTO kb_version (change_type, changed_at) VALUES (?, datetime('now', '+1 hour'))",
            ("pattern_rule_added",),
        )
        kb_conn.commit()
        assert is_pattern_rules_stale(conn, kb_conn, "normalize") is True
        assert is_pattern_rules_stale(conn, kb_conn, "suggest") is True
