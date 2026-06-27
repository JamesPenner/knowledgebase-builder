"""Integration tests for KB.V1 — run_mode field and independent scope filters."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import add_source, open_corpus, upsert_file
from src.db.kb import open_kb


def _open_dbs(tmp_path: Path):
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


def _kb_folder_stub(tmp_path):
    return lambda _kb: tmp_path


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


def test_run_request_run_mode_default_resume(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={"kb": "test"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_request_scope_filters_applied_independently(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    # source_id and file_type can be passed without specifying a scope mode
    resp = client.post("/api/stages/describe/run", json={
        "kb": "test", "run_mode": "resume", "source_id": 1, "file_type": "images",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_rerun_mode_resets_describe(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, str(tmp_path))
    file_id = upsert_file(conn, src_id, str(tmp_path / "a.jpg"), "a.jpg", ".jpg", "images", 1000, 0.0)
    conn.execute("INSERT OR REPLACE INTO descriptions (file_id, pass1_status) VALUES (?, 'done')", (file_id,))
    conn.commit()
    conn.close()
    import src.api.pipeline as pm
    import src.stages.describe as desc_mod
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    # Stub out the actual describe run so only the reset fires
    monkeypatch.setattr(desc_mod, "run_describe", lambda *a, **kw: None)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={"kb": "test", "run_mode": "rerun"})
    assert resp.status_code == 200
    import time; time.sleep(0.15)
    conn2 = open_corpus(corpus_path)
    row = conn2.execute("SELECT pass1_status FROM descriptions WHERE file_id=?", (file_id,)).fetchone()
    conn2.close()
    assert row["pass1_status"] == "pending"


def test_rerun_mode_resets_quality(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, str(tmp_path))
    file_id = upsert_file(conn, src_id, str(tmp_path / "a.jpg"), "a.jpg", ".jpg", "images", 1000, 0.0)
    conn.execute(
        "INSERT OR REPLACE INTO file_quality (file_id, sharpness) VALUES (?, 0.5)",
        (file_id,),
    )
    conn.commit()
    conn.close()
    import src.api.pipeline as pm
    import src.stages.quality as quality_mod
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    # Stub out the actual quality run so only the reset fires
    monkeypatch.setattr(quality_mod, "run_quality", lambda *a, **kw: None)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/quality/run", json={"kb": "test", "run_mode": "rerun"})
    assert resp.status_code == 200
    import time; time.sleep(0.15)
    conn2 = open_corpus(corpus_path)
    row = conn2.execute("SELECT COUNT(*) AS n FROM file_quality").fetchone()
    conn2.close()
    assert row["n"] == 0


def test_source_id_filter_limits_files(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={
        "kb": "test", "run_mode": "resume", "source_id": 42,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_file_type_filter_limits_files(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    import src.api.pipeline as pm
    monkeypatch.setattr(pm, "_get_kb_folder", _kb_folder_stub(tmp_path))
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/describe/run", json={
        "kb": "test", "run_mode": "resume", "file_type": "video",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
