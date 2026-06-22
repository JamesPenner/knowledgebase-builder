"""Integration tests for KB.Q3 — face cluster review page and API routes."""
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import open_kb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(corpus_path: Path, kb_path: Path) -> TestClient:
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


def _seed_source(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES ('/src', 'all', 1)"
    )
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE path='/src'").fetchone()["id"]


def _seed_file(conn: sqlite3.Connection, src_id: int, path: str) -> int:
    conn.execute(
        "INSERT INTO files (source_id, path, filename) VALUES (?, ?, 'f.jpg')",
        (src_id, path),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def _seed_cluster(conn: sqlite3.Connection, member_count: int = 2, person_id=None, label=None) -> int:
    conn.execute(
        "INSERT INTO face_clusters (centroid, member_count, spread, person_id, label) VALUES (?, ?, ?, ?, ?)",
        (b"\x00" * 512, member_count, 0.1, person_id, label),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_face_region(conn: sqlite3.Connection, file_id: int, region_index: int = 0, bbox=None) -> int:
    bbox_json = json.dumps(bbox or [10, 20, 50, 60])
    conn.execute(
        "INSERT INTO file_face_regions (file_id, region_index, bbox, embedding) VALUES (?, ?, ?, ?)",
        (file_id, region_index, bbox_json, b"\x00" * 512),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_member(conn: sqlite3.Connection, cluster_id: int, file_id: int, region_index: int = 0, similarity: float = 0.9):
    conn.execute(
        "INSERT INTO face_cluster_members (cluster_id, file_id, region_index, similarity) VALUES (?, ?, ?, ?)",
        (cluster_id, file_id, region_index, similarity),
    )
    conn.commit()


def _seed_person(kb_conn, name: str) -> int:
    kb_conn.execute(
        "INSERT INTO people (preferred_name) VALUES (?) ON CONFLICT DO NOTHING", (name,)
    )
    kb_conn.commit()
    return kb_conn.execute("SELECT id FROM people WHERE preferred_name=?", (name,)).fetchone()["id"]


def _make_test_image(path: Path, width: int = 200, height: int = 200):
    img = Image.new("RGB", (width, height), (100, 150, 200))
    img.save(str(path), format="JPEG")


# ---------------------------------------------------------------------------
# Page load
# ---------------------------------------------------------------------------

def test_face_review_page_loads(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/people/faces", params={"kb": "test"})
    assert resp.status_code == 200
    assert b"Face Review" in resp.content


# ---------------------------------------------------------------------------
# API: GET /api/knowledge/people/faces/clusters
# ---------------------------------------------------------------------------

def test_api_list_clusters_pending_and_assigned(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    file_id = _seed_file(conn, src_id, "/src/a.jpg")
    pending_id = _seed_cluster(conn)
    _seed_face_region(conn, file_id)
    _seed_member(conn, pending_id, file_id)
    _seed_cluster(conn, person_id=5, label="Alice")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/people/faces/clusters", params={"kb": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pending"]) == 1
    assert len(data["assigned"]) == 1
    assert data["assigned"][0]["label"] == "Alice"


def test_api_list_clusters_empty_corpus(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/people/faces/clusters", params={"kb": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] == []
    assert data["assigned"] == []


# ---------------------------------------------------------------------------
# Thumbnail route
# ---------------------------------------------------------------------------

def test_thumbnail_returns_jpeg(tmp_path):
    img_path = tmp_path / "photo.jpg"
    _make_test_image(img_path, 200, 200)

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    file_id = _seed_file(conn, src_id, str(img_path))
    region_id = _seed_face_region(conn, file_id, bbox=[50, 50, 150, 150])
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get(f"/api/knowledge/corpus/face-thumbnail/{region_id}", params={"kb": "test"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(resp.content))
    assert img.size == (120, 120)


def test_thumbnail_missing_face_region_returns_grey(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/corpus/face-thumbnail/9999", params={"kb": "test"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(resp.content))
    assert img.size == (1, 1)


def test_thumbnail_crops_correct_region(tmp_path):
    img_path = tmp_path / "photo.jpg"
    # Create image with distinct colour in the face region
    img = Image.new("RGB", (200, 200), (50, 50, 50))
    # Paint a bright region at the bbox location
    for x in range(60, 140):
        for y in range(60, 140):
            img.putpixel((x, y), (255, 200, 100))
    img.save(str(img_path), format="JPEG")

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    file_id = _seed_file(conn, src_id, str(img_path))
    region_id = _seed_face_region(conn, file_id, bbox=[70, 70, 130, 130])
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get(f"/api/knowledge/corpus/face-thumbnail/{region_id}", params={"kb": "test"})
    assert resp.status_code == 200
    out = Image.open(io.BytesIO(resp.content)).convert("RGB")
    # Centre pixel should be warm (from the bright region), not dark background
    cx, cy = out.size[0] // 2, out.size[1] // 2
    r, g, b = out.getpixel((cx, cy))
    assert r > 150


# ---------------------------------------------------------------------------
# Decide: assign to existing person
# ---------------------------------------------------------------------------

def test_decide_assign_existing_person(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cluster_id = _seed_cluster(conn)
    conn.close()
    kb_conn = open_kb(kb_path)
    person_id = _seed_person(kb_conn, "Alice")
    kb_conn.close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/review/faces/decide",
        params={"kb": "test"},
        data={"cluster_id": cluster_id, "action": "assign", "person_id": str(person_id)},
    )
    assert resp.status_code == 200
    assert "HX-Trigger" in resp.headers

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT person_id, label FROM face_clusters WHERE id=?", (cluster_id,)).fetchone()
    assert row["person_id"] == person_id
    assert row["label"] == "Alice"


def test_decide_assign_new_name_creates_person(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cluster_id = _seed_cluster(conn)
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/review/faces/decide",
        params={"kb": "test"},
        data={"cluster_id": cluster_id, "action": "assign", "new_name": "Bob"},
    )
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT id FROM people WHERE preferred_name='Bob'").fetchone()
    assert row is not None
    pid = row["id"]
    kb_conn.close()

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT person_id, label FROM face_clusters WHERE id=?", (cluster_id,)).fetchone()
    assert row["person_id"] == pid
    assert row["label"] == "Bob"


# ---------------------------------------------------------------------------
# Decide: unassign
# ---------------------------------------------------------------------------

def test_decide_unassign_clears_person(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cluster_id = _seed_cluster(conn, person_id=3, label="Charlie")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/review/faces/decide",
        params={"kb": "test"},
        data={"cluster_id": cluster_id, "action": "unassign"},
    )
    assert resp.status_code == 200

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT person_id, label FROM face_clusters WHERE id=?", (cluster_id,)).fetchone()
    assert row["person_id"] is None
    assert row["label"] is None


# ---------------------------------------------------------------------------
# HTMX partial routes
# ---------------------------------------------------------------------------

def test_partial_queue_returns_html(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/people/faces/partials/queue", params={"kb": "test"})
    assert resp.status_code == 200
    assert b"unassigned" in resp.content.lower() or b"empty" in resp.content.lower()


def test_partial_assigned_returns_html(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/people/faces/partials/assigned", params={"kb": "test"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
