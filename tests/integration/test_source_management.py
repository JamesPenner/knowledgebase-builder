"""Additional KB.U1 integration tests for sources panel and ingest filter wiring."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import add_source, open_corpus
from src.db.kb import open_kb


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


# ---------------------------------------------------------------------------
# GET /api/kb/{name}/sources/panel (HTMX partial)
# ---------------------------------------------------------------------------

def test_sources_panel_renders_html(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sources/panel")
    assert resp.status_code == 200
    assert b"sources-panel" in resp.content


def test_sources_panel_shows_add_form(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sources/panel")
    assert resp.status_code == 200
    # The form input for path uses id="src-form-path"
    assert b"src-form-path" in resp.content


# ---------------------------------------------------------------------------
# Ingest filter wiring: apply_source_filters in run_ingest
# ---------------------------------------------------------------------------

def test_ingest_with_glob_filter_limits_files(tmp_path):
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.ingest import run_ingest

    src_dir = tmp_path / "photos"
    src_dir.mkdir()
    (src_dir / "2024-01.jpg").write_bytes(b"JFIF" + b"\x00" * 100)
    (src_dir / "2024-02.jpg").write_bytes(b"JFIF" + b"\x00" * 100)
    (src_dir / "2023-12.jpg").write_bytes(b"JFIF" + b"\x00" * 100)

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, str(src_dir), "all", True, {"glob": "2024-*"})
    conn.close()

    cancel = make_cancel_event()
    config = load_config(Path("config.yaml"), tmp_path / "config.yaml")
    run_ingest(corpus_path, kb_path, config, NullProgressReporter(), cancel)

    conn2 = open_corpus(corpus_path)
    count = conn2.execute("SELECT COUNT(*) FROM files WHERE source_id=?", (src_id,)).fetchone()[0]
    conn2.close()
    assert count == 2


def test_ingest_with_count_limit_restricts_files(tmp_path):
    from src.config import load_config
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.ingest import run_ingest

    src_dir = tmp_path / "photos"
    src_dir.mkdir()
    for i in range(5):
        (src_dir / f"img{i}.jpg").write_bytes(b"JFIF" + b"\x00" * 100)

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_kb(kb_path).close()
    conn = open_corpus(corpus_path)
    src_id = add_source(conn, str(src_dir), "all", True, {"count_limit": 2})
    conn.close()

    cancel = make_cancel_event()
    config = load_config(Path("config.yaml"), tmp_path / "config.yaml")
    run_ingest(corpus_path, kb_path, config, NullProgressReporter(), cancel)

    conn2 = open_corpus(corpus_path)
    count = conn2.execute("SELECT COUNT(*) FROM files WHERE source_id=?", (src_id,)).fetchone()[0]
    conn2.close()
    assert count == 2


def test_sources_panel_shows_filters_summary(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    conn = open_corpus(corpus_path)
    add_source(conn, "/photos", "images", True, {"glob": "2024-*"})
    conn.close()
    _patch_registry(monkeypatch, tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/kb/test/sources/panel")
    assert resp.status_code == 200
    assert b"2024-" in resp.content
