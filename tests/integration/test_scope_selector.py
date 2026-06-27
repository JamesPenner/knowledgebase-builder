"""Integration tests for KB.T2 — Scope Selector (updated for KB.V1 run_mode)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import open_kb


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
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    return corpus_path, kb_path


def _add_source(conn, path: str, removed: bool = False) -> int:
    from src.db.corpus import add_source
    sid = add_source(conn, path)
    if removed:
        conn.execute("UPDATE sources SET removed_at = datetime('now') WHERE id = ?", (sid,))
        conn.commit()
    return sid


def _kb_folder_stub(tmp_path):
    return lambda _kb: tmp_path


def _patch_kb_registry(monkeypatch, tmp_path):
    monkeypatch.setattr("src.db.registry.get_kb_path", lambda reg, name: tmp_path)
    monkeypatch.setattr("src.db.registry.open_registry", lambda p: None)


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/sources
# ---------------------------------------------------------------------------

def test_get_sources_returns_list(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _add_source(conn, "/photos/2024")
    _add_source(conn, "/photos/2023")
    conn.close()
    _patch_kb_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/testname/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    paths = {s["path"] for s in data}
    assert "/photos/2024" in paths
    assert "/photos/2023" in paths
    assert all("id" in s for s in data)


def test_get_sources_empty(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_kb_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/testname/sources")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_sources_excludes_removed(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    _add_source(conn, "/photos/keep")
    _add_source(conn, "/photos/removed", removed=True)
    conn.close()
    _patch_kb_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/testname/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["path"] == "/photos/keep"


# ---------------------------------------------------------------------------
# Run endpoints accept run_mode + independent filter params
# ---------------------------------------------------------------------------

def test_run_describe_run_mode_resume(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={"kb": "test", "run_mode": "resume"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_with_source_id(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "run_mode": "resume", "source_id": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_with_file_type(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "run_mode": "resume", "file_type": "images"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_run_mode_rerun(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "run_mode": "rerun"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_quality_run_mode_rerun(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/quality/run",
        json={"kb": "test", "run_mode": "rerun"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_transcribe_with_source_id(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/transcribe/run",
        json={"kb": "test", "run_mode": "resume", "source_id": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_unknown_run_mode_does_not_error(tmp_path, monkeypatch):
    """RunRequest accepts any string for run_mode; unknown values fall back to resume."""
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "run_mode": "some_future_mode"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Pipeline page HTML
# ---------------------------------------------------------------------------

def test_pipeline_page_includes_sources_context(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    from src.db.corpus import add_source
    add_source(conn, "/photos/2024")
    conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    assert resp.status_code == 200
    assert "KB_SOURCES" in resp.text


def test_pipeline_page_scope_bar_present(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    assert resp.status_code == 200
    assert "wb-scope-bar" in resp.text
    assert "scope-source" in resp.text
    assert "scope-type" in resp.text
    assert "scope-set" in resp.text


def test_pipeline_page_sources_header_present(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    assert resp.status_code == 200
    assert "wb-sources" in resp.text
    assert "wb-sources-header" in resp.text


def test_pipeline_page_run_mode_toggle_present(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    assert resp.status_code == 200
    assert "wb-run-mode" in resp.text
    assert "setAllModes" in resp.text
