"""Integration tests for KB.U1 file-set endpoints."""
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import add_source, create_file_set, open_corpus, upsert_file
from src.db.kb import open_kb


def _open_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    return corpus_path, kb_path


def _make_client(corpus_path, kb_path):
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


def _patch_registry(monkeypatch, tmp_path):
    monkeypatch.setattr("src.db.registry.get_kb_path", lambda reg, name: tmp_path)
    monkeypatch.setattr("src.db.registry.open_registry", lambda p: None)


def _seed(conn, n: int = 3):
    src_id = add_source(conn, "/test")
    fids = []
    for i in range(n):
        fid = upsert_file(conn, src_id, f"/test/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000, 0.0)
        fids.append(fid)
    conn.commit()
    return src_id, fids


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/sets
# ---------------------------------------------------------------------------

def test_list_sets_empty(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sets_returns_sets(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _, fids = _seed(conn)
    create_file_set(conn, "myset", "desc", fids)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "myset"
    assert data[0]["file_count"] == 3


# ---------------------------------------------------------------------------
# POST /api/kb/{name}/sets
# ---------------------------------------------------------------------------

def test_create_set_all_scope(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _, fids = _seed(conn, 5)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sets", json={
        "name": "all_files", "description": "", "scope": {}
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_count"] == 5
    assert "id" in data


def test_create_set_by_source(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src1 = add_source(conn, "/src1")
    src2 = add_source(conn, "/src2")
    for i in range(3):
        upsert_file(conn, src1, f"/src1/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000, 0.0)
    for i in range(2):
        upsert_file(conn, src2, f"/src2/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000, 0.0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sets", json={
        "name": "src1_only", "description": "", "scope": {"source_id": src1}
    })
    assert resp.status_code == 200
    assert resp.json()["file_count"] == 3


def test_create_set_duplicate_name_422(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _, fids = _seed(conn)
    create_file_set(conn, "dupe", "", fids)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sets", json={
        "name": "dupe", "description": "other", "scope": {}
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/kb/{name}/sets/{set_id}
# ---------------------------------------------------------------------------

def test_delete_set(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _, fids = _seed(conn)
    set_id = create_file_set(conn, "to_delete", "", fids)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.delete(f"/api/kb/test/sets/{set_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    # Verify gone
    resp2 = client.get("/api/kb/test/sets")
    assert resp2.json() == []


# ---------------------------------------------------------------------------
# run endpoint accepts set_id scope
# ---------------------------------------------------------------------------

def test_run_describe_scope_by_set(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", lambda _kb: tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={"kb": "test", "run_mode": "resume", "set_id": 1})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
