"""Integration tests for KB.S5 — Prompt Library page and API routes."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import load_stage_prompt, open_kb


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
    return corpus_conn, kb_conn, corpus_path, kb_path


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

def test_prompt_library_page_returns_200(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/prompts?kb=test")
    assert resp.status_code == 200
    assert "Prompt Library" in resp.text


def test_prompt_library_page_lists_all_four_builtins(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/knowledge/prompts?kb=test")
    assert resp.status_code == 200
    assert "describe" in resp.text.lower()
    assert "retag" in resp.text.lower()
    assert "summarize" in resp.text.lower()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def test_create_prompt_returns_201(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/knowledge/prompts?kb=test", json={
        "stage": "retag",
        "prompt_key": "system",
        "name": "My Variant",
        "body": "Custom retag instruction.",
    })
    assert resp.status_code == 201
    assert "id" in resp.json()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def test_update_prompt_body(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    from src.db.kb import upsert_stage_prompt
    prompt_id = upsert_stage_prompt(kb_conn, "retag", "system", "Editable", "v1")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.put(f"/api/knowledge/prompts/{prompt_id}?kb=test", json={"body": "v2"})
    assert resp.status_code == 200
    kb_conn2 = open_kb(kb_path)
    row = kb_conn2.execute("SELECT body FROM stage_prompts WHERE id=?", (prompt_id,)).fetchone()
    kb_conn2.close()
    assert row["body"] == "v2"


def test_update_builtin_prompt_returns_400(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    builtin_id = kb_conn.execute(
        "SELECT id FROM stage_prompts WHERE is_builtin=1 LIMIT 1"
    ).fetchone()["id"]
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.put(f"/api/knowledge/prompts/{builtin_id}?kb=test", json={"body": "hacked"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------

def test_activate_makes_prompt_active(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    from src.db.kb import upsert_stage_prompt
    new_id = upsert_stage_prompt(kb_conn, "retag", "system", "NewActive", "new body")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.post(f"/api/knowledge/prompts/{new_id}/activate?kb=test")
    assert resp.status_code == 200
    kb_conn2 = open_kb(kb_path)
    loaded = load_stage_prompt(kb_conn2, "retag", "system", default="fallback")
    kb_conn2.close()
    assert loaded == "new body"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_user_prompt_returns_200(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    from src.db.kb import upsert_stage_prompt
    new_id = upsert_stage_prompt(kb_conn, "retag", "system", "ToDelete", "body")
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.delete(f"/api/knowledge/prompts/{new_id}?kb=test")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == new_id


def test_delete_builtin_returns_400(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    builtin_id = kb_conn.execute(
        "SELECT id FROM stage_prompts WHERE is_builtin=1 LIMIT 1"
    ).fetchone()["id"]
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.delete(f"/api/knowledge/prompts/{builtin_id}?kb=test")
    assert resp.status_code == 400


def test_delete_active_variant_restores_builtin(tmp_path):
    corpus_conn, kb_conn, corpus_path, kb_path = _open_dbs(tmp_path)
    from src.db.kb import set_active_stage_prompt, upsert_stage_prompt
    new_id = upsert_stage_prompt(kb_conn, "retag", "system", "ActiveVariant", "custom")
    set_active_stage_prompt(kb_conn, "retag", "system", new_id)
    corpus_conn.close()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    client.delete(f"/api/knowledge/prompts/{new_id}?kb=test")
    kb_conn2 = open_kb(kb_path)
    active = kb_conn2.execute(
        "SELECT is_builtin FROM stage_prompts WHERE stage='retag' AND prompt_key='system' AND is_active=1"
    ).fetchone()
    kb_conn2.close()
    assert active is not None
    assert active["is_builtin"] == 1
