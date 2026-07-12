"""Integration tests for KB.AM3 — Knowledge Settings UI panel + cascading gate badges."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import get_classify_rules, open_kb


def _make_client(corpus_path: Path, kb_path: Path, monkeypatch) -> TestClient:
    # ui.py's /pipeline* routes resolve the KB via the resolve_kb dependency.
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    # kb.py's endpoints (settings, classify-rules) look the KB up in the real
    # registry directly, bypassing resolve_kb — patch that lookup too so both
    # route families see the same tmp_path KB under the name "test".
    kb_folder = kb_path.parent
    monkeypatch.setattr("src.db.registry.get_kb_path", lambda reg, name: kb_folder)
    monkeypatch.setattr("src.db.registry.open_registry", lambda p: None)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


@pytest.fixture()
def kb_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    return corpus_path, kb_path


def _checkbox_checked(html: str, category: str) -> bool:
    m = re.search(
        r'type="checkbox"\s*(checked)?\s*\n?\s*onchange="WB\.toggleKnowledgeCategory\(\'test\', \'%s\'' % category,
        html,
    )
    assert m, f"could not find {category} toggle in HTML"
    return m.group(1) is not None


# ---------------------------------------------------------------------------
# Settings panel partial
# ---------------------------------------------------------------------------

def test_settings_panel_returns_default_toggles(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    resp = client.get("/api/kb/test/settings/panel")
    assert resp.status_code == 200
    assert "People" in resp.text
    assert "Places" in resp.text
    assert "Dates" in resp.text
    assert _checkbox_checked(resp.text, "people")
    assert _checkbox_checked(resp.text, "places")
    assert _checkbox_checked(resp.text, "dates")


def test_settings_panel_reflects_disabled_category(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    client.post("/api/kb/test/settings", json={"category": "people", "enabled": False})
    resp = client.get("/api/kb/test/settings/panel")
    assert resp.status_code == 200
    assert not _checkbox_checked(resp.text, "people")
    assert _checkbox_checked(resp.text, "places")
    assert _checkbox_checked(resp.text, "dates")


def test_settings_panel_lists_calendar_rules(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    resp = client.get("/api/kb/test/settings/panel")
    assert resp.status_code == 200
    assert "Christmas Day" in resp.text
    assert re.search(r"Calendar rules \(\d+\)", resp.text)
    # non-calendar rules must not leak into this list
    assert "Landscape orientation" not in resp.text


def test_pipeline_page_includes_knowledge_settings_panel(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Knowledge Settings" in resp.text
    assert "/api/kb/test/settings/panel" in resp.text


# ---------------------------------------------------------------------------
# Cascading stage state
# ---------------------------------------------------------------------------

def test_pipeline_groups_default_all_enabled_no_skips(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    resp = client.get("/pipeline/groups", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Skipped —" not in resp.text
    assert "Partial —" not in resp.text
    assert 'id="btn-run-face"' in resp.text


def test_pipeline_groups_shows_skipped_badge_when_people_disabled(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    client.post("/api/kb/test/settings", json={"category": "people", "enabled": False})
    resp = client.get("/pipeline/groups", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Skipped — People disabled" in resp.text
    # Run button removed for gated stages
    assert 'id="btn-run-face"' not in resp.text
    assert 'id="btn-run-voice"' not in resp.text
    # ungated stage keeps its Run button
    assert 'id="btn-run-classify"' in resp.text


def test_pipeline_groups_shows_skipped_badge_when_places_disabled(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    client.post("/api/kb/test/settings", json={"category": "places", "enabled": False})
    resp = client.get("/pipeline/groups", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Skipped — Places disabled" in resp.text
    assert 'id="btn-run-geolocate"' not in resp.text


def test_pipeline_groups_shows_partial_note_when_dates_disabled(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    client.post("/api/kb/test/settings", json={"category": "dates", "enabled": False})
    resp = client.get("/pipeline/groups", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Skipped — Dates disabled" in resp.text  # temporal stage fully gated
    assert "Partial — Dates disabled" in resp.text  # classify stays runnable
    assert 'id="btn-run-classify"' in resp.text


def test_pipeline_page_reflects_gating_end_to_end(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    client.post("/api/kb/test/settings", json={"category": "people", "enabled": False})
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Skipped — People disabled" in resp.text


# ---------------------------------------------------------------------------
# PATCH /api/kb/{name}/classify-rules/{id}
# ---------------------------------------------------------------------------

def test_patch_classify_rule_toggles_enabled(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    kb_conn = open_kb(kb_path)
    rule = get_classify_rules(kb_conn, enabled_only=False, category="calendar")[0]
    kb_conn.close()

    client = _make_client(corpus_path, kb_path, monkeypatch)
    resp = client.patch(f"/api/kb/test/classify-rules/{rule['id']}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] == 0

    # persists across a re-fetch
    kb_conn2 = open_kb(kb_path)
    refetched = get_classify_rules(kb_conn2, enabled_only=False, category="calendar")
    kb_conn2.close()
    updated = next(r for r in refetched if r["id"] == rule["id"])
    assert updated["enabled"] == 0


def test_patch_classify_rule_rejects_unknown_id(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path, monkeypatch)
    resp = client.patch("/api/kb/test/classify-rules/999999", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_classify_rule_rejects_non_calendar_rule(kb_dbs, monkeypatch):
    corpus_path, kb_path = kb_dbs
    kb_conn = open_kb(kb_path)
    non_calendar = get_classify_rules(kb_conn, enabled_only=False, category="technical")[0]
    kb_conn.close()

    client = _make_client(corpus_path, kb_path, monkeypatch)
    resp = client.patch(f"/api/kb/test/classify-rules/{non_calendar['id']}", json={"enabled": False})
    assert resp.status_code == 400
