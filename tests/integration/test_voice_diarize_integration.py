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


def _blob_result(seed: int = 0, duration_ms: int = 6000) -> tuple[bytes, int]:
    """(embedding, pooled_duration_ms) with a duration safely above the KB.AN2
    Criterion D ceiling (5.0s), so tests exercise the flat `voice_similarity_threshold`
    they configure rather than the duration-weighted ramp."""
    return _blob(seed), duration_ms


def _blob_with_similarity(base_seed: int, similarity: float) -> bytes:
    """A 256D unit vector with a specific cosine similarity to _blob(base_seed)."""
    base = np.frombuffer(_blob(base_seed), dtype=np.float32)
    rng = np.random.default_rng(base_seed + 1000)
    noise = rng.standard_normal(256).astype(np.float32)
    noise -= np.dot(noise, base) * base  # orthogonalize against base
    noise /= float(np.linalg.norm(noise))
    vec = similarity * base + (1.0 - similarity**2) ** 0.5 * noise
    vec /= float(np.linalg.norm(vec))
    return vec.astype(np.float32).tobytes()


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
    """Patch prepare_audio in voice.py to yield a fake AudioTrack.

    Also patches `_build_voice_encoder`/`_load_diarization_pipeline` (KB.AN2 —
    run_voice_diarize now constructs these once per run, outside the mocked
    `embed_pooled_voice_segments`/`diarize_audio` calls) so tests never touch
    the real resemblyzer/pyannote packages.
    """
    track = MagicMock()
    track.wav_path = MagicMock()
    track.has_speech = has_speech
    track.has_clipping = has_clipping
    track.duration_ms = 3000

    @contextmanager
    def _fake(*args, **kwargs):
        yield track

    with (
        patch("src.media.audiotrack.prepare_audio", new=_fake),
        patch("src.stages.voice._build_voice_encoder", return_value=MagicMock()),
        patch("src.stages.voice._load_diarization_pipeline", return_value=MagicMock()),
    ):
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

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(0)):
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

        def cancel_after_first(path, config, pipeline=None):
            nonlocal call_count
            call_count += 1
            cancel1.set()
            return segments

        with _mock_audio(), patch("src.stages.voice.diarize_audio", side_effect=cancel_after_first), patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(0)):
            run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), cancel1)

        second_calls = []

        def track(path, config, pipeline=None):
            second_calls.append(path)
            return segments

        with _mock_audio(), patch("src.stages.voice.diarize_audio", side_effect=track), patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(1)):
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
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=[]), patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(0)):
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

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_pooled_voice_segments", return_value=(emb, 6000)):
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

    def test_duration_weighted_threshold_rejects_short_pool(self, diarize_dbs):
        """KB.AN2 Criterion D: a similarity that clears the flat
        `voice_similarity_threshold` must still be rejected when the pooled
        audio is at the 1.5s floor and the boosted bar isn't cleared."""
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        corpus_conn.close()

        kb_conn.execute(
            "INSERT INTO people(id, preferred_name, voice_centroid, voice_samples) VALUES (1, 'Alice', ?, 3)",
            (_blob(1),),
        )
        kb_conn.commit()
        kb_conn.close()

        # Base threshold 0.80; similarity 0.83 clears it, but the 1.5s-floor
        # boosted bar (0.80 + 0.10 = 0.90) is not cleared.
        config = _make_config(similarity_threshold=0.80)
        segments = [{"start_ms": 0, "end_ms": 1500, "speaker_label": "SPEAKER_00"}]
        near_match = _blob_with_similarity(1, 0.83)

        with (
            _mock_audio(),
            patch("src.stages.voice.diarize_audio", return_value=segments),
            patch("src.stages.voice.embed_pooled_voice_segments", return_value=(near_match, 1500)),
        ):
            result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["segments_matched"] == 0

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute(
            "SELECT person_id, cluster_id FROM file_voice_segments WHERE file_id = 1"
        ).fetchone()
        corpus_conn2.close()
        assert row["person_id"] is None
        assert row["cluster_id"] is not None  # falls through to cluster creation instead

    def test_duration_weighted_threshold_accepts_same_similarity_at_full_duration(self, diarize_dbs):
        """The same borderline similarity (0.83) matches once the pooled
        audio reaches the ceiling duration, where the boosted bar relaxes
        back to the flat `voice_similarity_threshold` (0.80)."""
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        corpus_conn.close()

        kb_conn.execute(
            "INSERT INTO people(id, preferred_name, voice_centroid, voice_samples) VALUES (1, 'Alice', ?, 3)",
            (_blob(1),),
        )
        kb_conn.commit()
        kb_conn.close()

        config = _make_config(similarity_threshold=0.80)
        segments = [{"start_ms": 0, "end_ms": 6000, "speaker_label": "SPEAKER_00"}]
        near_match = _blob_with_similarity(1, 0.83)

        with (
            _mock_audio(),
            patch("src.stages.voice.diarize_audio", return_value=segments),
            patch("src.stages.voice.embed_pooled_voice_segments", return_value=(near_match, 6000)),
        ):
            result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["segments_matched"] == 1

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute("SELECT person_id FROM file_voice_segments WHERE file_id = 1").fetchone()
        corpus_conn2.close()
        assert row["person_id"] == 1

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

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(7)):
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

        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_pooled_voice_segments", return_value=(same_emb, 6000)):
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
            patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(0)),
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
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=[]) as mock_d, patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(0)):
            run_voice_diarize(corpus_path, kb_path, _make_config(), NullProgressReporter(), make_cancel_event())
            assert mock_d.call_count == 0

        # With force reset then re-run
        corpus_conn2 = open_corpus(corpus_path)
        from src.db.corpus import reset_voice_segments
        reset_voice_segments(corpus_conn2)
        corpus_conn2.close()

        new_segments = [{"start_ms": 0, "end_ms": 2000, "speaker_label": "SPEAKER_00"}]
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=new_segments), patch("src.stages.voice.embed_pooled_voice_segments", return_value=_blob_result(0)):
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
        with _mock_audio(), patch("src.stages.voice.diarize_audio", return_value=segments), patch("src.stages.voice.embed_pooled_voice_segments", return_value=(None, None)):
            result = run_voice_diarize(
                corpus_path, kb_path, _make_config(), NullProgressReporter(), make_cancel_event()
            )

        assert result["files_processed"] == 1
        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute("SELECT embedding FROM file_voice_segments").fetchone()
        corpus_conn2.close()
        assert row is not None
        assert row["embedding"] is None

    def test_pooling_calls_embed_once_per_label_and_propagates_match(self, diarize_dbs):
        """KB.AN2 Criterion A: turns sharing a local speaker label are pooled into
        one embed call, and the resulting embedding/match propagates to every
        turn under that label."""
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        # 3 turns per speaker, 2 speakers, interleaved — 6 turns, 2 unique labels.
        segments = _fake_segments(n_speakers=2, segs_per_speaker=3)

        calls = []

        def _embed(wav_path, spans, model_name="resemblyzer", encoder=None):
            calls.append(spans)
            return _blob_result(seed=len(calls))

        with (
            _mock_audio(),
            patch("src.stages.voice.diarize_audio", return_value=segments),
            patch("src.stages.voice.embed_pooled_voice_segments", side_effect=_embed) as mock_embed,
        ):
            result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["segments_found"] == 6
        assert mock_embed.call_count == 2  # one call per unique local speaker label, not per turn
        # Each call was pooled from the 3 turns sharing that label.
        assert all(len(spans) == 3 for spans in calls)

        corpus_conn2 = open_corpus(corpus_path)
        rows = corpus_conn2.execute(
            "SELECT speaker_label, embedding, person_id, cluster_id, similarity "
            "FROM file_voice_segments WHERE file_id = 1 ORDER BY segment_index"
        ).fetchall()
        corpus_conn2.close()

        by_label: dict[str, set] = {}
        for row in rows:
            by_label.setdefault(row["speaker_label"], set()).add(
                (bytes(row["embedding"]), row["person_id"], row["cluster_id"], row["similarity"])
            )
        # Every turn sharing a label carries the identical (embedding, match) tuple.
        assert all(len(variants) == 1 for variants in by_label.values())
        # The two labels were embedded independently — distinct pooled audio.
        embeddings_by_label = {label: next(iter(v))[0] for label, v in by_label.items()}
        assert len(set(embeddings_by_label.values())) == 2

    def test_overlapping_turn_excluded_from_pooling_but_still_stored(self, diarize_dbs):
        """KB.AN2 Criterion C: a cross-talk turn (overlaps a turn from a
        different label) is excluded from the pooled identity-matching audio,
        but its file_voice_segments row still carries its raw speaker_label
        and start/end — unaffected, since transcript attribution reads those,
        not the embedding match."""
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.voice import run_voice_diarize

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = diarize_dbs
        _ingest(corpus_conn, 1, "/clip.wav")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        segments = [
            {"start_ms": 0, "end_ms": 2000, "speaker_label": "SPEAKER_00"},      # overlaps SPEAKER_01
            {"start_ms": 1500, "end_ms": 3000, "speaker_label": "SPEAKER_01"},   # overlaps SPEAKER_00, its only turn
            {"start_ms": 4000, "end_ms": 6000, "speaker_label": "SPEAKER_00"},   # clean
        ]

        calls = []

        def _embed(wav_path, spans, model_name="resemblyzer", encoder=None):
            calls.append(spans)
            return (None, None) if not spans else _blob_result(seed=len(calls))

        with (
            _mock_audio(),
            patch("src.stages.voice.diarize_audio", return_value=segments),
            patch("src.stages.voice.embed_pooled_voice_segments", side_effect=_embed),
        ):
            result = run_voice_diarize(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["segments_found"] == 3
        # SPEAKER_00's pooled spans exclude its overlapping turn (0, 2000).
        assert (0, 2000) not in [tuple(s) for call in calls for s in call]
        assert (4000, 6000) in [tuple(s) for call in calls for s in call]
        # SPEAKER_01's only turn is overlapping, so its pooling spans are empty.
        assert [] in calls

        corpus_conn2 = open_corpus(corpus_path)
        rows = {
            (row["start_ms"], row["end_ms"]): row
            for row in corpus_conn2.execute(
                "SELECT start_ms, end_ms, speaker_label, embedding "
                "FROM file_voice_segments WHERE file_id = 1"
            ).fetchall()
        }
        corpus_conn2.close()

        # All three raw turns are stored regardless of pooling participation.
        assert rows[(0, 2000)]["speaker_label"] == "SPEAKER_00"
        assert rows[(1500, 3000)]["speaker_label"] == "SPEAKER_01"
        assert rows[(4000, 6000)]["speaker_label"] == "SPEAKER_00"
        # SPEAKER_00's match (from its clean turn) propagates to its overlapping turn too.
        assert rows[(0, 2000)]["embedding"] is not None
        assert bytes(rows[(0, 2000)]["embedding"]) == bytes(rows[(4000, 6000)]["embedding"])
        # SPEAKER_01 had no clean audio to pool, so its embedding is None.
        assert rows[(1500, 3000)]["embedding"] is None


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
