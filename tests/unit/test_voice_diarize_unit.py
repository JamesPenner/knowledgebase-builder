"""Unit tests for KB.P17 Voice Diarization — no pyannote inference, no filesystem."""
import sqlite3
import struct
import types
import unittest.mock as mock
import warnings
import wave

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blob(dim: int = 256, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


def _make_corpus_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE files (
            id                       INTEGER PRIMARY KEY,
            path                     TEXT    NOT NULL,
            file_type                TEXT    NOT NULL DEFAULT 'audio',
            voice_diarize_checked_at DATETIME
        );
        CREATE TABLE file_voice_segments (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id        INTEGER NOT NULL,
            segment_index  INTEGER NOT NULL,
            start_ms       INTEGER NOT NULL,
            end_ms         INTEGER NOT NULL,
            speaker_label  TEXT    NOT NULL,
            embedding      BLOB,
            cluster_id     INTEGER,
            person_id      INTEGER,
            similarity     REAL,
            processed_at   DATETIME DEFAULT (datetime('now')),
            UNIQUE(file_id, segment_index)
        );
        CREATE TABLE voice_speaker_clusters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            centroid     BLOB    NOT NULL,
            member_count INTEGER NOT NULL DEFAULT 0,
            spread       REAL,
            label        TEXT,
            person_id    INTEGER,
            created_at   DATETIME DEFAULT (datetime('now'))
        );
    """)
    return conn


def _make_wav(path, duration_ms: int = 200, sr: int = 16000):
    """Write a minimal silent WAV file so wave.open succeeds in diarize_audio."""
    import wave as _wave
    n_samples = int(sr * duration_ms / 1000)
    with _wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n_samples)
    return path


def _make_fake_pyannote(segments: list[dict]):
    """Build minimal fake pyannote + torch modules for diarize_audio tests.

    Returns (pyannote_mod, audio_mod, torch_mod).  All three must be patched
    into sys.modules so the real PyTorch C extension is never invoked across
    sequential test runs (which causes segfaults on Windows).
    """
    mod = types.ModuleType("pyannote")
    audio_mod = types.ModuleType("pyannote.audio")

    class _FakeTurn:
        def __init__(self, start, end):
            self.start = start
            self.end = end

    class _FakeDiarization:
        def itertracks(self, yield_label=False):
            for seg in segments:
                yield _FakeTurn(seg["start"], seg["end"]), None, seg["label"]

    class _FakePipeline:
        @classmethod
        def from_pretrained(cls, model_id, **kwargs):
            return cls()

        def __call__(self, audio_input):
            return _FakeDiarization()

    audio_mod.Pipeline = _FakePipeline
    mod.audio = audio_mod

    torch_mod = types.ModuleType("torch")
    _mock_tensor = mock.MagicMock()
    _mock_tensor.unsqueeze.return_value = _mock_tensor
    torch_mod.tensor = mock.MagicMock(return_value=_mock_tensor)

    return mod, audio_mod, torch_mod


# ---------------------------------------------------------------------------
# diarize_audio
# ---------------------------------------------------------------------------

class TestDiarizeAudio:
    def _config(self, min_ms: int = 500):
        from src.config import Config
        return Config(
            diarization_model="pyannote/speaker-diarization-3.1",
            voice_diarization_min_segment_ms=min_ms,
        )

    def test_returns_sorted_segments(self, tmp_path):
        from src.stages.voice import diarize_audio
        raw = [
            {"start": 5.0, "end": 10.0, "label": "SPEAKER_01"},
            {"start": 0.0, "end": 4.0,  "label": "SPEAKER_00"},
        ]
        pyannote_mod, audio_mod, torch_mod = _make_fake_pyannote(raw)
        wav = _make_wav(tmp_path / "a.wav")
        with mock.patch.dict("sys.modules", {"pyannote": pyannote_mod, "pyannote.audio": audio_mod, "torch": torch_mod}):
            result = diarize_audio(wav, self._config())
        assert len(result) == 2
        assert result[0]["start_ms"] < result[1]["start_ms"]

    def test_filters_short_segments(self, tmp_path):
        from src.stages.voice import diarize_audio
        raw = [
            {"start": 0.0, "end": 0.3, "label": "SPEAKER_00"},   # 300ms — below 500ms threshold
            {"start": 1.0, "end": 2.0, "label": "SPEAKER_01"},   # 1000ms — kept
        ]
        pyannote_mod, audio_mod, torch_mod = _make_fake_pyannote(raw)
        wav = _make_wav(tmp_path / "a.wav")
        with mock.patch.dict("sys.modules", {"pyannote": pyannote_mod, "pyannote.audio": audio_mod, "torch": torch_mod}):
            result = diarize_audio(wav, self._config(min_ms=500))
        assert len(result) == 1
        assert result[0]["speaker_label"] == "SPEAKER_01"

    def test_returns_empty_on_error(self, tmp_path):
        from src.stages.voice import diarize_audio
        pyannote_mod, audio_mod, torch_mod = _make_fake_pyannote([])

        class _BrokenPipeline:
            @classmethod
            def from_pretrained(cls, *a, **kw):
                return cls()
            def __call__(self, path):
                raise RuntimeError("codec error")

        audio_mod.Pipeline = _BrokenPipeline
        wav = _make_wav(tmp_path / "a.wav")
        with mock.patch.dict("sys.modules", {"pyannote": pyannote_mod, "pyannote.audio": audio_mod, "torch": torch_mod}):
            result = diarize_audio(wav, self._config())
        assert result == []

    def test_raises_model_load_error_if_not_installed(self, tmp_path):
        from src.stages.voice import ModelLoadError, diarize_audio
        with mock.patch.dict("sys.modules", {"pyannote": None, "pyannote.audio": None}):
            with pytest.raises(ModelLoadError):
                diarize_audio(tmp_path / "a.wav", self._config())

    def test_segment_fields(self, tmp_path):
        from src.stages.voice import diarize_audio
        raw = [{"start": 1.0, "end": 3.5, "label": "SPEAKER_00"}]
        pyannote_mod, audio_mod, torch_mod = _make_fake_pyannote(raw)
        wav = _make_wav(tmp_path / "a.wav")
        with mock.patch.dict("sys.modules", {"pyannote": pyannote_mod, "pyannote.audio": audio_mod, "torch": torch_mod}):
            result = diarize_audio(wav, self._config())
        assert result[0]["start_ms"] == 1000
        assert result[0]["end_ms"] == 3500
        assert result[0]["speaker_label"] == "SPEAKER_00"

    def test_empty_diarization_returns_empty_list(self, tmp_path):
        from src.stages.voice import diarize_audio
        pyannote_mod, audio_mod, torch_mod = _make_fake_pyannote([])
        wav = _make_wav(tmp_path / "a.wav")
        with mock.patch.dict("sys.modules", {"pyannote": pyannote_mod, "pyannote.audio": audio_mod, "torch": torch_mod}):
            result = diarize_audio(wav, self._config())
        assert result == []

    def test_pipeline_load_failure_raises_model_load_error(self, tmp_path):
        """KB.AN2: the pipeline now loads once per run rather than being
        silently retried per file, so a load failure (auth/network/missing
        cache) must surface as ModelLoadError, not degrade to an empty result."""
        from src.stages.voice import ModelLoadError, diarize_audio

        mod = types.ModuleType("pyannote")
        audio_mod = types.ModuleType("pyannote.audio")

        class _FailingPipeline:
            @classmethod
            def from_pretrained(cls, *a, **kw):
                raise RuntimeError("401 unauthorized")

        audio_mod.Pipeline = _FailingPipeline
        mod.audio = audio_mod

        wav = _make_wav(tmp_path / "a.wav")
        with mock.patch.dict("sys.modules", {"pyannote": mod, "pyannote.audio": audio_mod}):
            with pytest.raises(ModelLoadError):
                diarize_audio(wav, self._config())

    def test_reused_pipeline_skips_loading(self, tmp_path):
        """KB.AN2: a `pipeline` passed in must be used directly — no fresh
        Pipeline.from_pretrained() call, even if pyannote.audio is unavailable."""
        from src.stages.voice import diarize_audio

        class _FakeTurn:
            def __init__(self, start, end):
                self.start = start
                self.end = end

        class _FakeDiarization:
            def itertracks(self, yield_label=False):
                yield _FakeTurn(0.0, 2.0), None, "SPEAKER_00"

        class _PreloadedPipeline:
            def __call__(self, audio_input):
                return _FakeDiarization()

            @classmethod
            def from_pretrained(cls, *a, **kw):
                raise AssertionError("from_pretrained should not be called when a pipeline is provided")

        torch_mod = types.ModuleType("torch")
        _mock_tensor = mock.MagicMock()
        _mock_tensor.unsqueeze.return_value = _mock_tensor
        torch_mod.tensor = mock.MagicMock(return_value=_mock_tensor)

        wav = _make_wav(tmp_path / "a.wav")
        with mock.patch.dict("sys.modules", {"torch": torch_mod}):
            result = diarize_audio(wav, self._config(), pipeline=_PreloadedPipeline())

        assert len(result) == 1
        assert result[0]["speaker_label"] == "SPEAKER_00"


# ---------------------------------------------------------------------------
# _load_diarization_pipeline — warning scoping (KB.AN2 Criterion E)
# ---------------------------------------------------------------------------

class _WarningModule(types.ModuleType):
    """A fake `pyannote.audio` module whose `Pipeline` attribute emits a
    UserWarning on access — attribute access on an already-cached module
    still goes through class-level descriptors, so this fires even though
    `from pyannote.audio import Pipeline` doesn't re-execute module code."""

    def __init__(self, name, warning_message):
        super().__init__(name)
        self._warning_message = warning_message

    @property
    def Pipeline(self):
        import warnings as _warnings
        _warnings.warn(self._warning_message, UserWarning)

        class _FakePipeline:
            @classmethod
            def from_pretrained(cls, *a, **kw):
                return cls()
        return _FakePipeline


