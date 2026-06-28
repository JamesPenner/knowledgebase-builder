"""Integration tests for KB.X1 Face Metadata Stage."""
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.db.corpus import (
    get_files_without_face_regions,
    open_corpus,
    upsert_face_region,
)
from src.db.kb import open_kb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embedding(seed: int = 0, dim: int = 512) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def _make_config(tmp_path, *, quality_gate=False):
    from src.config import Config
    emb_model = tmp_path / "emb.onnx"
    emb_model.write_bytes(b"fake")
    det_model = tmp_path / "det.onnx"
    det_model.write_bytes(b"fake")
    return Config(
        face_embedding_model=str(emb_model),
        face_detection_model=str(det_model),
        face_meta_quality_gate=quality_gate,
        face_meta_quality_threshold=0.3,
        face_meta_min_centroid_similarity=0.0,  # never skip in tests
        face_meta_recalibrate_min_samples=100,  # disable auto-recalibrate in most tests
    )


def _add_source(corpus_conn):
    row = corpus_conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
    if row:
        return row["id"]
    return corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid


def _ingest_image(corpus_conn, file_id: int, img_path: Path) -> None:
    src_id = _add_source(corpus_conn)
    corpus_conn.execute(
        "INSERT OR IGNORE INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
        "VALUES (?, ?, ?, ?, '.jpg', 'images', 1000, 0.0)",
        (file_id, src_id, str(img_path), img_path.name),
    )
    corpus_conn.commit()


def _xmp_single_face(name="Alice", cx=0.5, cy=0.5, w=0.2, h=0.2):
    return {
        "RegionName": [name],
        "RegionType": ["Face"],
        "RegionAreaUnit": "normalized",
        "RegionAreaX": [cx],
        "RegionAreaY": [cy],
        "RegionAreaW": [w],
        "RegionAreaH": [h],
    }


def _null_progress():
    from src.pipeline.progress import NullProgressReporter
    return NullProgressReporter()


def _no_cancel():
    from src.pipeline.cancel import make_cancel_event
    return make_cancel_event()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_face_meta_creates_person_not_in_kb(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _ingest_image(corpus_conn, 1, sample_image)
    corpus_conn.close()
    kb_conn.close()

    xmp = _xmp_single_face("NewPerson")
    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", return_value=_fake_embedding(0)),
    ):
        result = run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    assert result["people_created"] == 1
    assert result["regions_found"] == 1

    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT preferred_name FROM people WHERE preferred_name = 'NewPerson'").fetchone()
    kb_conn2.close()
    assert row is not None


def test_run_face_meta_matches_existing_person(tmp_path, sample_image):
    from src.db.kb import add_person_name, upsert_person
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _ingest_image(corpus_conn, 1, sample_image)
    pid = upsert_person(kb_conn, preferred_name="Alice Smith")
    add_person_name(kb_conn, pid, "Alice Smith")
    corpus_conn.close()
    kb_conn.close()

    xmp = _xmp_single_face("Alice Smith")
    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", return_value=_fake_embedding(0)),
    ):
        result = run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    assert result["people_matched"] == 1
    assert result["people_created"] == 0

    kb_conn2 = open_kb(kb_path)
    count = kb_conn2.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    kb_conn2.close()
    assert count == 1  # no duplicate created


def test_run_face_meta_writes_face_region_with_metadata_source(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _ingest_image(corpus_conn, 1, sample_image)
    corpus_conn.close()

    xmp = _xmp_single_face("Bob")
    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", return_value=_fake_embedding(1)),
    ):
        run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    corpus_conn2 = open_corpus(corpus_path)
    rows = corpus_conn2.execute("SELECT source FROM file_face_regions").fetchall()
    corpus_conn2.close()
    assert len(rows) == 1
    assert rows[0]["source"] == "metadata"


def test_run_face_meta_updates_person_centroid(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _ingest_image(corpus_conn, 1, sample_image)
    corpus_conn.close()

    xmp = _xmp_single_face("Carol")
    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", return_value=_fake_embedding(2)),
    ):
        run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT face_centroid, face_samples FROM people WHERE preferred_name='Carol'").fetchone()
    kb_conn2.close()
    assert row is not None
    assert row["face_centroid"] is not None
    assert row["face_samples"] == 1


def test_run_face_meta_resumes_skips_processed_files(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _ingest_image(corpus_conn, 1, sample_image)
    corpus_conn.close()

    xmp = _xmp_single_face("Dave")
    config = _make_config(tmp_path)
    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", return_value=_fake_embedding(3)),
    ):
        result1 = run_face_meta(corpus_path, kb_path, config, _null_progress(), _no_cancel())
        result2 = run_face_meta(corpus_path, kb_path, config, _null_progress(), _no_cancel())

    assert result1["files_processed"] >= 1
    assert result2["files_processed"] == 0


