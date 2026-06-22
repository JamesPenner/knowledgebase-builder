"""Integration tests for KB.Q1 — knowledge locations API and UI routes."""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import get_entity_table_rows, open_kb


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


def _seed_cluster(
    conn: sqlite3.Connection,
    label: str,
    lat: float = 55.95,
    lon: float = -3.19,
    eps_km: float = 1.0,
    file_count: int = 2,
) -> int:
    cur = conn.execute(
        "INSERT INTO gps_clusters (label, centroid_lat, centroid_lon, file_count, eps_km, min_samples)"
        " VALUES (?, ?, ?, ?, ?, 2)",
        (label, lat, lon, file_count, eps_km),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# API: GET /api/knowledge/locations/clusters
# ---------------------------------------------------------------------------

def test_api_list_clusters_with_data(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    _seed_cluster(conn, "Edinburgh")
    _seed_cluster(conn, "Glasgow", lat=55.86, lon=-4.25)
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/locations/clusters", params={"kb": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["clusters"]) == 2
    labels = {c["label"] for c in data["clusters"]}
    assert labels == {"Edinburgh", "Glasgow"}


def test_api_list_clusters_empty(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/locations/clusters", params={"kb": "test"})
    assert resp.status_code == 200
    assert resp.json() == {"clusters": []}


# ---------------------------------------------------------------------------
# API: POST rename
# ---------------------------------------------------------------------------

def test_api_rename_success(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cid = _seed_cluster(conn, "Old Name")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        f"/api/knowledge/locations/clusters/{cid}/rename",
        params={"kb": "test"},
        json={"label": "New Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "New Name"
    assert resp.headers.get("HX-Trigger") == '{"clustersChanged": null}'


def test_api_rename_bad_id(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/knowledge/locations/clusters/9999/rename",
        params={"kb": "test"},
        json={"label": "Ghost"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API: POST promote
# ---------------------------------------------------------------------------

def test_api_promote_fresh(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cid = _seed_cluster(conn, "Edinburgh")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        f"/api/knowledge/locations/clusters/{cid}/promote",
        params={"kb": "test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "promoted"
    assert data["entity_table"] == "entity_gps_cluster_locations"

    kb_conn = open_kb(kb_path)
    rows = get_entity_table_rows(kb_conn, "gps_cluster_locations")
    kb_conn.close()
    assert len(rows) == 1
    assert rows[0]["location"] == "Edinburgh"


def test_api_promote_idempotent(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cid = _seed_cluster(conn, "Edinburgh")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    client.post(f"/api/knowledge/locations/clusters/{cid}/promote", params={"kb": "test"})
    resp = client.post(f"/api/knowledge/locations/clusters/{cid}/promote", params={"kb": "test"})
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    rows = get_entity_table_rows(kb_conn, "gps_cluster_locations")
    kb_conn.close()
    assert len(rows) == 1


def test_api_promote_missing_cluster(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/knowledge/locations/clusters/9999/promote",
        params={"kb": "test"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# UI: page and partial routes
# ---------------------------------------------------------------------------

def test_page_loads(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/locations", params={"kb": "test"})
    assert resp.status_code == 200
    assert "map" in resp.text


def test_cluster_list_partial_with_clusters(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    _seed_cluster(conn, "Edinburgh")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/locations/partials/cluster-list", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Edinburgh" in resp.text


def test_rename_round_trip(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cid = _seed_cluster(conn, "Original")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    client.post(
        f"/api/knowledge/locations/clusters/{cid}/rename",
        params={"kb": "test"},
        json={"label": "Renamed"},
    )
    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT label FROM gps_clusters WHERE id=?", (cid,)).fetchone()
    conn.close()
    assert row["label"] == "Renamed"


def test_promote_creates_entity_row(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cid = _seed_cluster(conn, "Glasgow", lat=55.86, lon=-4.25)
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    client.post(f"/api/knowledge/locations/clusters/{cid}/promote", params={"kb": "test"})

    kb_conn = open_kb(kb_path)
    rows = get_entity_table_rows(kb_conn, "gps_cluster_locations")
    kb_conn.close()
    assert any(r["location"] == "Glasgow" for r in rows)


def test_promote_idempotent(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cid = _seed_cluster(conn, "Glasgow")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    for _ in range(3):
        resp = client.post(
            f"/api/knowledge/locations/clusters/{cid}/promote", params={"kb": "test"}
        )
        assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    rows = get_entity_table_rows(kb_conn, "gps_cluster_locations")
    kb_conn.close()
    assert len(rows) == 1


def test_no_gps_files_empty_state(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/locations/partials/cluster-list", params={"kb": "test"})
    assert resp.status_code == 200
    assert "enrich geolocate cluster" in resp.text


def test_cluster_list_partial_shows_promoted_badge(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    cid = _seed_cluster(conn, "Edinburgh")
    conn.close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    client.post(f"/api/knowledge/locations/clusters/{cid}/promote", params={"kb": "test"})

    resp = client.get("/knowledge/locations/partials/cluster-list", params={"kb": "test"})
    assert resp.status_code == 200
    assert "promoted" in resp.text


# ---------------------------------------------------------------------------
# Nav
# ---------------------------------------------------------------------------

def test_nav_has_knowledge_section(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/locations", params={"kb": "test"})
    assert resp.status_code == 200
    assert "nav-label" in resp.text
    assert "Knowledge" in resp.text
    assert "/knowledge/locations" in resp.text
