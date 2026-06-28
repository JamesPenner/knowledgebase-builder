"""Integration tests for KB.X1 face centroid recalibration."""
import json
from unittest.mock import patch

import numpy as np

from src.db.corpus import open_corpus, upsert_face_region
from src.db.kb import (
    get_face_embeddings_for_person,
    open_kb,
    update_face_centroid,
    update_face_centroid_with_spread,
    upsert_person,
)
from src.stages.face import compute_trimmed_centroid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand_embedding(seed: int = 0, dim: int = 512) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def _seed_corpus_file(corpus_conn, src_id: int, file_id: int, path: str) -> None:
    corpus_conn.execute(
        "INSERT OR IGNORE INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, ?, ?, 'x.jpg', '.jpg', 'images', 100, 0.0)",
        (file_id, src_id, path),
    )
    corpus_conn.commit()


def _add_source(corpus_conn) -> int:
    return corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_recalibrate_updates_centroid_and_spread(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")

    src_id = _add_source(corpus_conn)
    _seed_corpus_file(corpus_conn, src_id, 1, "/src/img.jpg")

    pid = upsert_person(kb_conn, preferred_name="Alice")

    # Seed 11 embeddings (10 tight + 1 outlier) — 10% trim excludes 1
    base = np.zeros(512, dtype=np.float32)
    base[0] = 1.0
    embeddings = []
    for i in range(10):
        v = (base + np.random.default_rng(i).standard_normal(512) * 0.05).astype(np.float32)
        v = v / np.linalg.norm(v)
        emb = v.tobytes()
        embeddings.append(emb)
        upsert_face_region(corpus_conn, 1, i, json.dumps([0, 0, 10, 10]), emb, pid, None, source="metadata")

    # Outlier (opposite direction)
    outlier = np.zeros(512, dtype=np.float32)
    outlier[0] = -1.0
    outlier_bytes = outlier.tobytes()
    embeddings.append(outlier_bytes)
    upsert_face_region(corpus_conn, 1, 10, json.dumps([0, 0, 10, 10]), outlier_bytes, pid, None, source="metadata")
    corpus_conn.commit()

    # Seed initial centroid
    initial_blob, count = embeddings[0], 1
    update_face_centroid(kb_conn, pid, initial_blob, count)
    kb_conn.commit()

    # Run recalibration
    all_embeddings = get_face_embeddings_for_person(kb_conn, corpus_conn, pid)
    assert len(all_embeddings) == 11
    result = compute_trimmed_centroid(all_embeddings)
    assert result is not None
    centroid_blob, retained, spread = result
    update_face_centroid_with_spread(kb_conn, pid, centroid_blob, retained, spread)
    kb_conn.commit()

    row = kb_conn.execute("SELECT face_centroid, face_samples, face_centroid_spread FROM people WHERE id=?", (pid,)).fetchone()
    kb_conn.close()
    corpus_conn.close()

    assert row["face_centroid"] is not None
    assert row["face_centroid_spread"] is not None
    assert row["face_centroid_spread"] < 0.3


def test_recalibrate_skips_person_below_min_samples(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")

    src_id = _add_source(corpus_conn)
    _seed_corpus_file(corpus_conn, src_id, 1, "/src/img.jpg")

    pid = upsert_person(kb_conn, preferred_name="Bob")
    emb = _rand_embedding(0)
    update_face_centroid(kb_conn, pid, emb, 1)
    upsert_face_region(corpus_conn, 1, 0, None, emb, pid, None, source="metadata")
    corpus_conn.commit()
    kb_conn.commit()

    # Only 1 embedding; min_samples = 5
    all_embeddings = get_face_embeddings_for_person(kb_conn, corpus_conn, pid)
    assert len(all_embeddings) == 1

    # With min_samples=5, should not recalibrate (only 1 embedding)
    min_samples = 5
    if len(all_embeddings) < min_samples:
        pass  # skip recalibration
    else:
        result = compute_trimmed_centroid(all_embeddings)
        if result:
            update_face_centroid_with_spread(kb_conn, pid, result[0], result[1], result[2])
            kb_conn.commit()

    row_after = kb_conn.execute("SELECT face_centroid_spread FROM people WHERE id=?", (pid,)).fetchone()
    kb_conn.close()
    corpus_conn.close()
    # Spread should still be NULL (no recalibration occurred)
    assert row_after["face_centroid_spread"] is None


def test_recalibrate_idempotent(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")

    src_id = _add_source(corpus_conn)
    _seed_corpus_file(corpus_conn, src_id, 1, "/src/img.jpg")

    pid = upsert_person(kb_conn, preferred_name="Carol")
    base = np.zeros(512, dtype=np.float32)
    base[0] = 1.0
    embeddings = []
    for i in range(6):
        v = (base + np.random.default_rng(i).standard_normal(512) * 0.05).astype(np.float32)
        v = v / np.linalg.norm(v)
        emb = v.tobytes()
        embeddings.append(emb)
        upsert_face_region(corpus_conn, 1, i, None, emb, pid, None, source="metadata")
    corpus_conn.commit()
    update_face_centroid(kb_conn, pid, embeddings[0], 1)
    kb_conn.commit()

    all_embs = get_face_embeddings_for_person(kb_conn, corpus_conn, pid)
    result1 = compute_trimmed_centroid(all_embs)
    assert result1 is not None
    update_face_centroid_with_spread(kb_conn, pid, result1[0], result1[1], result1[2])
    kb_conn.commit()

    result2 = compute_trimmed_centroid(all_embs)
    assert result2 is not None
    update_face_centroid_with_spread(kb_conn, pid, result2[0], result2[1], result2[2])
    kb_conn.commit()

    kb_conn.close()
    corpus_conn.close()

    assert abs(result1[2] - result2[2]) < 1e-4


def test_face_meta_auto_recalibrates_when_threshold_reached(tmp_path, sample_image):
    from src.stages.face_meta import run_face_meta
    from src.config import Config

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    # Create 6 images, each with XMP tagging "David"
    images = []
    from PIL import Image
    for i in range(6):
        p = tmp_path / f"face_{i}.jpg"
        Image.new("RGB", (64, 64), color=(i * 30, 100, 200)).save(p, "JPEG")
        images.append(p)

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    src_id = corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid
    for i, img in enumerate(images):
        corpus_conn.execute(
            "INSERT INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime)"
            " VALUES (?, ?, ?, ?, '.jpg', 'images', 100, 0.0)",
            (i + 1, src_id, str(img), img.name),
        )
    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    xmp = _xmp_with_david()
    # Use tight-cluster embeddings around a base vector so similarity guard passes
    base = np.zeros(512, dtype=np.float32)
    base[0] = 1.0
    emb_counter = [0]
    def _embed(*a, **kw):
        v = (base + np.random.default_rng(emb_counter[0]).standard_normal(512) * 0.05).astype(np.float32)
        v = v / np.linalg.norm(v)
        emb_counter[0] += 1
        return v.tobytes()

    config = Config(
        face_embedding_model=str(tmp_path / "emb.onnx"),
        face_meta_recalibrate_min_samples=5,
        face_meta_min_centroid_similarity=0.0,
    )
    (tmp_path / "emb.onnx").write_bytes(b"fake")

    with (
        patch("src.stages.face_meta._read_xmp_exif", return_value=xmp),
        patch("src.stages.face.embed_face", side_effect=_embed),
    ):
        result = run_face_meta(corpus_path, kb_path, config, _null_progress(), _no_cancel())

    assert result["regions_found"] == 6

    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT face_centroid_spread FROM people WHERE preferred_name='David'").fetchone()
    kb_conn2.close()
    assert row is not None
    assert row["face_centroid_spread"] is not None


def _xmp_with_david():
    return {
        "RegionName": ["David"],
        "RegionType": ["Face"],
        "RegionAreaUnit": "normalized",
        "RegionAreaX": [0.5],
        "RegionAreaY": [0.5],
        "RegionAreaW": [0.2],
        "RegionAreaH": [0.2],
    }


def _null_progress():
    from src.pipeline.progress import NullProgressReporter
    return NullProgressReporter()


def _no_cancel():
    from src.pipeline.cancel import make_cancel_event
    return make_cancel_event()
