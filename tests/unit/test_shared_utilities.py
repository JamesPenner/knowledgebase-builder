"""Unit tests for KB.AG2 shared utility helpers — no DB fixture required."""
import sqlite3
import types

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blob(dim: int, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


def _conn_with_tables() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE analyse_tokens (id INTEGER PRIMARY KEY, token TEXT NOT NULL);
        CREATE TABLE candidates (id INTEGER PRIMARY KEY, term TEXT NOT NULL);
        CREATE TABLE files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL,
            file_type TEXT DEFAULT 'images', file_size INTEGER DEFAULT 0,
            mtime REAL DEFAULT 0.0, source_id INTEGER DEFAULT 1,
            filename TEXT DEFAULT 'f', ext TEXT DEFAULT '.jpg'
        );
    """)
    return conn


# ---------------------------------------------------------------------------
# src/db/utils.py — configure_connection
# ---------------------------------------------------------------------------

class TestConfigureConnection:
    def test_sets_wal_journal_mode(self, tmp_path):
        from src.db.utils import configure_connection
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        configure_connection(conn)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_enables_foreign_keys(self):
        from src.db.utils import configure_connection
        conn = sqlite3.connect(":memory:")
        configure_connection(conn)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_sets_temp_store_memory(self):
        from src.db.utils import configure_connection
        conn = sqlite3.connect(":memory:")
        configure_connection(conn)
        ts = conn.execute("PRAGMA temp_store").fetchone()[0]
        assert ts == 2  # MEMORY = 2


# ---------------------------------------------------------------------------
# src/db/corpus.py — single-row lookup helpers
# ---------------------------------------------------------------------------

class TestGetAnalyseTokenById:
    def test_returns_row_when_found(self):
        from src.db.corpus import get_analyse_token_by_id
        conn = _conn_with_tables()
        conn.execute("INSERT INTO analyse_tokens(id, token) VALUES (1, 'sunset')")
        row = get_analyse_token_by_id(conn, 1)
        assert row is not None
        assert row["token"] == "sunset"

    def test_returns_none_when_missing(self):
        from src.db.corpus import get_analyse_token_by_id
        conn = _conn_with_tables()
        assert get_analyse_token_by_id(conn, 99) is None

    def test_returns_correct_row_by_id(self):
        from src.db.corpus import get_analyse_token_by_id
        conn = _conn_with_tables()
        conn.execute("INSERT INTO analyse_tokens(id, token) VALUES (1, 'alpha')")
        conn.execute("INSERT INTO analyse_tokens(id, token) VALUES (2, 'beta')")
        assert get_analyse_token_by_id(conn, 2)["token"] == "beta"


class TestGetCandidateById:
    def test_returns_row_when_found(self):
        from src.db.corpus import get_candidate_by_id
        conn = _conn_with_tables()
        conn.execute("INSERT INTO candidates(id, term) VALUES (1, 'architecture')")
        row = get_candidate_by_id(conn, 1)
        assert row is not None
        assert row["term"] == "architecture"

    def test_returns_none_when_missing(self):
        from src.db.corpus import get_candidate_by_id
        conn = _conn_with_tables()
        assert get_candidate_by_id(conn, 42) is None

    def test_returns_correct_term_by_id(self):
        from src.db.corpus import get_candidate_by_id
        conn = _conn_with_tables()
        conn.execute("INSERT INTO candidates(id, term) VALUES (1, 'first')")
        conn.execute("INSERT INTO candidates(id, term) VALUES (2, 'second')")
        assert get_candidate_by_id(conn, 1)["term"] == "first"


class TestGetFilePathById:
    def test_returns_path_when_found(self):
        from src.db.corpus import get_file_path_by_id
        conn = _conn_with_tables()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/media/photo.jpg')")
        row = get_file_path_by_id(conn, 1)
        assert row is not None
        assert row["path"] == "/media/photo.jpg"

    def test_returns_none_when_missing(self):
        from src.db.corpus import get_file_path_by_id
        conn = _conn_with_tables()
        assert get_file_path_by_id(conn, 999) is None

    def test_returns_correct_path_by_id(self):
        from src.db.corpus import get_file_path_by_id
        conn = _conn_with_tables()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/a/b.jpg')")
        conn.execute("INSERT INTO files(id, path) VALUES (2, '/c/d.jpg')")
        assert get_file_path_by_id(conn, 2)["path"] == "/c/d.jpg"


# ---------------------------------------------------------------------------
# src/stages/vocab_llm.py — _require_text_model
# ---------------------------------------------------------------------------

class TestRequireTextModel:
    def _cfg(self, text_model=None):
        cfg = types.SimpleNamespace(text_model=text_model)
        return cfg

    def test_returns_false_when_no_text_model(self):
        from src.stages.vocab_llm import _require_text_model
        assert _require_text_model(self._cfg()) is False

    def test_returns_false_when_empty_string(self):
        from src.stages.vocab_llm import _require_text_model
        assert _require_text_model(self._cfg("")) is False

    def test_returns_true_when_model_configured(self):
        from src.stages.vocab_llm import _require_text_model
        assert _require_text_model(self._cfg("mistral")) is True

    def test_returns_true_for_any_truthy_string(self):
        from src.stages.vocab_llm import _require_text_model
        assert _require_text_model(self._cfg("llama3:8b")) is True


# ---------------------------------------------------------------------------
# src/pipeline/embeddings.py — (math properties already covered in
# test_voice_unit.py; add a few for the new module location)
# ---------------------------------------------------------------------------

class TestPipelineEmbeddings:
    def test_cosine_similarity_same_module(self):
        from src.pipeline.embeddings import cosine_similarity
        b = _blob(128, 0)
        assert cosine_similarity(b, b) == pytest.approx(1.0, abs=1e-5)

    def test_update_centroid_normalised_output(self):
        from src.pipeline.embeddings import update_centroid
        blob, count = update_centroid(None, 0, _blob(128, 1))
        result = np.frombuffer(blob, dtype=np.float32)
        assert float(np.linalg.norm(result)) == pytest.approx(1.0, abs=1e-5)
        assert count == 1