class TestLoadDiarizationPipelineWarnings:
    def _config(self):
        from src.config import Config
        return Config(diarization_model="pyannote/speaker-diarization-3.1")

    def test_torchcodec_warning_suppressed(self):
        from src.stages.voice import _load_diarization_pipeline

        audio_mod = _WarningModule("pyannote.audio", "TorchCodec is not available: some detail")
        pyannote_mod = types.ModuleType("pyannote")
        pyannote_mod.audio = audio_mod

        with mock.patch.dict("sys.modules", {"pyannote": pyannote_mod, "pyannote.audio": audio_mod}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                _load_diarization_pipeline(self._config())

        assert not any("torchcodec" in str(w.message).lower() for w in caught)

    def test_unrelated_warning_still_surfaces(self):
        """Only the confirmed-harmless torchcodec message is suppressed — a
        different warning from the same import must still be visible."""
        from src.stages.voice import _load_diarization_pipeline

        audio_mod = _WarningModule("pyannote.audio", "Some unrelated deprecation notice")
        pyannote_mod = types.ModuleType("pyannote")
        pyannote_mod.audio = audio_mod

        with mock.patch.dict("sys.modules", {"pyannote": pyannote_mod, "pyannote.audio": audio_mod}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                _load_diarization_pipeline(self._config())

        assert any("unrelated deprecation notice" in str(w.message).lower() for w in caught)


# ---------------------------------------------------------------------------
# _find_overlapping_indices
# ---------------------------------------------------------------------------

class TestFindOverlappingIndices:
    def test_no_overlap_returns_empty_set(self):
        from src.stages.voice import _find_overlapping_indices
        segments = [
            {"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00"},
            {"start_ms": 1000, "end_ms": 2000, "speaker_label": "SPEAKER_01"},
        ]
        assert _find_overlapping_indices(segments) == set()

    def test_touching_boundary_not_overlap(self):
        """Half-open intervals: [0,1000) and [1000,2000) share only a boundary point."""
        from src.stages.voice import _find_overlapping_indices
        segments = [
            {"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00"},
            {"start_ms": 1000, "end_ms": 2000, "speaker_label": "SPEAKER_01"},
        ]
        assert _find_overlapping_indices(segments) == set()

    def test_cross_talk_detected(self):
        from src.stages.voice import _find_overlapping_indices
        segments = [
            {"start_ms": 0, "end_ms": 5000, "speaker_label": "SPEAKER_00"},
            {"start_ms": 4000, "end_ms": 8000, "speaker_label": "SPEAKER_01"},
        ]
        assert _find_overlapping_indices(segments) == {0, 1}

    def test_same_label_overlap_not_flagged(self):
        """Overlapping turns sharing one label aren't cross-talk — pooling a
        speaker's own audio with itself doesn't corrupt the embedding."""
        from src.stages.voice import _find_overlapping_indices
        segments = [
            {"start_ms": 0, "end_ms": 5000, "speaker_label": "SPEAKER_00"},
            {"start_ms": 4000, "end_ms": 8000, "speaker_label": "SPEAKER_00"},
        ]
        assert _find_overlapping_indices(segments) == set()

    def test_one_speaker_overlaps_two_others(self):
        segments = [
            {"start_ms": 0, "end_ms": 5000, "speaker_label": "SPEAKER_00"},   # overlaps both
            {"start_ms": 1000, "end_ms": 2000, "speaker_label": "SPEAKER_01"},
            {"start_ms": 3000, "end_ms": 4000, "speaker_label": "SPEAKER_02"},
        ]
        from src.stages.voice import _find_overlapping_indices
        assert _find_overlapping_indices(segments) == {0, 1, 2}

    def test_nonoverlapping_turn_not_flagged(self):
        segments = [
            {"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00"},
            {"start_ms": 500, "end_ms": 1500, "speaker_label": "SPEAKER_01"},   # overlaps SPEAKER_00
            {"start_ms": 2000, "end_ms": 3000, "speaker_label": "SPEAKER_02"},  # clean
        ]
        from src.stages.voice import _find_overlapping_indices
        assert _find_overlapping_indices(segments) == {0, 1}


# ---------------------------------------------------------------------------
# _build_voice_encoder
# ---------------------------------------------------------------------------

class TestBuildVoiceEncoder:
    def test_raises_model_load_error_if_not_installed(self):
        from src.stages.voice import ModelLoadError, _build_voice_encoder
        with mock.patch.dict("sys.modules", {"resemblyzer": None}):
            with pytest.raises(ModelLoadError):
                _build_voice_encoder()

    def test_constructs_with_verbose_false(self):
        """Silences resemblyzer's third-party per-construction log line."""
        from src.stages.voice import _build_voice_encoder

        calls = []
        mod = types.ModuleType("resemblyzer")

        class _FakeEncoder:
            def __init__(self, verbose=True):
                calls.append(verbose)

        mod.VoiceEncoder = _FakeEncoder
        with mock.patch.dict("sys.modules", {"resemblyzer": mod}):
            _build_voice_encoder()
        assert calls == [False]


# ---------------------------------------------------------------------------
# _duration_weighted_threshold
# ---------------------------------------------------------------------------

class TestDurationWeightedThreshold:
    def test_floor_duration_applies_max_penalty(self):
        from src.stages.voice import _duration_weighted_threshold
        assert _duration_weighted_threshold(1.5, 0.75) == pytest.approx(0.85)

    def test_ceiling_duration_applies_no_penalty(self):
        from src.stages.voice import _duration_weighted_threshold
        assert _duration_weighted_threshold(5.0, 0.75) == pytest.approx(0.75)

    def test_beyond_ceiling_stays_at_base(self):
        from src.stages.voice import _duration_weighted_threshold
        assert _duration_weighted_threshold(8.0, 0.75) == pytest.approx(0.75)

    def test_midpoint_ramps_linearly(self):
        from src.stages.voice import _duration_weighted_threshold
        # Midpoint of [1.5, 5.0] is 3.25s — half the max penalty.
        assert _duration_weighted_threshold(3.25, 0.75) == pytest.approx(0.80)

    def test_below_floor_clamps_to_max_penalty(self):
        """Shouldn't happen in practice (floor == _MIN_DURATION_S), but a
        duration below it must not extrapolate past the max penalty."""
        from src.stages.voice import _duration_weighted_threshold
        assert _duration_weighted_threshold(0.5, 0.75) == pytest.approx(0.85)

    def test_penalty_never_pushes_threshold_above_one(self):
        from src.stages.voice import _duration_weighted_threshold
        assert _duration_weighted_threshold(1.5, 0.95) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# embed_pooled_voice_segments
# ---------------------------------------------------------------------------

def _make_fake_resemblyzer():
    mod = types.ModuleType("resemblyzer")
    class _FakeEncoder:
        def __init__(self, verbose=True):
            pass
        def embed_utterance(self, wav):
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


class TestEmbedPooledVoiceSegments:
    def test_returns_bytes_and_duration_for_valid_span(self, tmp_path):
        from src.stages.voice import _EMBEDDING_DIM, embed_pooled_voice_segments
        wav = _write_wav(tmp_path / "a.wav", n_samples=32000)  # 2 s
        fake_res = _make_fake_resemblyzer()
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            embedding, duration_ms = embed_pooled_voice_segments(wav, [(0, 2000)])
        assert embedding is not None
        assert len(embedding) == _EMBEDDING_DIM * 4
        assert duration_ms == pytest.approx(2000, abs=10)

    def test_pools_multiple_spans_into_one_call(self, tmp_path):
        """Two spans that individually miss _MIN_DURATION_S combine to clear it."""
        from src.stages.voice import embed_pooled_voice_segments
        wav = _write_wav(tmp_path / "a.wav", n_samples=32000)  # 2 s
        fake_res = _make_fake_resemblyzer()
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            # Two 800ms spans pool to 1.6s — clears the 1.5s minimum, neither alone would.
            embedding, duration_ms = embed_pooled_voice_segments(wav, [(0, 800), (1000, 1800)])
        assert embedding is not None
        assert duration_ms == pytest.approx(1600, abs=10)

    def test_returns_none_for_no_spans(self, tmp_path):
        from src.stages.voice import embed_pooled_voice_segments
        embedding, duration_ms = embed_pooled_voice_segments(tmp_path / "a.wav", [])
        assert embedding is None
        assert duration_ms is None

    def test_skips_zero_and_negative_duration_spans(self, tmp_path):
        """Degenerate spans are skipped rather than aborting the whole pool."""
        from src.stages.voice import embed_pooled_voice_segments
        wav = _write_wav(tmp_path / "a.wav", n_samples=32000)  # 2 s
        fake_res = _make_fake_resemblyzer()
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            embedding, duration_ms = embed_pooled_voice_segments(wav, [(1000, 1000), (2000, 1000), (0, 2000)])
        assert embedding is not None

    def test_returns_none_on_load_error(self, tmp_path):
        from src.stages.voice import embed_pooled_voice_segments
        corrupt = tmp_path / "corrupt.wav"
        corrupt.write_bytes(b"\x00\xFF" * 50)
        fake_res = _make_fake_resemblyzer()
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            embedding, duration_ms = embed_pooled_voice_segments(corrupt, [(0, 2000)])
        assert embedding is None
        assert duration_ms is None

    def test_returns_none_for_too_short_pooled_audio(self, tmp_path):
        """Pooled duration < _MIN_DURATION_S (1.5 s) returns None."""
        from src.stages.voice import embed_pooled_voice_segments
        wav = _write_wav(tmp_path / "a.wav", n_samples=32000)  # 2 s WAV
        fake_res = _make_fake_resemblyzer()
        with mock.patch.dict("sys.modules", {"resemblyzer": fake_res}):
            # Two 50ms spans pool to only 100ms — still below the 1.5 s minimum
            embedding, duration_ms = embed_pooled_voice_segments(wav, [(0, 50), (1000, 1050)])
        assert embedding is None
        assert duration_ms is None

    def test_reuses_provided_encoder_without_reconstructing(self, tmp_path):
        """KB.AN2: a passed-in `encoder` must be used directly — no fresh
        VoiceEncoder constructed, even though the class remains importable."""
        from src.stages.voice import embed_pooled_voice_segments

        class _FakeEncoder:
            def embed_utterance(self, wav):
                v = np.ones(256, dtype=np.float32)
                return v / float(np.linalg.norm(v))

        class _UnusedEncoderClass:
            def __init__(self, *a, **kw):
                raise AssertionError("VoiceEncoder should not be constructed when an encoder is provided")

        mod = types.ModuleType("resemblyzer")
        mod.VoiceEncoder = _UnusedEncoderClass
        mod.preprocess_wav = lambda wav, source_sr=None: wav

        wav = _write_wav(tmp_path / "a.wav", n_samples=32000)  # 2 s
        with mock.patch.dict("sys.modules", {"resemblyzer": mod}):
            embedding, duration_ms = embed_pooled_voice_segments(wav, [(0, 2000)], encoder=_FakeEncoder())
        assert embedding is not None


# ---------------------------------------------------------------------------
# corpus.py DB helpers
# ---------------------------------------------------------------------------

class TestUpsertVoiceSegment:
    def test_inserts_new_row(self):
        from src.db.corpus import upsert_voice_segment
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        upsert_voice_segment(conn, 1, 0, 0, 2000, "SPEAKER_00", _blob(), None, None, None)
        rows = conn.execute("SELECT * FROM file_voice_segments").fetchall()
        assert len(rows) == 1
        assert rows[0]["speaker_label"] == "SPEAKER_00"

    def test_replace_on_conflict(self):
        from src.db.corpus import upsert_voice_segment
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        upsert_voice_segment(conn, 1, 0, 0, 2000, "SPEAKER_00", None, None, None, None)
        upsert_voice_segment(conn, 1, 0, 0, 3000, "SPEAKER_00", _blob(), None, 5, 0.9)
        rows = conn.execute("SELECT * FROM file_voice_segments").fetchall()
        assert len(rows) == 1
        assert rows[0]["end_ms"] == 3000
        assert rows[0]["person_id"] == 5

    def test_embedding_nullable(self):
        from src.db.corpus import upsert_voice_segment
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        upsert_voice_segment(conn, 1, 0, 0, 500, "SPEAKER_00", None, None, None, None)
        row = conn.execute("SELECT embedding FROM file_voice_segments").fetchone()
        assert row["embedding"] is None


class TestGetFilesWithoutVoiceSegments:
    def test_returns_audio_and_video(self):
        from src.db.corpus import get_files_without_voice_segments
        conn = _make_corpus_db()
        conn.executescript("""
            INSERT INTO files(id, path, file_type) VALUES (1, '/a.mp3', 'audio');
            INSERT INTO files(id, path, file_type) VALUES (2, '/b.mp4', 'video');
            INSERT INTO files(id, path, file_type) VALUES (3, '/c.jpg', 'image');
        """)
        rows = get_files_without_voice_segments(conn)
        assert {r["id"] for r in rows} == {1, 2}

    def test_excludes_checked_files(self):
        from src.db.corpus import get_files_without_voice_segments, set_voice_diarize_checked, upsert_voice_segment
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (2, '/b.wav', 'audio')")
        upsert_voice_segment(conn, 1, 0, 0, 1000, "SPEAKER_00", None, None, None, None)
        set_voice_diarize_checked(conn, 1)
        rows = get_files_without_voice_segments(conn)
        assert len(rows) == 1 and rows[0]["id"] == 2

    def test_checked_file_with_zero_segments_stays_excluded(self):
        """A silent/too-short/errored file is marked checked with no segment rows —
        it must not be perpetually re-selected as pending (KB.AN1)."""
        from src.db.corpus import get_files_without_voice_segments, set_voice_diarize_checked
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/silent.wav', 'audio')")
        set_voice_diarize_checked(conn, 1)
        assert get_files_without_voice_segments(conn) == []

    def test_unchecked_file_with_no_segments_stays_pending(self):
        from src.db.corpus import get_files_without_voice_segments
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/a.wav', 'audio')")
        rows = get_files_without_voice_segments(conn)
        assert len(rows) == 1 and rows[0]["id"] == 1

    def test_empty_corpus(self):
        from src.db.corpus import get_files_without_voice_segments
        conn = _make_corpus_db()
        assert get_files_without_voice_segments(conn) == []


class TestResetVoiceSegments:
    def test_deletes_all(self):
        from src.db.corpus import reset_voice_segments, upsert_voice_segment
        conn = _make_corpus_db()
        for i in range(3):
            conn.execute(f"INSERT INTO files(id, path, file_type) VALUES ({i+1}, '/f{i}.wav', 'audio')")
            upsert_voice_segment(conn, i + 1, 0, 0, 1000, "SPEAKER_00", None, None, None, None)
        n = reset_voice_segments(conn)
        assert n == 3
        assert conn.execute("SELECT COUNT(*) FROM file_voice_segments").fetchone()[0] == 0


class TestGetVoiceSegmentsForExport:
    def test_joins_path(self):
        from src.db.corpus import get_voice_segments_for_export, upsert_voice_segment
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path, file_type) VALUES (1, '/audio/clip.wav', 'audio')")
        upsert_voice_segment(conn, 1, 0, 1000, 5000, "SPEAKER_00", None, None, 3, 0.88)
        rows = get_voice_segments_for_export(conn)
        assert len(rows) == 1
        assert rows[0].file_path == "/audio/clip.wav"
        assert rows[0].person_id == 3
        assert rows[0].score == pytest.approx(0.88, abs=1e-5)


class TestUpsertVoiceSpeakerCluster:
    def test_insert_returns_new_id(self):
        from src.db.corpus import upsert_voice_speaker_cluster
        conn = _make_corpus_db()
        cid = upsert_voice_speaker_cluster(conn, None, _blob(), 1, 0.0)
        assert isinstance(cid, int) and cid > 0

    def test_update_preserves_id(self):
        from src.db.corpus import upsert_voice_speaker_cluster
        conn = _make_corpus_db()
        cid = upsert_voice_speaker_cluster(conn, None, _blob(seed=0), 1, 0.0)
        cid2 = upsert_voice_speaker_cluster(conn, cid, _blob(seed=1), 2, 0.1)
        assert cid == cid2
        row = conn.execute("SELECT member_count FROM voice_speaker_clusters WHERE id = ?", (cid,)).fetchone()
        assert row["member_count"] == 2

    def test_multiple_clusters(self):
        from src.db.corpus import upsert_voice_speaker_cluster
        conn = _make_corpus_db()
        id1 = upsert_voice_speaker_cluster(conn, None, _blob(seed=0), 1, None)
        id2 = upsert_voice_speaker_cluster(conn, None, _blob(seed=1), 1, None)
        assert id1 != id2


class TestGetVoiceSpeakerClusters:
    def test_returns_ordered_by_id(self):
        from src.db.corpus import get_voice_speaker_clusters, upsert_voice_speaker_cluster
        conn = _make_corpus_db()
        upsert_voice_speaker_cluster(conn, None, _blob(seed=0), 1, None)
        upsert_voice_speaker_cluster(conn, None, _blob(seed=1), 2, None)
        rows = get_voice_speaker_clusters(conn)
        assert rows[0]["id"] < rows[1]["id"]
        assert rows[0]["member_count"] == 1


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestDiarizationHealthCheck:
    def test_diarization_model_check_in_results(self, tmp_path):
        from src.config import Config
        from src.health import run_checks
        checks = run_checks(Config(), None, None, tmp_path)
        ids = [c.id for c in checks]
        assert "diarization_model" in ids

    def test_diarization_model_severity_warning(self, tmp_path):
        from src.config import Config
        from src.health import run_checks
        checks = run_checks(Config(), None, None, tmp_path)
        dc = next(c for c in checks if c.id == "diarization_model")
        assert dc.severity == "warning"

    def test_total_check_count_is_24(self, tmp_path):
        from src.config import Config
        from src.health import run_checks
        assert len(run_checks(Config(), None, None, tmp_path)) == 28
