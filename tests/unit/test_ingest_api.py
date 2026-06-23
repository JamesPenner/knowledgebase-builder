"""Unit tests for the ingest API endpoint — HTTP routing and IngestRunRequest model."""
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import app
from src.api.pipeline import IngestRunRequest

client = TestClient(app, raise_server_exceptions=False)


class TestIngestRunRequest:
    def test_defaults(self):
        req = IngestRunRequest(kb="mydb")
        assert req.incremental is False
        assert req.workers is None

    def test_incremental_true(self):
        req = IngestRunRequest(kb="mydb", incremental=True)
        assert req.incremental is True

    def test_workers_accepted(self):
        req = IngestRunRequest(kb="mydb", workers=4)
        assert req.workers == 4


class TestIngestRunEndpoint:
    def test_missing_kb_returns_error(self):
        resp = client.post("/api/stages/ingest/run", json={})
        assert resp.status_code == 422

    def test_unknown_kb_returns_4xx(self):
        resp = client.post("/api/stages/ingest/run", json={"kb": "__no_such_kb__"})
        assert 400 <= resp.status_code < 600

    def test_incremental_flag_passed_to_run_ingest(self, tmp_path, monkeypatch):
        captured = {}

        def fake_get_kb_folder(kb: str) -> Path:
            folder = tmp_path / kb
            folder.mkdir(exist_ok=True)
            from src.db.corpus import open_corpus
            from src.db.kb import open_kb
            open_corpus(folder / "corpus.db").close()
            open_kb(folder / "knowledge.db").close()
            return folder

        def fake_run_ingest(corpus_path, kb_path, config, progress, cancel, incremental=False):
            captured["incremental"] = incremental

        import src.api.pipeline as _mod
        monkeypatch.setattr(_mod, "_get_kb_folder", fake_get_kb_folder)
        monkeypatch.setattr("src.stages.ingest.run_ingest", fake_run_ingest)

        resp = client.post("/api/stages/ingest/run", json={"kb": "test", "incremental": True})
        assert resp.status_code == 200
        assert captured.get("incremental") is True

    def test_cancel_returns_cancelled(self):
        resp = client.post("/api/stages/ingest/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_status_returns_idle_when_no_job(self):
        import src.pipeline.progress as _prog
        _prog._progress.pop("ingest", None)  # clear state from any prior test in this module
        resp = client.get("/api/stages/ingest/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
