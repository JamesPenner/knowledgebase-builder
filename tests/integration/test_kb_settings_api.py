"""Integration tests for GET/POST /api/kb/{name}/settings (KB.AM1)."""
from fastapi.testclient import TestClient

from src.api import app
from src.db.kb import open_kb


def _open_dbs(tmp_path):
    from src.db.corpus import open_corpus

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    return corpus_path, kb_path


def _make_client():
    return TestClient(app, raise_server_exceptions=True)


def _patch_registry(monkeypatch, tmp_path):
    monkeypatch.setattr("src.db.registry.get_kb_path", lambda reg, name: tmp_path)
    monkeypatch.setattr("src.db.registry.open_registry", lambda p: None)


def test_get_settings_returns_defaults(tmp_path, monkeypatch):
    _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client()

    resp = client.get("/api/kb/test/settings")
    assert resp.status_code == 200
    assert resp.json() == {"people": True, "places": True, "dates": True}


def test_post_settings_updates_and_returns_full_state(tmp_path, monkeypatch):
    _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client()

    resp = client.post("/api/kb/test/settings", json={"category": "people", "enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"people": False, "places": True, "dates": True}

    resp2 = client.get("/api/kb/test/settings")
    assert resp2.json()["people"] is False


def test_post_settings_rejects_unknown_category(tmp_path, monkeypatch):
    _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client()

    resp = client.post("/api/kb/test/settings", json={"category": "pets", "enabled": False})
    assert resp.status_code == 422
