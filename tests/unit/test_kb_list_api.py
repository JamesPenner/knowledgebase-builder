"""Unit tests for GET /api/kb — KB list endpoint."""
import sqlite3

from fastapi.testclient import TestClient

from src.api import app

client = TestClient(app, raise_server_exceptions=False)


def _fake_registry(rows: list[dict]):
    """Return (fake_open_registry, fake_list_kbs) that yield the given rows."""
    def fake_open_registry(path):
        return sqlite3.connect(":memory:", check_same_thread=False)

    def fake_list_kbs(c):
        return rows

    return fake_open_registry, fake_list_kbs


class TestListKbsEndpoint:
    def test_returns_200_with_kbs_key(self, monkeypatch):
        open_reg, list_kbs = _fake_registry([])
        monkeypatch.setattr("src.db.registry.open_registry", open_reg)
        monkeypatch.setattr("src.db.registry.list_kbs", list_kbs)
        resp = client.get("/api/kb")
        assert resp.status_code == 200
        assert "kbs" in resp.json()

    def test_empty_registry_returns_empty_list(self, monkeypatch):
        open_reg, list_kbs = _fake_registry([])
        monkeypatch.setattr("src.db.registry.open_registry", open_reg)
        monkeypatch.setattr("src.db.registry.list_kbs", list_kbs)
        assert client.get("/api/kb").json() == {"kbs": []}

    def test_returns_correct_fields(self, monkeypatch):
        rows = [{"name": "alpha", "is_active": 1, "created_at": "2025-01-01 00:00:00"}]
        open_reg, list_kbs = _fake_registry(rows)
        monkeypatch.setattr("src.db.registry.open_registry", open_reg)
        monkeypatch.setattr("src.db.registry.list_kbs", list_kbs)
        kbs = client.get("/api/kb").json()["kbs"]
        assert len(kbs) == 1
        assert kbs[0]["name"] == "alpha"
        assert kbs[0]["created_at"] == "2025-01-01 00:00:00"

    def test_is_active_is_bool(self, monkeypatch):
        rows = [
            {"name": "alpha", "is_active": 1, "created_at": "2025-01-01 00:00:00"},
            {"name": "beta",  "is_active": 0, "created_at": "2025-01-02 00:00:00"},
        ]
        open_reg, list_kbs = _fake_registry(rows)
        monkeypatch.setattr("src.db.registry.open_registry", open_reg)
        monkeypatch.setattr("src.db.registry.list_kbs", list_kbs)
        kbs = client.get("/api/kb").json()["kbs"]
        assert kbs[0]["is_active"] is True
        assert kbs[1]["is_active"] is False
