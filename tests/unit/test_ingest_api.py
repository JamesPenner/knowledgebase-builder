"""Unit tests for the ingest API endpoint — HTTP routing and run_mode behaviour."""
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import app
from src.api.pipeline import RunRequest

client = TestClient(app, raise_server_exceptions=False)


class TestRunRequestForIngest:
    def test_defaults(self):
        req = RunRequest(kb="mydb")
        assert req.run_mode == "resume"
        assert req.workers is None

    def test_rerun_accepted(self):
        req = RunRequest(kb="mydb", run_mode="rerun")
        assert req.run_mode == "rerun"


class TestIngestRunEndpoint:
    def test_missing_kb_returns_error(self):
        resp = client.post("/api/stages/ingest/run", json={})
        assert resp.status_code == 422

    def test_unknown_kb_returns_4xx(self):
        resp = client.post("/api/stages/ingest/run", json={"kb": "__no_such_kb__"})
        assert 400 <= resp.status_code < 600

    def test_resume_mode_does_not_clear_corpus(self, tmp_path, monkeypatch):
        captured = {}

        def fake_get_kb_folder(kb: str) -> Path:
            folder = tmp_path / kb
            folder.mkdir(exist_ok=True)
            from src.db.corpus import open_corpus
            from src.db.kb import open_kb
            open_corpus(folder / "corpus.db").close()
            open_kb(folder / "knowledge.db").close()
            return folder

        def fake_run_ingest(corpus_path, kb_path, config, progress, cancel):
            captured["called"] = True

        def fake_reset(conn):
            captured["reset"] = True

        import src.api.pipeline as _mod
        import src.db.corpus as _corpus
        monkeypatch.setattr(_mod, "_get_kb_folder", fake_get_kb_folder)
        monkeypatch.setattr("src.stages.ingest.run_ingest", fake_run_ingest)
        monkeypatch.setattr(_corpus, "reset_corpus_files", fake_reset)

        resp = client.post("/api/stages/ingest/run", json={"kb": "test", "run_mode": "resume"})
        assert resp.status_code == 200
        assert "reset" not in captured

    def test_rerun_mode_clears_corpus_before_ingest(self, tmp_path, monkeypatch):
        call_order = []

        def fake_get_kb_folder(kb: str) -> Path:
            folder = tmp_path / kb
            folder.mkdir(exist_ok=True)
            from src.db.corpus import open_corpus
            from src.db.kb import open_kb
            open_corpus(folder / "corpus.db").close()
            open_kb(folder / "knowledge.db").close()
            return folder

        def fake_reset(conn):
            call_order.append("reset")

        def fake_run_ingest(corpus_path, kb_path, config, progress, cancel):
            call_order.append("ingest")

        import src.api.pipeline as _mod
        import src.db.corpus as _corpus
        monkeypatch.setattr(_mod, "_get_kb_folder", fake_get_kb_folder)
        monkeypatch.setattr(_corpus, "reset_corpus_files", fake_reset)
        monkeypatch.setattr("src.stages.ingest.run_ingest", fake_run_ingest)

        resp = client.post("/api/stages/ingest/run", json={"kb": "test", "run_mode": "rerun"})
        assert resp.status_code == 200
        # Background tasks run synchronously in TestClient
        assert call_order == ["reset", "ingest"]

    def test_cancel_returns_cancelled(self):
        resp = client.post("/api/stages/ingest/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_status_returns_idle_when_no_job(self):
        import src.pipeline.progress as _prog
        _prog._progress.pop("ingest", None)
        resp = client.get("/api/stages/ingest/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
