"""Integration tests for KB.AM1 — early-skip gating in STAGE_REQUIRES stages.

The gating check runs before any model config validation or corpus access,
so these tests don't need real ML models, mocked embeddings, or seeded
files — just a knowledge.db with the relevant domain disabled. This is also
what proves the gate actually precedes model loading: if it didn't, these
tests would fail with ModelLoadError/missing-file errors instead of
returning a clean "skipped" result.
"""
import threading

from src.config import Config
from src.db.kb import open_kb, set_knowledge_category_enabled
from src.pipeline.progress import NullProgressReporter


def _disabled_kb(tmp_path, category: str):
    kb_path = tmp_path / "knowledge.db"
    conn = open_kb(kb_path)
    set_knowledge_category_enabled(conn, category, False)
    conn.close()
    return kb_path


def _enabled_kb(tmp_path):
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    return kb_path


def test_face_skipped_when_people_disabled(tmp_path):
    from src.stages.face import run_face

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "people")

    result = run_face(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    assert result["skipped"] is True
    assert "people" in result["skipped_reason"]


def test_face_meta_skipped_when_people_disabled(tmp_path):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "people")

    result = run_face_meta(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    assert result["skipped"] is True


def test_voice_skipped_when_people_disabled(tmp_path):
    from src.stages.voice import run_voice

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "people")

    result = run_voice(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    assert result["skipped"] is True


def test_voice_diarize_skipped_when_people_disabled(tmp_path):
    from src.stages.voice import run_voice_diarize

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "people")

    result = run_voice_diarize(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    assert result["skipped"] is True


def test_attribute_speakers_skipped_when_people_disabled(tmp_path):
    from src.stages.attribute_speakers import run_attribute_speakers

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "people")

    result = run_attribute_speakers(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    assert result["skipped"] is True


def test_geolocate_skipped_when_places_disabled(tmp_path):
    from src.stages.geolocate import run_geolocate

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "places")

    # Should not raise even though corpus.db doesn't exist and no region
    # data is on disk — the skip happens before either is touched.
    run_geolocate(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())


def test_geo_meta_skipped_when_places_disabled(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "places")

    result = run_geo_meta(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    assert result["skipped"] is True


def test_temporal_skipped_when_dates_disabled(tmp_path):
    from src.stages.temporal import run_temporal

    corpus_path = tmp_path / "corpus.db"
    kb_path = _disabled_kb(tmp_path, "dates")

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())


# ---------------------------------------------------------------------------
# Sanity check: enabling the domain does NOT short-circuit — face.py should
# still reach its normal config validation instead of silently no-oping.
# ---------------------------------------------------------------------------

def test_face_not_skipped_when_people_enabled_still_validates_config(tmp_path):
    from src.stages.face import ModelLoadError, run_face

    corpus_path = tmp_path / "corpus.db"
    kb_path = _enabled_kb(tmp_path)

    try:
        run_face(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
        assert False, "expected ModelLoadError for unconfigured face_detection_model"
    except ModelLoadError:
        pass
