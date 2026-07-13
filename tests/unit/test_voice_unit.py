"""Unit tests for KB.P16 Voice Stage — no Resemblyzer inference, no filesystem."""
import sqlite3
import struct
import types
import unittest.mock as mock
import wave

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vec(dim: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / float(np.linalg.norm(v))


def _blob(dim: int = 256, seed: int = 0) -> bytes:
    return _vec(dim, seed).tobytes()


def _make_corpus_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE files (
            id                INTEGER PRIMARY KEY,
            path              TEXT    NOT NULL,
            file_type         TEXT    NOT NULL DEFAULT 'audio',
            voice_checked_at  DATETIME
        );
        CREATE TABLE file_voice_embeddings (
            file_id      INTEGER PRIMARY KEY,
            embedding    BLOB    NOT NULL,
            model        TEXT    NOT NULL,
            duration_ms  INTEGER,
            processed_at DATETIME DEFAULT (datetime('now'))
        );
    """)
    return conn


def _make_kb_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE people (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            preferred_name TEXT    NOT NULL,
            voice_centroid BLOB,
            voice_samples  INTEGER NOT NULL DEFAULT 0
        );
    """)
    return conn


# ---------------------------------------------------------------------------
# cosine_similarity (shared embedding utility)
# ---------------------------------------------------------------------------

class TestCosineSimilarityVoice:
    def test_identical_vectors_return_one(self):
        from src.pipeline.embeddings import cosine_similarity
        b = _blob(256, 7)
        assert cosine_similarity(b, b) == pytest.approx(1.0, abs=1e-5)

    def test_orthogonal_vectors_return_zero(self):
        from src.pipeline.embeddings import cosine_similarity
        v1 = np.zeros(256, dtype=np.float32)
        v1[0] = 1.0
        v2 = np.zeros(256, dtype=np.float32)
        v2[1] = 1.0
        assert cosine_similarity(v1.tobytes(), v2.tobytes()) == pytest.approx(0.0, abs=1e-5)

    def test_zero_vector_a_returns_zero(self):
        from src.pipeline.embeddings import cosine_similarity
        zero = np.zeros(256, dtype=np.float32).tobytes()
        result = cosine_similarity(zero, _blob(256, 1))
        assert result == 0.0

    def test_zero_vector_b_returns_zero(self):
        from src.pipeline.embeddings import cosine_similarity
        zero = np.zeros(256, dtype=np.float32).tobytes()
        result = cosine_similarity(_blob(256, 1), zero)
        assert result == 0.0

    def test_different_vectors_in_range(self):
        from src.pipeline.embeddings import cosine_similarity
        sim = cosine_similarity(_blob(256, 0), _blob(256, 99))
        assert -1.0 <= sim <= 1.0

    def test_symmetric(self):
        from src.pipeline.embeddings import cosine_similarity
        a = _blob(256, 3)
        b = _blob(256, 4)
        assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a), abs=1e-6)


# ---------------------------------------------------------------------------
# update_centroid (shared embedding utility)
# ---------------------------------------------------------------------------

class TestUpdateVoiceCentroid:
    def test_first_sample_returns_normalised_embedding(self):
        from src.pipeline.embeddings import update_centroid
        emb = _blob(256, 1)
        result_blob, count = update_centroid(None, 0, emb)
        assert count == 1
        result = np.frombuffer(result_blob, dtype=np.float32)
        assert float(np.linalg.norm(result)) == pytest.approx(1.0, abs=1e-5)

    def test_running_mean_count_increments(self):
        from src.pipeline.embeddings import update_centroid
        blob1, count1 = update_centroid(None, 0, _blob(256, 0))
        blob2, count2 = update_centroid(blob1, count1, _blob(256, 1))
        assert count2 == 2

    def test_running_mean_is_normalised(self):
        from src.pipeline.embeddings import update_centroid
        blob, count = update_centroid(None, 0, _blob(256, 0))
        for seed in range(1, 5):
            blob, count = update_centroid(blob, count, _blob(256, seed))
        result = np.frombuffer(blob, dtype=np.float32)
        assert float(np.linalg.norm(result)) == pytest.approx(1.0, abs=1e-5)

    def test_zero_old_count_treated_as_first(self):
        from src.pipeline.embeddings import update_centroid
        existing_blob = _blob(256, 99)
        new_blob = _blob(256, 1)
        result_blob, count = update_centroid(existing_blob, 0, new_blob)
        assert count == 1
        result = np.frombuffer(result_blob, dtype=np.float32)
        assert float(np.linalg.norm(result)) == pytest.approx(1.0, abs=1e-5)

    def test_centroid_lies_between_two_embeddings(self):
        from src.pipeline.embeddings import update_centroid
        v1 = np.zeros(256, dtype=np.float32)
        v1[0] = 1.0
        v2 = np.zeros(256, dtype=np.float32)
        v2[1] = 1.0
        blob1, count1 = update_centroid(None, 0, v1.tobytes())
        blob2, _ = update_centroid(blob1, count1, v2.tobytes())
        result = np.frombuffer(blob2, dtype=np.float32)
        # centroid direction should be equal weight between dim 0 and dim 1
        assert result[0] == pytest.approx(result[1], abs=1e-5)


