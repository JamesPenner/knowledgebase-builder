"""Integration tests for KB.AC1 criteria-based file-set endpoints."""
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import add_source, create_file_set, open_corpus, upsert_file
from src.db.kb import open_kb
from src.pipeline.filter_spec import CorpusFilterSpec


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
    src_id, _ = _seed(conn)
    create_file_set(conn, "myset", "desc", CorpusFilterSpec(source_id=src_id))
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
# GET /api/kb/{name}/sets/preview
# ---------------------------------------------------------------------------

def test_sets_preview_all(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn, 5)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets/preview")
    assert resp.status_code == 200
    assert resp.json()["file_count"] == 5


def test_sets_preview_by_file_type(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, "/x")
    upsert_file(conn, src_id, "/x/a.jpg", "a.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src_id, "/x/b.mp4", "b.mp4", ".mp4", "video", 1001, 0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets/preview?file_type=images")
    assert resp.json()["file_count"] == 1


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/folders
# ---------------------------------------------------------------------------

def test_folders_endpoint(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, "/photos")
    upsert_file(conn, src_id, "/photos/2023/a.jpg", "a.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src_id, "/photos/2024/b.jpg", "b.jpg", ".jpg", "images", 1001, 0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/folders")
    assert resp.status_code == 200
    folders = resp.json()["folders"]
    assert any("2023" in f for f in folders)
    assert any("2024" in f for f in folders)


# ---------------------------------------------------------------------------
# POST /api/kb/{name}/sets
# ---------------------------------------------------------------------------

def test_create_set_empty_spec(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn, 5)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sets", json={"name": "all_files", "description": ""})
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
        "name": "src1_only", "description": "", "source_id": src1,
    })
    assert resp.status_code == 200
    assert resp.json()["file_count"] == 3


def test_create_set_duplicate_name_422(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn)
    create_file_set(conn, "dupe", "", CorpusFilterSpec())
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/kb/test/sets", json={"name": "dupe", "description": "other"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/sets/{set_id}
# ---------------------------------------------------------------------------

def test_get_set_by_id(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn)
    set_id = create_file_set(conn, "named_set", "d", CorpusFilterSpec(file_type="images"))
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get(f"/api/kb/test/sets/{set_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "named_set"
    assert data["file_type"] == "images"


def test_get_set_404(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/kb/{name}/sets/{set_id}
# ---------------------------------------------------------------------------

def test_delete_set(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn)
    set_id = create_file_set(conn, "to_delete", "", CorpusFilterSpec())
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.delete(f"/api/kb/test/sets/{set_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    resp2 = client.get("/api/kb/test/sets")
    assert resp2.json() == []


# ---------------------------------------------------------------------------
# run endpoint accepts CorpusFilterSpec inline fields
# ---------------------------------------------------------------------------

def test_run_describe_scope_inline(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", lambda _kb: tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={
        "kb": "test", "run_mode": "resume", "file_type": "images", "folder_prefix": "/photos",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_scope_with_date_range(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", lambda _kb: tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={
        "kb": "test", "run_mode": "resume",
        "date_from": "2023-01-01", "date_to": "2023-12-31",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_scope_with_name_pattern(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", lambda _kb: tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={
        "kb": "test", "run_mode": "resume", "name_pattern": "IMG_*",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/sets/panel
# ---------------------------------------------------------------------------

def test_sets_panel_renders_html(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn, 3)
    create_file_set(conn, "alpha", "desc", CorpusFilterSpec(file_type="images"))
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets/panel")
    assert resp.status_code == 200
    assert "alpha" in resp.text
    assert "wb-sets-panel" in resp.text


def test_sets_panel_empty_state(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets/panel")
    assert resp.status_code == 200
    assert "No saved sets" in resp.text


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/folders with source filter
# ---------------------------------------------------------------------------

def test_folders_filtered_by_source(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src1 = add_source(conn, "/a")
    src2 = add_source(conn, "/b")
    from src.db.corpus import upsert_file
    upsert_file(conn, src1, "/a/sub/f.jpg", "f.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src2, "/b/other/g.jpg", "g.jpg", ".jpg", "images", 1001, 0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get(f"/api/kb/test/folders?source_id={src1}")
    assert resp.status_code == 200
    folders = resp.json()["folders"]
    assert any("/a/" in f for f in folders)
    assert not any("/b/" in f for f in folders)


def test_folders_empty_when_no_files(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/folders")
    assert resp.status_code == 200
    assert resp.json()["folders"] == []


# ---------------------------------------------------------------------------
# sets/preview with various filter combinations
# ---------------------------------------------------------------------------

def test_sets_preview_by_source(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src1 = add_source(conn, "/src1")
    src2 = add_source(conn, "/src2")
    for i in range(4):
        from src.db.corpus import upsert_file
        upsert_file(conn, src1, f"/src1/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src2, "/src2/x.jpg", "x.jpg", ".jpg", "images", 1001, 0)
    conn.commit()
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get(f"/api/kb/test/sets/preview?source_id={src1}")
    assert resp.json()["file_count"] == 4


def test_sets_preview_no_match_returns_zero(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _seed(conn, 3)
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sets/preview?file_type=video")
    assert resp.json()["file_count"] == 0
