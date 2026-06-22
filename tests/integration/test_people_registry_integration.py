"""Integration tests for KB.Q4 — People Registry page and API routes."""
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


def _open_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    return corpus_conn, kb_conn, corpus_path, kb_path


def _add_person(kb_conn, name: str) -> int:
    kb_conn.execute("INSERT INTO people (preferred_name) VALUES (?)", (name,))
    kb_conn.commit()
    return kb_conn.execute("SELECT id FROM people WHERE preferred_name=?", (name,)).fetchone()[0]


def _centroid_blob() -> bytes:
    return struct.pack("f" * 256, *([0.1] * 256))


def _add_voice_cluster(corpus_conn, person_id=None, label=None) -> int:
    corpus_conn.execute(
        "INSERT INTO voice_speaker_clusters (centroid, member_count, spread, person_id, label)"
        " VALUES (?, 3, 0.1, ?, ?)",
        (_centroid_blob(), person_id, label),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_face_cluster(corpus_conn, person_id=None, label=None) -> int:
    corpus_conn.execute(
        "INSERT INTO face_clusters (centroid, member_count, spread, person_id, label)"
        " VALUES (?, 2, 0.05, ?, ?)",
        (b"\x00" * 512, person_id, label),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# Page and partial routes
# ---------------------------------------------------------------------------

def test_people_registry_page_loads(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/people?kb=test")
    assert r.status_code == 200
    assert b"People" in r.content


# ---------------------------------------------------------------------------
# API: GET /api/knowledge/people
# ---------------------------------------------------------------------------

def test_api_list_people_with_data(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Alice")
    _add_voice_cluster(corpus_conn, person_id=pid)
    _add_face_cluster(corpus_conn, person_id=pid)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.get("/api/knowledge/people?kb=test")
    assert r.status_code == 200
    data = r.json()
    assert len(data["people"]) == 1
    assert data["people"][0]["preferred_name"] == "Alice"
    assert data["people"][0]["voice_cluster_count"] == 1
    assert data["people"][0]["face_cluster_count"] == 1


def test_api_list_people_empty(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.get("/api/knowledge/people?kb=test")
    assert r.status_code == 200
    assert r.json() == {"people": []}


# ---------------------------------------------------------------------------
# API: POST /api/knowledge/people (add)
# ---------------------------------------------------------------------------

def test_api_add_person_success(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post("/api/knowledge/people?kb=test", json={"preferred_name": "Bob"})
    assert r.status_code == 200
    assert r.json()["preferred_name"] == "Bob"
    # Verify persisted
    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT preferred_name FROM people WHERE preferred_name='Bob'").fetchone()
    kb_conn2.close()
    assert row is not None


def test_api_add_person_blank_name_returns_422(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post("/api/knowledge/people?kb=test", json={"preferred_name": "  "})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# API: PUT /api/knowledge/people/{id} (edit)
# ---------------------------------------------------------------------------

def test_api_edit_person_success(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Charlie")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.put(f"/api/knowledge/people/{pid}?kb=test", json={"preferred_name": "Charles"})
    assert r.status_code == 200
    assert r.json()["preferred_name"] == "Charles"


def test_api_edit_person_not_found_returns_404(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.put("/api/knowledge/people/9999?kb=test", json={"preferred_name": "X"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# API: DELETE /api/knowledge/people/{id}
# ---------------------------------------------------------------------------

def test_api_delete_person_success(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Diana")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.delete(f"/api/knowledge/people/{pid}?kb=test")
    assert r.status_code == 200


def test_api_delete_person_blocked_by_cluster_returns_422(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Eve")
    _add_voice_cluster(corpus_conn, person_id=pid)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.delete(f"/api/knowledge/people/{pid}?kb=test")
    assert r.status_code == 422
    assert "voice cluster" in r.json()["detail"]


def test_api_delete_person_not_found_returns_404(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.delete("/api/knowledge/people/9999?kb=test")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# API: POST /api/knowledge/people/{id}/merge
# ---------------------------------------------------------------------------

def test_api_merge_person_success(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    keep_id = _add_person(kb_conn, "Frank")
    drop_id = _add_person(kb_conn, "Francis")
    _add_face_cluster(corpus_conn, person_id=drop_id, label="Francis")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/api/knowledge/people/{keep_id}/merge?kb=test", json={"merge_from_id": drop_id})
    assert r.status_code == 200
    assert r.json()["merged_into"] == keep_id


def test_api_merge_same_id_returns_422(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Grace")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/api/knowledge/people/{pid}/merge?kb=test", json={"merge_from_id": pid})
    assert r.status_code == 422


def test_api_merge_not_found_returns_404(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Hank")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/api/knowledge/people/{pid}/merge?kb=test", json={"merge_from_id": 9999})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Integration: full flows
# ---------------------------------------------------------------------------

def test_add_person_persists_via_ui(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/people/add?kb=test", data={"preferred_name": "Iris"})
    assert r.status_code == 200
    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT preferred_name FROM people WHERE preferred_name='Iris'").fetchone()
    kb_conn2.close()
    assert row is not None


def test_edit_person_name_persists_via_ui(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Jake")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/people/{pid}/edit?kb=test", data={"preferred_name": "Jacob"})
    assert r.status_code == 200
    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT preferred_name FROM people WHERE id=?", (pid,)).fetchone()
    kb_conn2.close()
    assert row["preferred_name"] == "Jacob"


def test_delete_blocked_when_clusters_assigned(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Kate")
    _add_face_cluster(corpus_conn, person_id=pid)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/people/{pid}/delete?kb=test")
    assert r.status_code == 200  # UI handler returns HTML with error message
    assert b"face cluster" in r.content


def test_delete_succeeds_when_no_clusters(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    pid = _add_person(kb_conn, "Leo")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/people/{pid}/delete?kb=test")
    assert r.status_code == 200
    assert b"Deleted" in r.content


def test_merge_reassigns_clusters_and_deletes_person(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    keep_id = _add_person(kb_conn, "Mia")
    drop_id = _add_person(kb_conn, "Mia-old")
    fc_id = _add_face_cluster(corpus_conn, person_id=drop_id, label="Mia-old")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/people/{keep_id}/merge?kb=test", data={"merge_from_id": drop_id})
    assert r.status_code == 200
    corpus_conn2 = open_corpus(corpus_path)
    fc = corpus_conn2.execute("SELECT person_id FROM face_clusters WHERE id=?", (fc_id,)).fetchone()
    corpus_conn2.close()
    assert fc["person_id"] == keep_id
    kb_conn2 = open_kb(kb_path)
    gone = kb_conn2.execute("SELECT id FROM people WHERE id=?", (drop_id,)).fetchone()
    kb_conn2.close()
    assert gone is None


def test_merge_weighted_averages_voice_centroid(tmp_path):
    import numpy as np
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    keep_id = _add_person(kb_conn, "Nina")
    drop_id = _add_person(kb_conn, "Nora")
    # Give drop person a voice cluster with known centroid
    v = np.array([1.0] + [0.0] * 255, dtype=np.float32)
    centroid_blob = v.tobytes()
    corpus_conn.execute(
        "INSERT INTO voice_speaker_clusters (centroid, member_count, spread, person_id, label)"
        " VALUES (?, 4, 0.1, ?, 'Nora')",
        (centroid_blob, drop_id),
    )
    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/people/{keep_id}/merge?kb=test", data={"merge_from_id": drop_id})
    assert r.status_code == 200
    # Nina should now have a voice_centroid
    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT voice_centroid, voice_samples FROM people WHERE id=?", (keep_id,)).fetchone()
    kb_conn2.close()
    assert row["voice_centroid"] is not None
    assert row["voice_samples"] == 4


# ---------------------------------------------------------------------------
# Speaker URL migration
# ---------------------------------------------------------------------------

def test_review_speakers_redirects_to_new_url(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.get("/review/speakers?kb=test", follow_redirects=False)
    assert r.status_code == 301
    assert "/knowledge/people/speakers" in r.headers["location"]


def test_speaker_partials_at_new_path_queue(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/people/speakers/partials/queue?kb=test")
    assert r.status_code == 200


def test_speaker_partials_at_new_path_decisions(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/people/speakers/partials/decisions?kb=test")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Nav
# ---------------------------------------------------------------------------

def test_nav_has_people_link_and_speakers_in_knowledge_section():
    base = Path("D:/Python_Environments/kb-builder/templates/base.html").read_text()
    assert "/knowledge/people?kb=" in base or "/knowledge/people" in base
    assert "/knowledge/people/speakers" in base
    assert "/review/speakers?kb=" not in base