# ---------------------------------------------------------------------------
# corpus.py DB helpers
# ---------------------------------------------------------------------------

class TestUpsertVoiceEmbedding:
    def test_insert_new_row(self):
        from src.db.corpus import upsert_voice_embedding
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        upsert_voice_embedding(conn, 1, _blob(256, 0), "resemblyzer", 3000)
        row = conn.execute("SELECT * FROM file_voice_embeddings WHERE file_id = 1").fetchone()
        assert row is not None
        assert row["model"] == "resemblyzer"
        assert row["duration_ms"] == 3000

    def test_upsert_replaces_existing(self):
        from src.db.corpus import upsert_voice_embedding
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        upsert_voice_embedding(conn, 1, _blob(256, 0), "resemblyzer", 2000)
        upsert_voice_embedding(conn, 1, _blob(256, 1), "resemblyzer", 5000)
        count = conn.execute("SELECT COUNT(*) FROM file_voice_embeddings").fetchone()[0]
        assert count == 1
        row = conn.execute("SELECT duration_ms FROM file_voice_embeddings WHERE file_id = 1").fetchone()
        assert row["duration_ms"] == 5000

    def test_duration_ms_nullable(self):
        from src.db.corpus import upsert_voice_embedding
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        upsert_voice_embedding(conn, 1, _blob(256, 0), "resemblyzer", None)
        row = conn.execute("SELECT duration_ms FROM file_voice_embeddings WHERE file_id = 1").fetchone()
        assert row["duration_ms"] is None


