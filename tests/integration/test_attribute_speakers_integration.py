"""Integration tests for KB.P19 Transcript Speaker Attribution — real SQLite, no ML."""
from pathlib import Path

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


def _make_config():
    from src.config import Config
    return Config()


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


def _add_voice_seg(corpus_conn, file_id, start_ms, end_ms, label="SPEAKER_00",
                   cluster_id=None, person_id=None, seg_index=0):
    corpus_conn.execute(
        "INSERT INTO file_voice_segments "
        "(file_id, segment_index, start_ms, end_ms, speaker_label, cluster_id, person_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, seg_index, start_ms, end_ms, label, cluster_id, person_id),
    )
    corpus_conn.commit()


def _add_ts(corpus_conn, file_id, start_ms, end_ms, text="Hello", speaker_label=None):
    cur = corpus_conn.execute(
        "INSERT INTO transcript_segments "
        "(file_id, start_ms, end_ms, text, speaker_label) "
        "VALUES (?, ?, ?, ?, ?)",
        (file_id, start_ms, end_ms, text, speaker_label),
    )
    corpus_conn.commit()
    return cur.lastrowid


def _add_cluster(corpus_conn, label=None, person_id=None) -> int:
    cur = corpus_conn.execute(
        "INSERT INTO voice_speaker_clusters (centroid, member_count, label, person_id) VALUES (?, 1, ?, ?)",
        (_blob(0), label, person_id),
    )
    corpus_conn.commit()
    return cur.lastrowid


def _add_person(kb_conn, name="Alice") -> int:
    cur = kb_conn.execute("INSERT INTO people(preferred_name) VALUES (?)", (name,))
    kb_conn.commit()
    return cur.lastrowid


@pytest.fixture
def attr_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    return corpus_conn, kb_conn, corpus_path, kb_path, tmp_path


def _run(corpus_path, kb_path, cancel_event=None):
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.attribute_speakers import run_attribute_speakers

    if cancel_event is None:
        cancel_event = make_cancel_event()
    return run_attribute_speakers(corpus_path, kb_path, _make_config(), NullProgressReporter(), cancel_event)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunAttributeSpeakersIntegration:
    def test_happy_path_attributes_overlapping_segment(self, attr_dbs):
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_voice_seg(corpus_conn, 1, 0, 5000, label="SPEAKER_00")
        sid = _add_ts(corpus_conn, 1, 0, 5000)

        result = _run(corpus_path, kb_path)

        assert result["files_processed"] == 1
        assert result["segments_attributed"] == 1
        row = corpus_conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] == "SPEAKER_00"

    def test_label_priority_person_name(self, attr_dbs):
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        pid = _add_person(kb_conn, "Alice")
        cid = _add_cluster(corpus_conn, label="Cluster A", person_id=pid)
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_voice_seg(corpus_conn, 1, 0, 5000, label="SPEAKER_00",
                       cluster_id=cid, person_id=pid)
        sid = _add_ts(corpus_conn, 1, 0, 5000)

        _run(corpus_path, kb_path)

        row = corpus_conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] == "Alice"

    def test_label_priority_cluster_label(self, attr_dbs):
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        cid = _add_cluster(corpus_conn, label="Meeting Group", person_id=None)
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_voice_seg(corpus_conn, 1, 0, 5000, label="SPEAKER_00",
                       cluster_id=cid, person_id=None)
        sid = _add_ts(corpus_conn, 1, 0, 5000)

        _run(corpus_path, kb_path)

        row = corpus_conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] == "Meeting Group"

    def test_label_priority_fallback_raw_label(self, attr_dbs):
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_voice_seg(corpus_conn, 1, 0, 5000, label="SPEAKER_01")
        sid = _add_ts(corpus_conn, 1, 0, 5000)

        _run(corpus_path, kb_path)

        row = corpus_conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] == "SPEAKER_01"

    def test_no_voice_segments_skips_file(self, attr_dbs):
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        sid = _add_ts(corpus_conn, 1, 0, 5000)

        result = _run(corpus_path, kb_path)

        assert result["files_processed"] == 0
        row = corpus_conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] is None

    def test_no_overlap_leaves_speaker_label_null(self, attr_dbs):
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_voice_seg(corpus_conn, 1, 8000, 12000, label="SPEAKER_00")
        sid = _add_ts(corpus_conn, 1, 0, 5000)

        result = _run(corpus_path, kb_path)

        assert result["segments_skipped"] == 1
        row = corpus_conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] is None

    def test_resume_already_attributed_segments_not_reprocessed(self, attr_dbs):
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_voice_seg(corpus_conn, 1, 0, 5000)
        _add_ts(corpus_conn, 1, 0, 5000, speaker_label="Bob")

        result = _run(corpus_path, kb_path)

        assert result["files_processed"] == 0
        assert result["segments_attributed"] == 0

    def test_force_reset_then_rerun(self, attr_dbs):
        from src.db.corpus import reset_transcript_speaker_labels
        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_voice_seg(corpus_conn, 1, 0, 5000, label="SPEAKER_00")
        sid = _add_ts(corpus_conn, 1, 0, 5000, speaker_label="OldLabel")  # noqa: F841
        corpus_conn.commit()

        n = reset_transcript_speaker_labels(corpus_conn)
        corpus_conn.commit()
        assert n == 1

        _run(corpus_path, kb_path)

        row = corpus_conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] == "SPEAKER_00"

    def test_cancel_mid_run_stops_after_committed_file(self, attr_dbs):
        from src.pipeline.cancel import make_cancel_event

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _ingest(corpus_conn, 2, "/audio/b.wav")
        _add_voice_seg(corpus_conn, 1, 0, 3000, label="SPEAKER_00", seg_index=0)
        _add_voice_seg(corpus_conn, 2, 0, 3000, label="SPEAKER_01", seg_index=0)
        _add_ts(corpus_conn, 1, 0, 3000)
        _add_ts(corpus_conn, 2, 0, 3000)

        cancel = make_cancel_event()
        cancel.set()

        result = _run(corpus_path, kb_path, cancel_event=cancel)
        assert result["files_processed"] == 0

    def test_export_writes_transcript_segments_csv(self, attr_dbs):
        from src.stages.export import _write_transcripts

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = attr_dbs
        _ingest(corpus_conn, 1, "/audio/a.wav")
        _add_ts(corpus_conn, 1, 0, 1000, text="Hello", speaker_label="Alice")
        corpus_conn.commit()

        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _write_transcripts(export_dir, corpus_conn)

        csv_path = export_dir / "transcript_segments.csv"
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8")
        assert "speaker_label" in content
        assert "Alice" in content

    def test_schema_has_speaker_label_column(self, attr_dbs):
        corpus_conn, _, _, _, _ = attr_dbs
        cols = [row[1] for row in corpus_conn.execute("PRAGMA table_info(transcript_segments)").fetchall()]
        assert "speaker_label" in cols

    def test_dag_attribute_speakers_has_expected_deps(self):
        from src.pipeline.dag import DEPENDENCIES
        assert "attribute_speakers" in DEPENDENCIES
        assert set(DEPENDENCIES["attribute_speakers"]) == {"transcribe", "voice_diarize"}
