"""Integration tests for the Pattern Rules management UI."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import add_pattern_rule, add_vocabulary_term, open_kb


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


def _seed_capture(kb_path: Path, pattern: str = r"^\d{8}$", extract_as: str = "file_date") -> int:
    conn = open_kb(kb_path)
    rule_id = add_pattern_rule(conn, pattern=pattern, action="capture", is_regex=True,
                               label="Test rule", extract_as=extract_as,
                               value_type="date", date_precision="day")
    conn.close()
    return rule_id


def _seed_reject(kb_path: Path, pattern: str = r"^\d{10}$") -> int:
    conn = open_kb(kb_path)
    rule_id = add_pattern_rule(conn, pattern=pattern, action="reject", is_regex=True, label="GUID")
    conn.close()
    return rule_id


def _seed_ignore(kb_path: Path, pattern: str = r"^dsc$") -> int:
    conn = open_kb(kb_path)
    rule_id = add_pattern_rule(conn, pattern=pattern, action="ignore", is_regex=True, label="Camera prefix")
    conn.close()
    return rule_id


def _seed_replace(kb_path: Path, pattern: str = "colour", replace_with: str = "color",
                  replace_type: str = "correction") -> int:
    conn = open_kb(kb_path)
    rule_id = add_pattern_rule(conn, pattern=pattern, action="replace", is_regex=False,
                               label="Spelling", replace_with=replace_with, replace_type=replace_type)
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
    r = client.get("/knowledge/pattern-rules?kb=test")
    assert r.status_code == 200
    assert "Pattern Rules" in r.text


def test_list_partial_empty(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/pattern-rules/partials/list?kb=test")
    assert r.status_code == 200
    assert "No pattern rules" in r.text


def test_add_capture_rule_persists(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": r"^\d{8}$",
        "action": "capture",
        "extract_as": "file_date_full",
        "label": "Date YYYYMMDD",
        "value_type": "date",
        "date_precision": "day",
        "format_str": "",
        "keep_token": "false",
        "is_regex": "true",
    })
    assert r.status_code == 200
    assert "added" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM pattern_rules WHERE extract_as='file_date_full'").fetchone()
    conn.close()
    assert row is not None
    assert row["action"] == "capture"
    assert row["value_type"] == "date"
    assert row["date_precision"] == "day"


def test_add_reject_rule_persists(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": r"^\d{10}$",
        "action": "reject",
        "label": "GUID",
        "is_regex": "true",
    })
    assert r.status_code == 200
    assert "added" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM pattern_rules WHERE pattern=?", (r"^\d{10}$",)).fetchone()
    conn.close()
    assert row is not None
    assert row["action"] == "reject"


def test_add_ignore_rule_persists(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": r"^dsc$",
        "action": "ignore",
        "label": "Camera prefix",
        "is_regex": "true",
    })
    assert r.status_code == 200
    assert "added" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM pattern_rules WHERE pattern=?", (r"^dsc$",)).fetchone()
    conn.close()
    assert row is not None
    assert row["action"] == "ignore"


def test_add_replace_rule_correction_persists(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": "colour",
        "action": "replace",
        "replace_with": "color",
        "replace_type": "correction",
        "label": "Spelling",
        "is_regex": "false",
    })
    assert r.status_code == 200
    assert "added" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM pattern_rules WHERE pattern='colour'").fetchone()
    conn.close()
    assert row is not None
    assert row["action"] == "replace"
    assert row["replace_with"] == "color"
    assert row["replace_type"] == "correction"


def test_add_replace_rule_synonym_updates_vocabulary(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    # Pre-seed the vocabulary with the canonical term
    conn = open_kb(kb_path)
    add_vocabulary_term(conn, "beach", source="test")
    conn.commit()
    conn.close()

    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": "shore",
        "action": "replace",
        "replace_with": "beach",
        "replace_type": "synonym",
        "is_regex": "false",
    })
    assert r.status_code == 200
    assert "added" in r.text.lower()
    conn = open_kb(kb_path)
    import json
    row = conn.execute("SELECT synonyms_json FROM vocabulary WHERE term='beach'").fetchone()
    conn.close()
    assert row is not None
    synonyms = json.loads(row["synonyms_json"])
    assert "shore" in synonyms


def test_add_rule_duplicate_pattern_returns_error(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _seed_capture(kb_path, pattern=r"^\d{8}$")
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": r"^\d{8}$",
        "action": "capture",
        "extract_as": "other_field",
    })
    assert r.status_code == 200
    assert "already exists" in r.text
    conn = open_kb(kb_path)
    count = conn.execute("SELECT COUNT(*) FROM pattern_rules").fetchone()[0]
    conn.close()
    assert count == 1


def test_add_capture_rule_missing_extract_as_returns_error(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": r"^\w+$",
        "action": "capture",
        "extract_as": "",
    })
    assert r.status_code == 200
    assert "required" in r.text.lower()
    conn = open_kb(kb_path)
    count = conn.execute("SELECT COUNT(*) FROM pattern_rules").fetchone()[0]
    conn.close()
    assert count == 0


def test_add_replace_rule_missing_replace_with_returns_error(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": "colour",
        "action": "replace",
        "replace_with": "",
    })
    assert r.status_code == 200
    assert "required" in r.text.lower()


def test_list_partial_shows_added_rules(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _seed_capture(kb_path, pattern=r"^\d{8}$", extract_as="file_date")
    _seed_reject(kb_path, pattern=r"^\d{10}$")
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/pattern-rules/partials/list?kb=test")
    assert r.status_code == 200
    assert r"^\d{8}$" in r.text
    assert r"^\d{10}$" in r.text
    assert "capture" in r.text
    assert "reject" in r.text


def test_list_shows_action_badges(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    _seed_ignore(kb_path)
    _seed_replace(kb_path)
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/pattern-rules/partials/list?kb=test")
    assert r.status_code == 200
    assert "ignore" in r.text
    assert "replace" in r.text


def test_form_partial_new_mode(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    r = client.get("/knowledge/pattern-rules/partials/form?kb=test")
    assert r.status_code == 200
    assert "Add rule" in r.text


def test_form_partial_edit_mode(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_capture(kb_path, pattern=r"^\d{6}$", extract_as="file_date_short")
    client = _make_client(corpus_path, kb_path)
    r = client.get(f"/knowledge/pattern-rules/partials/form?kb=test&rule_id={rule_id}")
    assert r.status_code == 200
    assert "Edit rule" in r.text
    assert r"^\d{6}$" in r.text
    assert "file_date_short" in r.text


def test_edit_rule_updates_db(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_capture(kb_path, pattern=r"^\d{8}$", extract_as="file_date")
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/pattern-rules/{rule_id}/edit?kb=test", data={
        "pattern": r"^\d{8}$",
        "action": "capture",
        "extract_as": "file_date_full",
        "label": "Updated label",
        "value_type": "date",
        "date_precision": "day",
        "is_regex": "true",
    })
    assert r.status_code == 200
    assert "updated" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM pattern_rules WHERE id=?", (rule_id,)).fetchone()
    conn.close()
    assert row["extract_as"] == "file_date_full"
    assert row["label"] == "Updated label"


def test_edit_capture_rule_to_reject(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_capture(kb_path, pattern=r"^\d{8}$", extract_as="file_date")
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/pattern-rules/{rule_id}/edit?kb=test", data={
        "pattern": r"^\d{8}$",
        "action": "reject",
        "label": "Now a reject rule",
        "is_regex": "true",
    })
    assert r.status_code == 200
    assert "updated" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT * FROM pattern_rules WHERE id=?", (rule_id,)).fetchone()
    conn.close()
    assert row["action"] == "reject"


def test_delete_rule_removes_from_db(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_capture(kb_path)
    client = _make_client(corpus_path, kb_path)
    r = client.post(f"/knowledge/pattern-rules/{rule_id}/delete?kb=test")
    assert r.status_code == 200
    assert "deleted" in r.text.lower()
    conn = open_kb(kb_path)
    row = conn.execute("SELECT id FROM pattern_rules WHERE id=?", (rule_id,)).fetchone()
    conn.close()
    assert row is None


# ---------------------------------------------------------------------------
# kb_version tracking (drives staleness banners on review pages)
# ---------------------------------------------------------------------------

def test_add_rule_bumps_kb_version(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    client = _make_client(corpus_path, kb_path)
    client.post("/knowledge/pattern-rules/add?kb=test", data={
        "pattern": r"^\d{10}$", "action": "reject", "label": "GUID", "is_regex": "true",
    })
    conn = open_kb(kb_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM kb_version WHERE change_type='pattern_rule_added'"
    ).fetchone()
    conn.close()
    assert row[0] == 1


def test_edit_rule_bumps_kb_version(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_capture(kb_path)
    client = _make_client(corpus_path, kb_path)
    client.post(f"/knowledge/pattern-rules/{rule_id}/edit?kb=test", data={
        "pattern": r"^\d{8}$", "action": "reject", "label": "Now reject", "is_regex": "true",
    })
    conn = open_kb(kb_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM kb_version WHERE change_type='pattern_rule_updated'"
    ).fetchone()
    conn.close()
    assert row[0] == 1


def test_delete_rule_bumps_kb_version(tmp_path):
    corpus_path, kb_path = _open_dbs(tmp_path)
    rule_id = _seed_capture(kb_path)
    client = _make_client(corpus_path, kb_path)
    client.post(f"/knowledge/pattern-rules/{rule_id}/delete?kb=test")
    conn = open_kb(kb_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM kb_version WHERE change_type='pattern_rule_deleted'"
    ).fetchone()
    conn.close()
    assert row[0] == 1
