"""Integration tests for KB.P17 Voice Diarization — mocked diarize/embed, real SQLite."""
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.db.corpus import open_corpus
from src.db.kb import open_kb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blob(seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(256).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


def _make_config(*, similarity_threshold: float = 0.75, min_segment_ms: int = 500):
    from src.config import Config
    return Config(
        voice_similarity_threshold=similarity_threshold,
        voice_diarization_min_segment_ms=min_segment_ms,
    )


def _ensure_source(corpus_conn) -> int:
    row = corpus_conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
    if row:
        return row["id"]
    return corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid


def _ingest(corpus_conn, file_id: int, path: str, file_type: str = "audio") -> None:
    source_id = _ensure_source(corpus_conn)
    corpus_conn.execute(
        "INSERT OR IGNORE INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
        "VALUES (?, ?, ?, ?, '.wav', ?, 1000, 0.0)",
        (file_id, source_id, path, Path(path).name, file_type),
    )
    corpus_conn.commit()


def _fake_segments(n_speakers: int = 2, segs_per_speaker: int = 2) -> list[dict]:
    segs = []
    t = 0
    for i in range(segs_per_speaker):
        for s in range(n_speakers):
            segs.append({"start_ms": t, "end_ms": t + 2000, "speaker_label": f"SPEAKER_{s:02d}"})
            t += 2000
    return segs


@contextmanager
def _mock_audio(has_speech: bool = True, has_clipping: bool = False):
    """Patch prepare_audio in voice.py to yield a fake AudioTrack."""
    track = MagicMock()
    track.wav_path = MagicMock()
    track.has_speech = has_speech
    track.has_clipping = has_clipping
    track.duration_ms = 3000

    @contextmanager
    def _fake(*args, **kwargs):
        yield track

    with patch("src.media.audiotrack.prepare_audio", new=_fake):
        yield track


@pytest.fixture
def diarize_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    return corpus_conn, kb_conn, corpus_path, kb_path, tmp_path


# ---------------------------------------------------------------------------
# run_voice_diarize integration tests
# ---------------------------------------------------------------------------

class TestRunVoiceDiarizeIntegration:
    def test_happy_path_two_speakers(self, diarize_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/audio/clip.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        segments = _fake_segments(n_speakers=2, segs_per_speaker=2)

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_voice_segment", return_value=_blob(0)):
            result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["files_processed"] == 1
        assert result["segments_found"] == 4
        assert result["errors"] == 0

        corpus_conn2 = open_corpus(corpus_path)
        count = corpus_conn2.execute("SELECT COUNT(*) FROM file_voice_segments").fetchone()[0]
        corpus_conn2.close()
        assert count == 4

    def test_resume_skips_already_processed(self, diarize_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/a.wav")
        _ingest(corpus_conn, 2, "/b.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        segments = _fake_segments(n_speakers=1, segs_per_speaker=1)
        cancel1 = threading.Event()
        call_count = 0

        def cancel_after_first(path, config):
            nonlocal call_count
            call_count += 1
            cancel1.set()
            return segments

        with _mock_audio(), patch("src.stages.voice.diarize_audio", side_effect=cancel_after_first), patch("src.stages.voice.embed_voice_segment", return_value=_blob(0)):
            run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), cancel1)

        second_calls = []

        def track(path, config):
            second_calls.append(path)
            return segments

        with _mock_audio(), patch("src.stages.voice.diarize_audio", side_effect=track), patch("src.stages.voice.embed_voice_segment", return_value=_blob(1)):
            run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert len(second_calls) == 1

    def test_no_segments_no_error(self, diarize_dbs):
        """Silent file: diarize returns [], file is still marked as processed."""
        from src.db.corpus import get_files_without_voice_segments, open_corpus
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/silent.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=[]), patch("src.stages.voice.embed_voice_segment", return_value=_blob(0)):
            result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["files_processed"] == 1
        assert result["segments_found"] == 0

        # KB.AN1: a file that legitimately produces zero segments must not be
        # re-selected as pending forever just because it has no segment rows.
        corpus_conn2 = open_corpus(corpus_path)
        pending = get_files_without_voice_segments(corpus_conn2)
        corpus_conn2.close()
        assert pending == []

    def test_segment_matches_known_person(self, diarize_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        corpus_conn.close()

        emb = _blob(42)
        kb_conn.execute(
            "INSERT INTO people(id, preferred_name, voice_centroid, voice_samples) VALUES (1, 'Alice', ?, 3)",
            (emb,),
        )
        kb_conn.commit()
        kb_conn.close()

        config = _make_config(similarity_threshold=0.10)
        segments = [{"start_ms": 0, "end_ms": 3000, "speaker_label": "SPEAKER_00"}]

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_voice_segment", return_value=emb):
            result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["segments_matched"] == 1

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute("SELECT person_id FROM file_voice_segments WHERE file_id = 1").fetchone()
        corpus_conn2.close()
        assert row["person_id"] == 1

        kb_conn2 = open_kb(kb_path)
        p = kb_conn2.execute("SELECT voice_samples FROM people WHERE id = 1").fetchone()
        kb_conn2.close()
        assert p["voice_samples"] == 4  # 3 prior + 1 new

    def test_unmatched_segment_creates_cluster(self, diarize_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config(similarity_threshold=0.99)  # very high — no match
        segments = [{"start_ms": 0, "end_ms": 3000, "speaker_label": "SPEAKER_00"}]

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_voice_segment", return_value=_blob(7)):
            run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        corpus_conn2 = open_corpus(corpus_path)
        cluster_count = corpus_conn2.execute("SELECT COUNT(*) FROM voice_speaker_clusters").fetchone()[0]
        seg_row = corpus_conn2.execute("SELECT cluster_id FROM file_voice_segments").fetchone()
        corpus_conn2.close()
        assert cluster_count == 1
        assert seg_row["cluster_id"] is not None

    def test_second_segment_joins_existing_cluster(self, diarize_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/a.wav")
        _ingest(corpus_conn, 2, "/b.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config(similarity_threshold=0.10)  # very low — always matches cluster
        segments = [{"start_ms": 0, "end_ms": 3000, "speaker_label": "SPEAKER_00"}]
        same_emb = _blob(5)

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_voice_segment", return_value=same_emb):
            run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        corpus_conn2 = open_corpus(corpus_path)
        cluster_count = corpus_conn2.execute("SELECT COUNT(*) FROM voice_speaker_clusters").fetchone()[0]
        total_member_count = corpus_conn2.execute(
            "SELECT SUM(member_count) FROM voice_speaker_clusters"
        ).fetchone()[0]
        corpus_conn2.close()
        assert cluster_count == 1
        assert total_member_count == 2

    def test_images_excluded(self, diarize_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        source_id = _ensure_source(corpus_conn)
        corpus_conn.execute(
            "INSERT INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
            "VALUES (1, ?, '/photo.jpg', 'photo.jpg', '.jpg', 'image', 1000, 0.0)",
            (source_id,),
        )
        corpus_conn.commit()
        corpus_conn.close()
        kb_conn.close()

        called = []
        with (
            patch("src.stages.voice.diarize_audio", side_effect=lambda p, c: called.append(p) or []),
            patch("src.stages.voice.embed_voice_segment", return_value=_blob(0)),
        ):
            run_voice_diarize(corpus_path, kb_path, _make_config(), NullProgressReporter(), make_cancel_event())

        assert called == []

    def test_diarize_error_increments_error_count(self, diarize_dbs):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/bad.wav")
        corpus_conn.close()
        kb_conn.close()

        with _mock_audio(), patch("src.stages.voice.diarize_audio", side_effect=RuntimeError("decode error")):
            result = run_voice_diarize(
                corpus_path, kb_path, _make_config(), NullProgressReporter(), make_cancel_event()
            )

        assert result["errors"] == 1
        assert result["files_processed"] == 0

    def test_force_resets_segments(self, diarize_dbs):
        from src.db.corpus import set_voice_diarize_checked, upsert_voice_segment
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        upsert_voice_segment(corpus_conn, 1, 0, 0, 1000, "SPEAKER_00", None, None, None, None)
        # A real prior run_voice_diarize call always sets this marker alongside
        # writing segment rows (KB.AN1) — simulate that here, not just the rows.
        set_voice_diarize_checked(corpus_conn, 1)
        corpus_conn.commit()
        corpus_conn.close()
        kb_conn.close()

        # Without force: file already has segments → skipped
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=[]) as mock_d, patch("src.stages.voice.embed_voice_segment", return_value=_blob(0)):
            run_voice_diarize(corpus_path, kb_path, _make_config(), NullProgressReporter(), make_cancel_event())
            assert mock_d.call_count == 0

        # With force reset then re-run
        corpus_conn2 = open_corpus(corpus_path)
        from src.db.corpus import reset_voice_segments
        reset_voice_segments(corpus_conn2)
        corpus_conn2.close()

        new_segments = [{"start_ms": 0, "end_ms": 2000, "speaker_label": "SPEAKER_00"}]
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=new_segments), patch("src.stages.voice.embed_voice_segment", return_value=_blob(0)):
            result = run_voice_diarize(
                corpus_path, kb_path, _make_config(), NullProgressReporter(), make_cancel_event()
            )

        assert result["files_processed"] == 1

    def test_embed_none_segment_stored_without_embedding(self, diarize_dbs):
        """Segments where embed_voice_segment returns None are still stored."""
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        corpus_conn.close()
        kb_conn.close()

        segments = [{"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00"}]
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_voice_segment", return_value=None):
            result = run_voice_diarize(
                corpus_path, kb_path, _make_config(), NullProgressReporter(), make_cancel_event()
            )

        assert result["files_processed"] == 1
        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute("SELECT embedding FROM file_voice_segments").fetchone()
        corpus_conn2.close()
        assert row is not None
        assert row["embedding"] is None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class TestVoiceDiarizeExport:
    def test_voice_segments_csv_written(self, tmp_path):
        from src.db.corpus import open_corpus, upsert_voice_segment
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
            "VALUES (1, ?, '/audio/meet.wav', 'meet.wav', '.wav', 'audio', 500, 0.0)",
            (source_id,),
        )
        upsert_voice_segment(corpus_conn, 1, 0, 0, 5000, "SPEAKER_00", None, None, None, None)
        upsert_voice_segment(corpus_conn, 1, 1, 5000, 9000, "SPEAKER_01", None, None, None, None)
        corpus_conn.commit()

        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _write_people(export_dir, kb_conn, corpus_conn, export_biometric=False)

        csv_path = export_dir / "people" / "voice_segments.csv"
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8")
        assert "meet.wav" in content
        assert "SPEAKER_00" in content
        assert "SPEAKER_01" in content

    def test_voice_segments_csv_empty_when_no_segments(self, tmp_path):
        from src.db.corpus import open_corpus
        from src.db.kb import open_kb
        from src.stages.export import _write_people

        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)

        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _write_people(export_dir, kb_conn, corpus_conn, export_biometric=False)

        csv_path = export_dir / "people" / "voice_segments.csv"
        assert csv_path.exists()
        lines = csv_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestVoiceDiarizeSchema:
    def test_new_tables_present(self, tmp_path):
        corpus_conn = open_corpus(tmp_path / "corpus.db")
        tables = {
            r[0] for r in corpus_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "file_voice_segments" in tables
        assert "voice_speaker_clusters" in tables
