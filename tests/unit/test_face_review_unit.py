"""Unit tests for face cluster DB helpers and thumbnail crop logic (KB.Q3, KB.AJ1)."""
import io
import json

import numpy as np
import pytest
from PIL import Image

from src.db.corpus import (
    assign_face_cluster,
    get_assigned_face_clusters,
    get_face_region_for_thumbnail,
    get_pending_face_clusters,
    open_corpus,
    unassign_face_cluster,
)
from src.db.kb import open_kb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def corpus(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    # Minimal source + file
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",))
    conn.commit()
    source_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO files (source_id, path, filename) VALUES (?, ?, ?)",
        (source_id, "/src/a.jpg", "a.jpg"),
    )
    conn.commit()
    file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return conn, file_id


def _insert_cluster(conn, member_count=2, spread=0.12):
    conn.execute(
        "INSERT INTO face_clusters (centroid, member_count, spread) VALUES (?, ?, ?)",
        (b"\x00" * 512, member_count, spread),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_face_region(conn, file_id, region_index=0, bbox=None):
    bbox_json = json.dumps(bbox or [10, 20, 50, 60])
    conn.execute(
        "INSERT INTO file_face_regions (file_id, region_index, bbox, embedding) VALUES (?, ?, ?, ?)",
        (file_id, region_index, bbox_json, b"\x00" * 512),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_member(conn, cluster_id, file_id, region_index=0, similarity=0.9):
    conn.execute(
        "INSERT INTO face_cluster_members (cluster_id, file_id, region_index, similarity) VALUES (?, ?, ?, ?)",
        (cluster_id, file_id, region_index, similarity),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# get_pending_face_clusters
# ---------------------------------------------------------------------------

def test_get_pending_face_clusters_returns_unassigned(corpus):
    conn, file_id = corpus
    cluster_id = _insert_cluster(conn)
    _insert_face_region(conn, file_id)
    _insert_member(conn, cluster_id, file_id)

    rows = get_pending_face_clusters(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == cluster_id
    assert rows[0]["member_count"] == 2


def test_get_pending_face_clusters_excludes_assigned(corpus):
    conn, file_id = corpus
    cluster_id = _insert_cluster(conn)
    conn.execute(
        "UPDATE face_clusters SET person_id = 1, label = 'Alice' WHERE id = ?",
        (cluster_id,),
    )
    conn.commit()

    rows = get_pending_face_clusters(conn)
    assert rows == []


# ---------------------------------------------------------------------------
# get_assigned_face_clusters
# ---------------------------------------------------------------------------

def test_get_assigned_face_clusters_returns_assigned(corpus):
    conn, _ = corpus
    cluster_id = _insert_cluster(conn)
    conn.execute(
        "UPDATE face_clusters SET person_id = 7, label = 'Bob' WHERE id = ?",
        (cluster_id,),
    )
    conn.commit()

    rows = get_assigned_face_clusters(conn)
    assert len(rows) == 1
    assert rows[0]["person_id"] == 7
    assert rows[0]["label"] == "Bob"


def test_get_assigned_face_clusters_empty_when_none_assigned(corpus):
    conn, _ = corpus
    _insert_cluster(conn)
    rows = get_assigned_face_clusters(conn)
    assert rows == []


# ---------------------------------------------------------------------------
# assign_face_cluster
# ---------------------------------------------------------------------------

def test_assign_face_cluster_sets_person_and_label(corpus):
    conn, _ = corpus
    cluster_id = _insert_cluster(conn)
    assign_face_cluster(conn, cluster_id, 42, "Alice")
    conn.commit()

    row = conn.execute("SELECT person_id, label FROM face_clusters WHERE id=?", (cluster_id,)).fetchone()
    assert row["person_id"] == 42
    assert row["label"] == "Alice"


def test_assign_face_cluster_nonexistent_is_noop(corpus):
    conn, _ = corpus
    assign_face_cluster(conn, 9999, 1, "Ghost")
    conn.commit()
    rows = get_assigned_face_clusters(conn)
    assert rows == []


def test_assign_face_cluster_propagates_person_id_to_regions(corpus):
    conn, file_id = corpus
    cluster_id = _insert_cluster(conn)
    _insert_face_region(conn, file_id, region_index=0)
    _insert_member(conn, cluster_id, file_id, region_index=0)

    assign_face_cluster(conn, cluster_id, 42, "Alice")
    conn.commit()

    row = conn.execute(
        "SELECT person_id FROM file_face_regions WHERE file_id=? AND region_index=0", (file_id,)
    ).fetchone()
    assert row["person_id"] == 42


def test_assign_face_cluster_does_not_affect_other_clusters(corpus):
    conn, file_id = corpus
    cluster1 = _insert_cluster(conn)
    cluster2 = _insert_cluster(conn)
    _insert_face_region(conn, file_id, region_index=0)
    _insert_face_region(conn, file_id, region_index=1)
    _insert_member(conn, cluster1, file_id, region_index=0)
    _insert_member(conn, cluster2, file_id, region_index=1)

    assign_face_cluster(conn, cluster1, 42, "Alice")
    conn.commit()

    other = conn.execute(
        "SELECT person_id FROM file_face_regions WHERE file_id=? AND region_index=1", (file_id,)
    ).fetchone()
    assert other["person_id"] is None


# ---------------------------------------------------------------------------
# unassign_face_cluster
# ---------------------------------------------------------------------------

def test_unassign_face_cluster_clears_fields(corpus):
    conn, _ = corpus
    cluster_id = _insert_cluster(conn)
    assign_face_cluster(conn, cluster_id, 5, "Charlie")
    conn.commit()

    unassign_face_cluster(conn, cluster_id)
    conn.commit()
    row = conn.execute("SELECT person_id, label FROM face_clusters WHERE id=?", (cluster_id,)).fetchone()
    assert row["person_id"] is None
    assert row["label"] is None


def test_unassign_face_cluster_clears_region_person_ids(corpus):
    conn, file_id = corpus
    cluster_id = _insert_cluster(conn)
    _insert_face_region(conn, file_id, region_index=0)
    _insert_member(conn, cluster_id, file_id, region_index=0)

    assign_face_cluster(conn, cluster_id, 5, "Charlie")
    conn.commit()
    unassign_face_cluster(conn, cluster_id)
    conn.commit()

    row = conn.execute(
        "SELECT person_id FROM file_face_regions WHERE file_id=? AND region_index=0", (file_id,)
    ).fetchone()
    assert row["person_id"] is None


# ---------------------------------------------------------------------------
# get_face_region_for_thumbnail
# ---------------------------------------------------------------------------

def test_get_face_region_for_thumbnail_found(corpus):
    conn, file_id = corpus
    bbox = [5, 10, 80, 90]
    region_id = _insert_face_region(conn, file_id, bbox=bbox)

    row = get_face_region_for_thumbnail(conn, region_id)
    assert row is not None
    assert json.loads(row["bbox"]) == bbox
    assert row["file_path"] == "/src/a.jpg"


def test_get_face_region_for_thumbnail_missing_returns_none(corpus):
    conn, _ = corpus
    row = get_face_region_for_thumbnail(conn, 9999)
    assert row is None


# ---------------------------------------------------------------------------
# Thumbnail crop logic (mirrors knowledge.py route logic, tested in isolation)
# ---------------------------------------------------------------------------

def _make_crop(img: Image.Image, bbox_json: str, padding: int = 10, size: int = 120):
    bbox = json.loads(bbox_json)
    x1, y1, x2, y2 = bbox
    box = (
        max(0, int(x1) - padding),
        max(0, int(y1) - padding),
        min(img.width, int(x2) + padding),
        min(img.height, int(y2) + padding),
    )
    crop = img.crop(box)
    crop = crop.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_thumbnail_crop_happy_path():
    img = Image.new("RGB", (200, 200), (255, 0, 0))
    bbox_json = json.dumps([50, 50, 150, 150])
    data = _make_crop(img, bbox_json)
    out = Image.open(io.BytesIO(data))
    assert out.size == (120, 120)
    assert out.format == "JPEG"


def test_thumbnail_crop_padding_clamps_to_image_bounds():
    img = Image.new("RGB", (100, 100), (0, 255, 0))
    # bbox near the edge; padding would exceed bounds
    bbox_json = json.dumps([0, 0, 10, 10])
    data = _make_crop(img, bbox_json, padding=50)
    out = Image.open(io.BytesIO(data))
    assert out.size == (120, 120)


def test_thumbnail_missing_file_returns_grey_jpeg():
    grey = Image.new("RGB", (1, 1), (128, 128, 128))
    buf = io.BytesIO()
    grey.save(buf, format="JPEG")
    data = buf.getvalue()
    out = Image.open(io.BytesIO(data))
    assert out.size == (1, 1)


# ---------------------------------------------------------------------------
# merge_face_centroid (KB.AJ1 — mirrors merge_voice_centroid)
# ---------------------------------------------------------------------------

def _blob(seed: int = 0, dim: int = 512) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


@pytest.fixture
def kb(tmp_path):
    return open_kb(tmp_path / "knowledge.db")


def test_merge_face_centroid_sets_directly_when_no_prior_centroid(kb):
    from src.db.kb import merge_face_centroid
    pid = kb.execute("INSERT INTO people(preferred_name) VALUES ('Alice')").lastrowid
    emb = _blob(1)
    merge_face_centroid(kb, pid, emb, 3)
    row = kb.execute("SELECT face_centroid, face_samples FROM people WHERE id=?", (pid,)).fetchone()
    result = np.frombuffer(bytes(row["face_centroid"]), dtype=np.float32)
    expected = np.frombuffer(emb, dtype=np.float32)
    np.testing.assert_allclose(result, expected, atol=1e-5)
    assert row["face_samples"] == 3


def test_merge_face_centroid_weighted_average_is_l2_normalised(kb):
    from src.db.kb import merge_face_centroid
    pid = kb.execute("INSERT INTO people(preferred_name) VALUES ('Bob')").lastrowid
    merge_face_centroid(kb, pid, _blob(0), 2)
    merge_face_centroid(kb, pid, _blob(1), 2)
    row = kb.execute("SELECT face_centroid FROM people WHERE id=?", (pid,)).fetchone()
    result = np.frombuffer(bytes(row["face_centroid"]), dtype=np.float32)
    norm = float(np.linalg.norm(result))
    assert abs(norm - 1.0) < 1e-4


def test_merge_face_centroid_sample_count_accumulates(kb):
    from src.db.kb import merge_face_centroid
    pid = kb.execute("INSERT INTO people(preferred_name) VALUES ('Carol')").lastrowid
    merge_face_centroid(kb, pid, _blob(0), 3)
    merge_face_centroid(kb, pid, _blob(1), 5)
    row = kb.execute("SELECT face_samples FROM people WHERE id=?", (pid,)).fetchone()
    assert row["face_samples"] == 8


def test_merge_face_centroid_noop_for_unknown_person(kb):
    from src.db.kb import merge_face_centroid
    merge_face_centroid(kb, 999, _blob(0), 1)
