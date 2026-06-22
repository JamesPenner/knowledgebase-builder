"""Integration tests for KB.Q2 — location registry API and UI routes."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.kb import (
    create_entity_table,
    get_entity_table_entries,
    open_kb,
    register_entity_table,
    upsert_entity_row,
)


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


def _seed_registry_table(kb_path: Path, table_name: str = "cluster_locs") -> None:
    conn = open_kb(kb_path)
    create_entity_table(conn, table_name, ["location", "latitude", "longitude", "threshold_m"], "location")
    register_entity_table(
        conn,
        table_name=table_name,
        display_name="Test Locs",
        trigger_word="",
        trigger_aliases_json="[]",
        key_column="location",
        match_type="gps",
    )
    conn.close()


def _seed_row(kb_path: Path, table_name: str, location: str,
              lat: float | None = None, lon: float | None = None,
              threshold_m: float | None = None) -> None:
    conn = open_kb(kb_path)
    upsert_entity_row(conn, table_name, {
        "location": location,
        "latitude": str(lat) if lat is not None else "",
        "longitude": str(lon) if lon is not None else "",
        "threshold_m": str(threshold_m) if threshold_m is not None else "",
    })
    conn.commit()
    conn.close()


def _get_entry_id(kb_path: Path, table: str, location: str) -> int:
    conn = open_kb(kb_path)
    rows = get_entity_table_entries(conn, table)
    conn.close()
    return next(r["id"] for r in rows if r["location"] == location)


# ---------------------------------------------------------------------------
# API: GET /api/knowledge/locations/registry
# ---------------------------------------------------------------------------

def test_api_get_registry_with_data(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Edinburgh", lat=55.95, lon=-3.19, threshold_m=500)

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/locations/registry", params={"kb": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "tables" in data
    assert len(data["tables"]) == 1
    tbl = data["tables"][0]
    assert tbl["name"] == "entity_cluster_locs"
    assert tbl["match_type"] == "gps"
    assert len(tbl["entries"]) == 1
    entry = tbl["entries"][0]
    assert entry["location"] == "Edinburgh"
    assert entry["latitude"] == pytest.approx(55.95)
    assert "near_duplicates" in tbl


def test_api_get_registry_empty(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/locations/registry", params={"kb": "test"})
    assert resp.status_code == 200
    assert resp.json() == {"tables": []}


def test_api_put_success(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Old Name", lat=55.0, lon=-3.0)
    eid = _get_entry_id(kb_path, "entity_cluster_locs", "Old Name")

    client = _make_client(corpus_path, kb_path)
    resp = client.put(
        f"/api/knowledge/locations/registry/entity_cluster_locs/{eid}",
        params={"kb": "test"},
        json={"location": "New Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["location"] == "New Name"


def test_api_put_unknown_table_404(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.put(
        "/api/knowledge/locations/registry/entity_ghost/1",
        params={"kb": "test"},
        json={"location": "X"},
    )
    assert resp.status_code == 404


def test_api_delete_success(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "ToDelete")
    eid = _get_entry_id(kb_path, "entity_cluster_locs", "ToDelete")

    client = _make_client(corpus_path, kb_path)
    resp = client.delete(
        f"/api/knowledge/locations/registry/entity_cluster_locs/{eid}",
        params={"kb": "test"},
    )
    assert resp.status_code == 200
    assert resp.json() == {}

    conn = open_kb(kb_path)
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    conn.close()
    assert len(rows) == 0


def test_api_merge_success(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Keep", lat=None)
    _seed_row(kb_path, "cluster_locs", "Drop", lat=55.0, lon=-3.0)
    keep_id = _get_entry_id(kb_path, "entity_cluster_locs", "Keep")
    drop_id = _get_entry_id(kb_path, "entity_cluster_locs", "Drop")

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/knowledge/locations/registry/merge",
        params={"kb": "test"},
        json={"table": "entity_cluster_locs", "keep_id": keep_id, "drop_id": drop_id},
    )
    assert resp.status_code == 200
    assert resp.json()["merged_into"] == keep_id


def test_api_merge_same_id_422(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/knowledge/locations/registry/merge",
        params={"kb": "test"},
        json={"table": "entity_cluster_locs", "keep_id": 1, "drop_id": 1},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration: page and form round-trips
# ---------------------------------------------------------------------------

def test_page_loads(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/locations/registry", params={"kb": "test"})
    assert resp.status_code == 200
    assert b"Location Registry" in resp.content


def test_edit_roundtrip_persists(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Original Name")
    eid = _get_entry_id(kb_path, "entity_cluster_locs", "Original Name")

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/knowledge/locations/registry/update",
        params={"kb": "test"},
        data={"table": "entity_cluster_locs", "entry_id": eid, "location": "Updated Name"},
    )
    assert resp.status_code == 200
    assert b"Saved" in resp.content

    conn = open_kb(kb_path)
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    conn.close()
    assert rows[0]["location"] == "Updated Name"


def test_delete_removes_entry(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Gone")
    eid = _get_entry_id(kb_path, "entity_cluster_locs", "Gone")

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/knowledge/locations/registry/delete",
        params={"kb": "test"},
        data={"table": "entity_cluster_locs", "entry_id": eid},
    )
    assert resp.status_code == 200

    conn = open_kb(kb_path)
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    conn.close()
    assert len(rows) == 0


def test_merge_backfills_and_deletes(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Keeper")
    _seed_row(kb_path, "cluster_locs", "Dropper", lat=48.85, lon=2.35, threshold_m=200)
    keep_id = _get_entry_id(kb_path, "entity_cluster_locs", "Keeper")
    drop_id = _get_entry_id(kb_path, "entity_cluster_locs", "Dropper")

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/knowledge/locations/registry/merge",
        params={"kb": "test"},
        data={"table": "entity_cluster_locs", "keep_id": keep_id, "drop_id": drop_id},
    )
    assert resp.status_code == 200

    conn = open_kb(kb_path)
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["location"] == "Keeper"
    assert rows[0]["latitude"] == "48.85"


def test_near_dup_pair_detected_on_load(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Edinburgh Castle")
    _seed_row(kb_path, "cluster_locs", "Edinburh Castle")  # typo

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/knowledge/locations/registry", params={"kb": "test"})
    assert resp.status_code == 200
    tbl = resp.json()["tables"][0]
    assert len(tbl["near_duplicates"]) == 1
    pair = tbl["near_duplicates"][0]
    assert pair["score"] >= 0.85


def test_reseed_after_merge_does_not_recreate(tmp_path):
    """After merge+delete, upserting the kept entry's data doesn't create a duplicate."""
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    _seed_registry_table(kb_path)
    _seed_row(kb_path, "cluster_locs", "Alpha", lat=55.0, lon=-3.0)
    _seed_row(kb_path, "cluster_locs", "Beta")
    keep_id = _get_entry_id(kb_path, "entity_cluster_locs", "Alpha")
    drop_id = _get_entry_id(kb_path, "entity_cluster_locs", "Beta")

    client = _make_client(corpus_path, kb_path)
    client.post(
        "/knowledge/locations/registry/merge",
        params={"kb": "test"},
        data={"table": "entity_cluster_locs", "keep_id": keep_id, "drop_id": drop_id},
    )

    # Re-upsert Alpha with same location value → ON CONFLICT updates (no new row)
    conn = open_kb(kb_path)
    upsert_entity_row(conn, "cluster_locs", {"location": "Alpha", "latitude": "55.0",
                                              "longitude": "-3.0", "threshold_m": ""})
    conn.commit()
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["location"] == "Alpha"
