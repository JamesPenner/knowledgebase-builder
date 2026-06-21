"""Integration tests for KB.P16 Voice Stage — mocked embed_voice, real SQLite."""
import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from src.db.corpus import open_corpus
from src.db.kb import open_kb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embedding(seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(256).astype(np.float32)
    v = v / float(np.linalg.norm(v))
    return v.tobytes()


def _make_config(*, similarity_threshold: float = 0.75):
    from src.config import Config
    return Config(voice_similarity_threshold=similarity_threshold)


def _ensure_source(corpus_conn) -> int:
    row = corpus_conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
    if row:
        return row["id"]
    return corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid


def _ingest_audio(corpus_conn, file_id: int, path: str, file_type: str = "audio") -> None:
    source_id = _ensure_source(corpus_conn)
    corpus_conn.execute(
        "INSERT OR IGNORE INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
        "VALUES (?, ?, ?, ?, '.wav', ?, 1000, 0.0)",
        (file_id, source_id, path, Path(path).name, file_type),
    )
    corpus_conn.commit()


@pytest.fixture
def voice_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    return corpus_conn, kb_conn, corpus_path, kb_path, tmp_path


# ---------------------------------------------------------------------------
# run_voice integration tests
# ---------------------------------------------------------------------------

class TestRunVoiceIntegration:
    def test_happy_path_no_people(self, voice_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = voice_dbs
        _ingest_audio(corpus_conn, 1, "/audio/track1.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        with patch("src.stages.voice.embed_voice", return_value=(_fake_embedding(0), 3000)):
            result = run_voice(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["files_processed"] == 1
        assert result["files_matched"] == 0
        assert result["files_skipped"] == 0
        assert result["errors"] == 0

        corpus_conn2 = open_corpus(corpus_path)
        rows = corpus_conn2.execute("SELECT * FROM file_voice_embeddings").fetchall()
        corpus_conn2.close()
        assert len(rows) == 1
        assert rows[0]["model"] == "resemblyzer"
        assert rows[0]["duration_ms"] == 3000

    def test_matches_known_person(self, voice_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = voice_dbs
        _ingest_audio(corpus_conn, 1, "/audio/track1.wav")
        corpus_conn.close()

        emb = _fake_embedding(42)
        kb_conn.execute(
            "INSERT INTO people(id, preferred_name, voice_centroid, voice_samples) VALUES (1, 'Alice', ?, 5)",
            (emb,),
        )
        kb_conn.commit()
        kb_conn.close()

        config = _make_config(similarity_threshold=0.10)
        with patch("src.stages.voice.embed_voice", return_value=(emb, 4000)):
            result = run_voice(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["files_matched"] == 1

        kb_conn2 = open_kb(kb_path)
        row = kb_conn2.execute("SELECT voice_samples FROM people WHERE id = 1").fetchone()
        kb_conn2.close()
        assert row["voice_samples"] == 6  # 5 prior + 1 new

    def test_resume_skips_already_embedded(self, voice_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = voice_dbs
        _ingest_audio(corpus_conn, 1, "/a.wav")
        _ingest_audio(corpus_conn, 2, "/b.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        call_count = 0

        def embed_side_effect(path, model="resemblyzer"):
            nonlocal call_count
            call_count += 1
            return (_fake_embedding(call_count), 2000)

        cancel1 = threading.Event()

        def cancel_after_one(path, model="resemblyzer"):
            cancel1.set()
            return (_fake_embedding(0), 2000)

        with patch("src.stages.voice.embed_voice", side_effect=cancel_after_one):
            run_voice(corpus_path, kb_path, config, NullProgressReporter(), cancel1)

        corpus_conn2 = open_corpus(corpus_path)
        first_count = corpus_conn2.execute(
            "SELECT COUNT(*) FROM file_voice_embeddings"
        ).fetchone()[0]
        corpus_conn2.close()
        assert first_count == 1

        # Second run — only the unprocessed file should be picked up
        processed_second = 0

        def count_calls(path, model="resemblyzer"):
            nonlocal processed_second
            processed_second += 1
            return (_fake_embedding(99), 1000)

        with patch("src.stages.voice.embed_voice", side_effect=count_calls):
            run_voice(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert processed_second == 1

    def test_no_audio_track_skips_file(self, voice_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = voice_dbs
        _ingest_audio(corpus_conn, 1, "/silent.mp4", file_type="video")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        with patch("src.stages.voice.embed_voice", return_value=(None, None)):
            result = run_voice(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["files_skipped"] == 1
        assert result["files_processed"] == 0

        corpus_conn2 = open_corpus(corpus_path)
        count = corpus_conn2.execute("SELECT COUNT(*) FROM file_voice_embeddings").fetchone()[0]
        corpus_conn2.close()
        assert count == 0

    def test_embed_error_increments_error_count(self, voice_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = voice_dbs
        _ingest_audio(corpus_conn, 1, "/bad.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        with patch("src.stages.voice.embed_voice", side_effect=RuntimeError("codec error")):
            result = run_voice(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["errors"] == 1
        assert result["files_processed"] == 0

    def test_images_are_not_processed(self, voice_dbs):
        """run_voice must ignore image files."""
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = voice_dbs
        source_id = _ensure_source(corpus_conn)
        corpus_conn.execute(
            "INSERT INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
            "VALUES (1, ?, '/photo.jpg', 'photo.jpg', '.jpg', 'image', 1000, 0.0)",
            (source_id,),
        )
        corpus_conn.commit()
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        embed_called = []

        def track_calls(path, model="resemblyzer"):
            embed_called.append(path)
            return (_fake_embedding(0), 2000)

        with patch("src.stages.voice.embed_voice", side_effect=track_calls):
            result = run_voice(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert embed_called == []
        assert result["files_processed"] == 0


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestVoiceHealthCheck:
    def test_voice_model_check_appears_in_results(self):
        from src.config import Config
        from src.health import run_checks
        from pathlib import Path

        config = Config()
        checks = run_checks(config, None, None, Path("."))
        ids = [c.id for c in checks]
        assert "voice_model" in ids

    def test_voice_model_check_severity_is_warning(self):
        from src.config import Config
        from src.health import run_checks

        config = Config()
        checks = run_checks(config, None, None, Path("."))
        vc = next(c for c in checks if c.id == "voice_model")
        assert vc.severity == "warning"

    def test_total_check_count_is_20(self):
        from src.config import Config
        from src.health import run_checks

        config = Config()
        checks = run_checks(config, None, None, Path("."))
        assert len(checks) == 23


# ---------------------------------------------------------------------------
# Export — people section includes voice_embeddings.csv
# ---------------------------------------------------------------------------

class TestVoiceExport:
    def test_voice_embeddings_csv_written(self, tmp_path):
        from src.db.corpus import open_corpus, upsert_voice_embedding
        from src.db.kb import open_kb
        from src.stages.export import _write_people

        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)

        source_id = corpus_conn.execute(
            "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
        ).lastrowid
        corpus_conn.execute(
            "INSERT INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
            "VALUES (1, ?, '/audio/clip.wav', 'clip.wav', '.wav', 'audio', 500, 0.0)",
            (source_id,),
        )
        emb = np.ones(256, dtype=np.float32)
        emb = emb / float(np.linalg.norm(emb))
        upsert_voice_embedding(corpus_conn, 1, emb.tobytes(), "resemblyzer", 2500)
        corpus_conn.commit()

        export_dir = tmp_path / "export"
        export_dir.mkdir()

        _write_people(export_dir, kb_conn, corpus_conn, export_biometric=False)

        csv_path = export_dir / "people" / "voice_embeddings.csv"
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8")
        assert "clip.wav" in content
        assert "2500" in content

    def test_voice_centroids_csv_only_when_export_biometric(self, tmp_path):
        from src.db.corpus import open_corpus
        from src.db.kb import open_kb
        from src.stages.export import _write_people

        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)

        emb = np.ones(256, dtype=np.float32)
        emb = emb / float(np.linalg.norm(emb))
        kb_conn.execute(
            "INSERT INTO people(id, preferred_name, voice_centroid, voice_samples) VALUES (1, 'Alice', ?, 2)",
            (emb.tobytes(),),
        )
        kb_conn.commit()

        export_dir = tmp_path / "export"
        export_dir.mkdir()

        _write_people(export_dir, kb_conn, corpus_conn, export_biometric=False)
        assert not (export_dir / "people" / "voice_centroids.csv").exists()

        _write_people(export_dir, kb_conn, corpus_conn, export_biometric=True)
        assert (export_dir / "people" / "voice_centroids.csv").exists()
        content = (export_dir / "people" / "voice_centroids.csv").read_text(encoding="utf-8")
        assert "Alice" in content
