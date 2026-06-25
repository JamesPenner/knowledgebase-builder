"""Integration tests for KB.T2 — Scope Selector."""
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
    """Returns a function that patches _get_kb_folder to point at tmp_path."""
    return lambda _kb: tmp_path


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/sources
# ---------------------------------------------------------------------------

def _patch_kb_registry(monkeypatch, tmp_path):
    """Patch registry lookups so kb_sources/stats/health point at tmp_path."""
    monkeypatch.setattr("src.db.registry.get_kb_path", lambda reg, name: tmp_path)
    monkeypatch.setattr("src.db.registry.open_registry", lambda p: None)


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
# Run endpoints accept scope params
# ---------------------------------------------------------------------------

def test_run_describe_scope_resume(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={"kb": "test", "scope_mode": "resume"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_scope_by_source(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "scope_mode": "by_source", "source_id": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_scope_by_type(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "scope_mode": "by_type", "file_type": "image"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_describe_scope_rerun(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "scope_mode": "rerun"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_quality_scope_rerun(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/quality/run",
        json={"kb": "test", "scope_mode": "rerun"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_transcribe_scope_by_source(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/transcribe/run",
        json={"kb": "test", "scope_mode": "by_source", "source_id": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_unknown_scope_mode_does_not_error(tmp_path, monkeypatch):
    """RunRequest accepts any string for scope_mode; unknown values fall back to resume."""
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/describe/run",
        json={"kb": "test", "scope_mode": "some_future_mode"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Pipeline page includes sources
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
    # Sources are injected as KB_SOURCES in page JS
    assert "KB_SOURCES" in resp.text


def test_pipeline_page_scope_selector_present(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    assert resp.status_code == 200
    assert "scope-mode" in resp.text
    assert "scope-source" in resp.text
    assert "scope-type" in resp.text
