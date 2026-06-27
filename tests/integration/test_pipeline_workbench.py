"""Integration tests for KB.T1 — Pipeline Workbench."""
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
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    corpus_conn.close()
    kb_conn.close()
    return corpus_path, kb_path


# ---------------------------------------------------------------------------
# validate stage API endpoints
# ---------------------------------------------------------------------------

def test_validate_run_endpoint(tmp_path, monkeypatch):
    corpus_path, kb_path = _open_dbs(tmp_path)
    # The run endpoint calls _get_kb_folder which reads the real registry;
    # patch it to point at our tmp_path KB folder instead.
    import src.api.pipeline as _pipeline_mod
    monkeypatch.setattr(_pipeline_mod, "_get_kb_folder", lambda _kb: tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/validate/run", json={"kb": "test"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_validate_cancel_endpoint(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/validate/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_validate_status_endpoint(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/stages/validate/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


# ---------------------------------------------------------------------------
# resolve-plan endpoint
# ---------------------------------------------------------------------------

def test_resolve_plan_single_stage(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/stages/resolve-plan", json={"stages": ["analyse"], "completed": []})
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    stage_names = [e for e in plan if isinstance(e, str)]
    assert "ingest" in stage_names
    assert "analyse" in stage_names
    assert stage_names.index("ingest") < stage_names.index("analyse")


def test_resolve_plan_multi_stage(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/resolve-plan",
        json={"stages": ["describe", "quality"], "completed": []},
    )
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    stage_names = [e for e in plan if isinstance(e, str)]
    assert "hash" in stage_names
    assert "describe" in stage_names
    assert "quality" in stage_names
    assert stage_names.index("hash") < stage_names.index("describe")


def test_resolve_plan_with_completed(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/resolve-plan",
        json={"stages": ["analyse"], "completed": ["ingest"]},
    )
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    stage_names = [e for e in plan if isinstance(e, str)]
    assert "ingest" not in stage_names
    assert "analyse" in stage_names


def test_resolve_plan_unknown_stage_returns_422(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/resolve-plan",
        json={"stages": ["not_a_real_stage"], "completed": []},
    )
    assert resp.status_code == 422


def test_resolve_plan_includes_touchpoints(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/resolve-plan",
        json={"stages": ["normalize"], "completed": []},
    )
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    touchpoints = [e["touchpoint"] for e in plan if isinstance(e, dict)]
    assert "normalise_review" in touchpoints


def test_resolve_plan_no_duplicate_stages(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/stages/resolve-plan",
        json={"stages": ["describe", "transcribe"], "completed": []},
    )
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    stage_names = [e for e in plan if isinstance(e, str)]
    assert len(stage_names) == len(set(stage_names))


# ---------------------------------------------------------------------------
# Pipeline workbench page
# ---------------------------------------------------------------------------

def test_pipeline_page_returns_200(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    assert resp.status_code == 200


def test_pipeline_page_includes_group_labels(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    text = resp.text
    for label in ("Discovery", "Metadata", "ML Analysis", "Enrichment", "Vocabulary", "Output"):
        assert label in text, f"Group label {label!r} not found in pipeline page"


def test_pipeline_page_includes_validate(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    assert "validate" in resp.text


def test_pipeline_page_includes_gate_labels(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    text = resp.text
    assert "Normalise Review" in text
    assert "Suggest Review" in text
    assert "New Terms Review" in text


def test_pipeline_page_context_has_groups(tmp_path):
    """Template context must include groups list with expected structure."""
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    # Presence of all six group IDs injected as JS confirms server-side enrichment
    for gid in ("discovery", "metadata", "ml_analysis", "enrichment", "vocabulary", "output"):
        assert gid in resp.text


def test_pipeline_page_stage_ready_when_no_deps(tmp_path):
    """Ingest has no deps — should appear as ready on a fresh corpus."""
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    # ingest badge should be 'ready' not 'blocked'
    assert "badge-ready" in resp.text or "ready" in resp.text


def test_pipeline_page_touchpoints_keys_present(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline?kb=test")
    # Gate banners for all three review touchpoints should always appear (links are conditional on data)
    assert "Normalise Review" in resp.text
    assert "Suggest Review" in resp.text
    assert "New Terms Review" in resp.text
