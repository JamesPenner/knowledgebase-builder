"""Integration tests for KB.U1 — New KB creation form."""
import pytest
from fastapi.testclient import TestClient

from src.api import app


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client():
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /kb/new
# ---------------------------------------------------------------------------

def test_get_kb_new_renders_form():
    client = _client()
    resp = client.get("/kb/new")
    assert resp.status_code == 200
    assert b"Create New Knowledge Base" in resp.content


def test_get_kb_new_has_name_input():
    client = _client()
    resp = client.get("/kb/new")
    assert b'name="name"' in resp.content


# ---------------------------------------------------------------------------
# POST /kb/new — validation errors
# ---------------------------------------------------------------------------

def test_post_kb_new_invalid_name_rejects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client()
    resp = client.post("/kb/new", data={"name": "invalid name!", "template": "general-media"},
                       follow_redirects=False)
    assert resp.status_code == 422


def test_post_kb_new_empty_name_rejects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client()
    resp = client.post("/kb/new", data={"name": "", "template": "general-media"},
                       follow_redirects=False)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /kb/new — successful creation
# ---------------------------------------------------------------------------

def test_post_kb_new_creates_kb_and_redirects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.db.registry import open_registry
    # Ensure registry file is ready
    open_registry(tmp_path).close()
    client = _client()
    resp = client.post(
        "/kb/new",
        data={"name": "testmykb", "template": "general-media"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "testmykb" in resp.headers["location"]
    # KB folder should now exist
    assert (tmp_path / "knowledge-bases" / "testmykb").is_dir()
    assert (tmp_path / "knowledge-bases" / "testmykb" / "corpus.db").exists()


def test_post_kb_new_duplicate_name_rejects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    open_registry = __import__("src.db.registry", fromlist=["open_registry"]).open_registry
    open_registry(tmp_path).close()
    client = _client()
    client.post("/kb/new", data={"name": "mydup", "template": "general-media"}, follow_redirects=False)
    resp = client.post("/kb/new", data={"name": "mydup", "template": "general-media"}, follow_redirects=False)
    assert resp.status_code == 422