class TestGetFilesWithoutVoiceEmbedding:
    def test_returns_audio_and_video_only(self):
        from src.db.corpus import get_files_without_voice_embedding
        conn = _make_corpus_db()
        conn.executescript("""
            INSERT INTO files(id, path, file_type) VALUES (1, '/a.mp3', 'audio');
            INSERT INTO files(id, path, file_type) VALUES (2, '/b.mp4', 'video');
            INSERT INTO files(id, path, file_type) VALUES (3, '/c.jpg', 'image');
        """)
        rows = get_files_without_voice_embedding(conn)
        ids = {row["id"] for row in rows}
        assert ids == {1, 2}

    def test_excludes_checked_files(self):
        from src.db.corpus import get_files_without_voice_embedding, set_voice_checked, upsert_voice_embedding
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (2, '/b.wav', 'audio')")
        upsert_voice_embedding(conn, 1, _blob(256, 0), "resemblyzer", 2000)
        set_voice_checked(conn, 1)
        rows = get_files_without_voice_embedding(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == 2

    def test_checked_file_with_zero_embeddings_stays_excluded(self):
        """A silent/too-short/errored file is marked checked with no embedding row —
        it must not be perpetually re-selected as pending (KB.AN1)."""
        from src.db.corpus import get_files_without_voice_embedding, set_voice_checked
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/silent.wav', 'audio')")
        set_voice_checked(conn, 1)
        assert get_files_without_voice_embedding(conn) == []

    def test_unchecked_file_with_no_embedding_stays_pending(self):
        from src.db.corpus import get_files_without_voice_embedding
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        rows = get_files_without_voice_embedding(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == 1

    def test_empty_corpus_returns_empty(self):
        from src.db.corpus import get_files_without_voice_embedding
        conn = _make_corpus_db()
        assert get_files_without_voice_embedding(conn) == []


class TestResetVoiceEmbeddings:
    def test_deletes_all_embeddings(self):
        from src.db.corpus import reset_voice_embeddings, upsert_voice_embedding
        conn = _make_corpus_db()
        for i in range(3):
            conn.execute(f"INSERT INTO files(id, path, file_type) VALUES ({i+1}, '/f{i}.wav', 'audio')")
            upsert_voice_embedding(conn, i + 1, _blob(256, i), "resemblyzer", 1000)
        n = reset_voice_embeddings(conn)
        assert n == 3
        count = conn.execute("SELECT COUNT(*) FROM file_voice_embeddings").fetchone()[0]
        assert count == 0


class TestGetVoiceEmbeddingsForExport:
    def test_returns_path_and_metadata(self):
        from src.db.corpus import get_voice_embeddings_for_export, upsert_voice_embedding
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        upsert_voice_embedding(conn, 1, _blob(256, 0), "resemblyzer", 4500)
        rows = get_voice_embeddings_for_export(conn)
        assert len(rows) == 1
        assert rows[0]["path"] == "/a.wav"
        assert rows[0]["duration_ms"] == 4500
        assert rows[0]["model"] == "resemblyzer"


# ---------------------------------------------------------------------------
# kb.py DB helpers
# ---------------------------------------------------------------------------

class TestGetPeopleWithVoiceCentroids:
    def test_returns_only_people_with_centroid(self):
        from src.db.kb import get_people_with_centroids
        conn = _make_kb_db()
        conn.execute(
            "INSERT INTO people(id, preferred_name, voice_centroid, voice_samples) VALUES (1, 'Alice', ?, 2)",
            (_blob(256, 0),),
        )
        conn.execute("INSERT INTO people(id, preferred_name) VALUES (2, 'Bob')")
        rows = get_people_with_centroids(conn, "voice")
        assert len(rows) == 1
        assert rows[0]["id"] == 1

    def test_returns_empty_when_none_have_centroid(self):
        from src.db.kb import get_people_with_centroids
        conn = _make_kb_db()
        conn.execute("INSERT INTO people(id, preferred_name) VALUES (1, 'Alice')")
        assert get_people_with_centroids(conn, "voice") == []


class TestUpdateVoiceCentroidDb:
    def test_updates_centroid_and_samples(self):
        from src.db.kb import update_person_centroid
        conn = _make_kb_db()
        conn.execute("INSERT INTO people(id, preferred_name) VALUES (1, 'Alice')")
        blob = _blob(256, 5)
        update_person_centroid(conn, 1, blob, 3, kind="voice")
        row = conn.execute("SELECT voice_centroid, voice_samples FROM people WHERE id = 1").fetchone()
        assert row["voice_samples"] == 3
        assert bytes(row["voice_centroid"]) == blob


class TestGetPeopleVoiceCentroidsForExport:
    def test_excludes_people_with_no_centroid(self):
        from src.db.kb import get_people_voice_centroids_for_export
        conn = _make_kb_db()
        conn.execute(
            "INSERT INTO people(id, preferred_name, voice_centroid, voice_samples) VALUES (1, 'Alice', ?, 1)",
            (_blob(256, 0),),
        )
        conn.execute("INSERT INTO people(id, preferred_name) VALUES (2, 'Bob')")
        rows = get_people_voice_centroids_for_export(conn)
        assert len(rows) == 1
        assert rows[0]["person_id"] == 1


# ---------------------------------------------------------------------------
# Helpers for mocking lazy imports in embed_voice
# ---------------------------------------------------------------------------

def _make_fake_resemblyzer(embedding: np.ndarray | None = None):
    """Build a minimal fake resemblyzer module."""
    mod = types.ModuleType("resemblyzer")

    class _FakeEncoder:
        def embed_utterance(self, wav):
            if embedding is not None:
                return embedding
            v = np.ones(256, dtype=np.float32)
            return v / float(np.linalg.norm(v))

    mod.VoiceEncoder = _FakeEncoder
    mod.preprocess_wav = lambda wav, source_sr=None: wav
    return mod


def _write_wav(path, n_samples: int = 32000, amplitude: int = 4096, sr: int = 16000):
    """Write a minimal 16 kHz mono PCM WAV file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([amplitude] * n_samples)))
    return path


# ---------------------------------------------------------------------------
# embed_voice — mocked Resemblyzer, real WAV files
# ---------------------------------------------------------------------------

class TestEmbedVoiceMocked:
    def test_returns_none_for_short_audio(self, tmp_path):
        """WAV shorter than _MIN_DURATION_S (1 s) returns (None, None)."""
        from src.stages.voice import embed_voice
        wav = _write_wav(tmp_path / "short.wav", n_samples=100)  # ~6 ms
        fake_res = _make_fake_resemblyzer()
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            result, dur = embed_voice(wav)
        assert result is None
        assert dur is None

    def test_returns_bytes_for_valid_audio(self, tmp_path):
        from src.stages.voice import _EMBEDDING_DIM, embed_voice
        embedding = np.ones(256, dtype=np.float32)
        embedding = embedding / float(np.linalg.norm(embedding))
        wav = _write_wav(tmp_path / "valid.wav", n_samples=32000)  # 2 s
        fake_res = _make_fake_resemblyzer(embedding)
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            result, dur = embed_voice(wav)
        assert result is not None
        assert len(result) == _EMBEDDING_DIM * 4
        assert dur == pytest.approx(2000, abs=50)

    def test_returns_none_for_corrupt_file(self, tmp_path):
        from src.stages.voice import embed_voice
        corrupt = tmp_path / "corrupt.wav"
        corrupt.write_bytes(b"\x00\xFF" * 50)
        fake_res = _make_fake_resemblyzer()
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            result, dur = embed_voice(corrupt)
        assert result is None
        assert dur is None