def test_face_ml_pending_still_sees_meta_processed_files(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _ingest_image(corpus_conn, 1, sample_image)
    corpus_conn.close()

    xmp = _xmp_single_face("Eve")
    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", return_value=_fake_embedding(4)),
    ):
        run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    corpus_conn2 = open_corpus(corpus_path)
    pending_ml = get_files_without_face_regions(corpus_conn2)
    corpus_conn2.close()
    assert len(pending_ml) == 1  # ML stage still sees the file as pending


def test_run_face_meta_force_resets_metadata_regions_only(tmp_path, sample_image):
    from src.db.corpus import reset_meta_face_regions
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _ingest_image(corpus_conn, 1, sample_image)

    # Manually insert an ML region
    upsert_face_region(corpus_conn, 1, 0, json.dumps([0, 0, 10, 10]), _fake_embedding(99), None, None, source="ml")
    corpus_conn.commit()
    corpus_conn.close()

    # Run face_meta to add a metadata region
    xmp = _xmp_single_face("Frank")
    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", return_value=_fake_embedding(5)),
    ):
        run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    # Reset only metadata regions
    corpus_conn2 = open_corpus(corpus_path)
    reset_meta_face_regions(corpus_conn2)
    ml_rows = corpus_conn2.execute("SELECT * FROM file_face_regions WHERE source='ml'").fetchall()
    meta_rows = corpus_conn2.execute("SELECT * FROM file_face_regions WHERE source='metadata'").fetchall()
    corpus_conn2.close()
    assert len(ml_rows) == 1
    assert len(meta_rows) == 0


def test_run_face_meta_deduplicates_mwg_rs_and_acdsee(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _ingest_image(corpus_conn, 1, sample_image)
    corpus_conn.close()

    # Both MWG-RS and ACDSee describe 3 people — should deduplicate to 3 regions
    xmp = {
        "RegionName": ["Alice", "Bob", "Carol"],
        "RegionType": ["Face", "Face", "Face"],
        "RegionAreaUnit": "normalized",
        "RegionAreaX": [0.2, 0.5, 0.8],
        "RegionAreaY": [0.3, 0.3, 0.3],
        "RegionAreaW": [0.1, 0.1, 0.1],
        "RegionAreaH": [0.1, 0.1, 0.1],
        "ACDSeeRegionName": ["Alice", "Bob", "Carol"],
        "ACDSeeRegionType": ["Face", "Face", "Face"],
        "ACDSeeRegionDLYAreaX": [0.2, 0.5, 0.8],
        "ACDSeeRegionDLYAreaY": [0.3, 0.3, 0.3],
        "ACDSeeRegionDLYAreaW": [0.1, 0.1, 0.1],
        "ACDSeeRegionDLYAreaH": [0.1, 0.1, 0.1],
    }
    call_count = [0]
    def _embed(*a, **kw):
        emb = _fake_embedding(call_count[0])
        call_count[0] += 1
        return emb

    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", side_effect=_embed),
    ):
        result = run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    assert result["regions_found"] == 3  # not 6


def test_run_face_meta_skips_file_without_face_metadata(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _ingest_image(corpus_conn, 1, sample_image)
    corpus_conn.close()

    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value={}),  # no XMP data
        patch("src.stages.face.embed_face", return_value=_fake_embedding(0)),
    ):
        result = run_face_meta(corpus_path, kb_path, _make_config(tmp_path), _null_progress(), _no_cancel())

    assert result["regions_found"] == 0

    corpus_conn2 = open_corpus(corpus_path)
    rows = corpus_conn2.execute("SELECT * FROM file_face_regions WHERE source='metadata'").fetchall()
    corpus_conn2.close()
    assert len(rows) == 0


def test_migration_0021_face_region_has_source_column(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    cols = [r[1] for r in corpus_conn.execute("PRAGMA table_info(file_face_regions)").fetchall()]
    corpus_conn.close()
    assert "source" in cols


def test_migration_0021_source_defaults_to_ml(tmp_path, sample_image):
    """Verify upsert_face_region with no source param writes source='ml'."""
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    src_id = corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid
    corpus_conn.execute(
        "INSERT INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, ?, ?, 'x.jpg', '.jpg', 'images', 100, 0.0)",
        (src_id, str(sample_image)),
    )
    upsert_face_region(corpus_conn, 1, 0, None, _fake_embedding(0), None, None)
    corpus_conn.commit()
    row = corpus_conn.execute("SELECT source FROM file_face_regions WHERE file_id=1").fetchone()
    corpus_conn.close()
    assert row["source"] == "ml"
