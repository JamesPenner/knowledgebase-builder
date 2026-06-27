"""Integration tests for the Capture Rules management UI."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import add_capture_rule, open_kb


def _make_client(corpus_path: Path, kb_path: Path) -> TestClient:
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app, raise_server_exceptions=True)


def _open_dbs(tmp_path: Path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()
    return corpus_path, kb_path


def _seed_rule(kb_path: Path, pattern: str = r"^\d{8}$", extract_as: str = "file_date") -> int:
    conn = open_kb(kb_path)
    rule_id = add_capture_rule(conn, pattern=pattern, label="Test rule", extract_as=extract_as,
                               value_type="date", date_precision="day")
    conn.close()
    return rule_id


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


# ---------------------------------------------------------------------------

def test_page_loads(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/capture-rules?kb=test")
    assert r.status_code == 200
    assert "Capture Rules" in r.text


def test_list_partial_empty(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/capture-rules/partials/list?kb=test")
    assert r.status_code == 200
    assert "No capture rules" in r.text


def test_add_rule_persists(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/capture-rules/add?kb=test", data={
        "pattern": r"^\d{8}$",
        "extract_as": "file_date_full",
        "label": "Date YYYYMMDD",
        "value_type": "date",
        "date_precision": "day",
        "format_str": "",
        "keep_token": "false",
    })
    assert r.status_code == 200
    assert "added" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM capture_rules WHERE extract_as='file_date_full'").fetchone()
    conn.close()
    assert row is not None
    assert row["value_type"] == "date"
    assert row["date_precision"] == "day"


def test_add_rule_duplicate_pattern_returns_error(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _seed_rule(kb_path, pattern=r"^\d{8}$")
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/capture-rules/add?kb=test", data={
        "pattern": r"^\d{8}$",
        "extract_as": "other_field",
        "label": "",
        "value_type": "",
        "date_precision": "",
        "format_str": "",
        "keep_token": "false",
    })
    assert r.status_code == 200
    assert "already exists" in r.text
    conn = open_kb(kb_path)
    count = conn.execute("SELECT COUNT(*) FROM capture_rules").fetchone()[0]
    conn.close()
    assert count == 1


def test_list_partial_shows_added_rule(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _seed_rule(kb_path, pattern=r"^\d{8}$", extract_as="file_date_full")
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/capture-rules/partials/list?kb=test")
    assert r.status_code == 200
    assert r"^\d{8}$" in r.text
    assert "file_date_full" in r.text


def test_form_partial_new_mode(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/capture-rules/partials/form?kb=test")
    assert r.status_code == 200
    assert "Add rule" in r.text
    assert 'value=""' in r.text or 'value=""' in r.text


def test_form_partial_edit_mode(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_rule(kb_path, pattern=r"^\d{6}$", extract_as="file_date_short")
    client = _make_client(corpus_path, kb_path)
    r = client.get(f"/knowledge/capture-rules/partials/form?kb=test&rule_id={rule_id}")
    assert r.status_code == 200
    assert "Edit rule" in r.text
    assert r"^\d{6}$" in r.text
    assert "file_date_short" in r.text


def test_edit_rule_updates_db(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_rule(kb_path, pattern=r"^\d{8}$", extract_as="file_date")
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/capture-rules/{rule_id}/edit?kb=test", data={
        "pattern": r"^\d{8}$",
        "extract_as": "file_date_full",
        "label": "Updated label",
        "value_type": "date",
        "date_precision": "day",
        "format_str": "",
        "keep_token": "false",
    })
    assert r.status_code == 200
    assert "updated" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM capture_rules WHERE id=?", (rule_id,)).fetchone()
    conn.close()
    assert row["extract_as"] == "file_date_full"
    assert row["label"] == "Updated label"


def test_delete_rule_removes_from_db(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_rule(kb_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/capture-rules/{rule_id}/delete?kb=test")
    assert r.status_code == 200
    assert "deleted" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT id FROM capture_rules WHERE id=?", (rule_id,)).fetchone()
    conn.close()
    assert row is None
