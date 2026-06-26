"""Integration tests for KB.U1 source management endpoints."""
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import add_source, open_corpus, upsert_file
from src.db.kb import open_kb


def _open_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    return corpus_path, kb_path


def _patch_registry(monkeypatch, tmp_path):
    monkeypatch.setattr("src.db.registry.get_kb_path", lambda reg, name: tmp_path)
    monkeypatch.setattr("src.db.registry.open_registry", lambda p: None)


def _make_client(corpus_path, kb_path):
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


# ---------------------------------------------------------------------------
# POST /api/kb/{name}/sources
# ---------------------------------------------------------------------------

def test_add_source_valid_path(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    src_dir = tmp_path / "photos"
    src_dir.mkdir()
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sources", json={
        "path": str(src_dir), "file_type": "images", "recursive": True, "filters": {}
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["path"] == str(src_dir)


def test_add_source_invalid_path_returns_422(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sources", json={
        "path": "/this/path/does/not/exist/xyz123", "file_type": "all", "recursive": True, "filters": {}
    })
    assert resp.status_code == 422


def test_add_source_with_filters(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    src_dir = tmp_path / "photos"
    src_dir.mkdir()
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sources", json={
        "path": str(src_dir), "file_type": "all", "recursive": False,
        "filters": {"glob": "2024-*", "count_limit": 100}
    })
    assert resp.status_code == 200
    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT filters_json FROM sources WHERE id=?", (resp.json()["id"],)).fetchone()
    import json
    stored = json.loads(row["filters_json"])
    assert stored["glob"] == "2024-*"
    conn.close()


# ---------------------------------------------------------------------------
# DELETE /api/kb/{name}/sources/{source_id}
# ---------------------------------------------------------------------------

def test_remove_source_soft(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, "/photos")
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.delete(f"/api/kb/test/sources/{src_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted_files"] == 0
    conn2 = open_corpus(corpus_path)
    row = conn2.execute("SELECT removed_at FROM sources WHERE id=?", (src_id,)).fetchone()
    assert row["removed_at"] is not None
    conn2.close()


def test_remove_source_cascade(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, "/photos")
    for i in range(3):
        upsert_file(conn, src_id, f"/photos/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000, 0.0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.delete(f"/api/kb/test/sources/{src_id}?cascade=true")
    assert resp.status_code == 200
    assert resp.json()["deleted_files"] == 3


# ---------------------------------------------------------------------------
# POST /api/kb/{name}/sources/preview
# ---------------------------------------------------------------------------

def test_preview_source_counts_files(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.jpg").write_text("x")
    (src_dir / "b.jpg").write_text("x")
    (src_dir / "c.mp4").write_text("x")
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sources/preview", json={
        "path": str(src_dir), "file_type": "all", "recursive": True, "filters": {}
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert "images" in data["by_type"]
    assert "video" in data["by_type"]


def test_preview_source_with_glob_filter(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "2024-01.jpg").write_text("x")
    (src_dir / "2024-02.jpg").write_text("x")
    (src_dir / "2023-01.jpg").write_text("x")
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sources/preview", json={
        "path": str(src_dir), "file_type": "all", "recursive": True,
        "filters": {"glob": "2024-*"}
    })
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_preview_invalid_path_422(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sources/preview", json={
        "path": "/no/such/path/xyz", "file_type": "all", "recursive": True, "filters": {}
    })
    assert resp.status_code == 422
