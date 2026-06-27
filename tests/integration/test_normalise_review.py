"""Integration tests for the Normalisation Review Pattern 2 API endpoints."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import open_kb


def _make_client(corpus_path: Path, kb_path: Path) -> TestClient:
    """Return a TestClient with resolve_kb overridden to use test DBs."""
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app)


def _seed_token(corpus_path: Path, token: str, pattern_class: str = "word", semantic_type: str = "word") -> int:
    """Insert a pending token into analyse_tokens; return its id."""
    conn = open_corpus(corpus_path)
    conn.execute(
        """
        INSERT INTO analyse_tokens
            (token, pattern_class, semantic_type, frequency, file_count, proposed_action, proposed_extract_as)
        VALUES (?, ?, ?, 1, 1, 'none', '')
        """,
        (token, pattern_class, semantic_type),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM analyse_tokens WHERE token=?", (token,)).fetchone()
    conn.close()
    return row[0]


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure dependency overrides are cleaned up after each test."""
    yield
    app.dependency_overrides.pop(resolve_kb, None)


def test_pending_returns_items(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    _seed_token(corpus_path, "highway")
    _seed_token(corpus_path, "bridge")

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/review/normalise/pending", params={"kb": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) >= 2
    assert data["total"] >= 2


def test_decide_capture_writes_capture_rule(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    token_id = _seed_token(corpus_path, "160929", "6digit_numeric", "date")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/decide", json={
        "kb": "test",
        "item_id": token_id,
        "action": "capture",
        "value": {
            "pattern": r"^\d{6}$",
            "label": "date_yymmdd",
            "extract_as": "file_date",
            "format_str": "",
            "value_type": "date",
            "keep_token": False,
        },
    })
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT * FROM capture_rules").fetchone()
    kb_conn.close()
    assert row is not None
    assert row["extract_as"] == "file_date"


def test_decide_ignore_writes_stoplist(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    token_id = _seed_token(corpus_path, "construction")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/decide", json={
        "kb": "test",
        "item_id": token_id,
        "action": "ignore",
    })
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute(
        "SELECT * FROM stoplist WHERE term='construction' AND source='domain'"
    ).fetchone()
    kb_conn.close()
    assert row is not None


def test_decide_correct_writes_corrections(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    token_id = _seed_token(corpus_path, "tuckinleted", "camelcase", "compound")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/decide", json={
        "kb": "test",
        "item_id": token_id,
        "action": "correct",
        "value": {"canonical_term": "Tuck Inlet", "correction_kind": "typo"},
    })
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute(
        "SELECT * FROM corrections WHERE raw_term='tuckinleted'"
    ).fetchone()
    kb_conn.close()
    assert row is not None
    assert row["canonical_term"] == "Tuck Inlet"


def test_decide_reject_writes_reject_token(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    token_id = _seed_token(corpus_path, "2019govbc")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/decide", json={
        "kb": "test",
        "item_id": token_id,
        "action": "reject",
    })
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT * FROM reject_tokens WHERE pattern='2019govbc'").fetchone()
    kb_conn.close()
    assert row is not None


def test_decide_accept_marks_decided_with_no_kb_rule(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    token_id = _seed_token(corpus_path, "mountain")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/decide", json={
        "kb": "test",
        "item_id": token_id,
        "action": "accept",
    })
    assert resp.status_code == 200

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT status FROM analyse_tokens WHERE id=?", (token_id,)).fetchone()
    conn.close()
    assert row["status"] == "decided"

    # Accept adds no KB rules (reject_tokens, corrections, capture_rules are always empty on a fresh KB)
    kb_conn = open_kb(kb_path)
    assert kb_conn.execute("SELECT COUNT(*) FROM reject_tokens").fetchone()[0] == 0
    assert kb_conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 0
    assert kb_conn.execute("SELECT COUNT(*) FROM capture_rules").fetchone()[0] == 0
    kb_conn.close()


def test_decide_marks_token_decided(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    token_id = _seed_token(corpus_path, "backup")

    client = _make_client(corpus_path, kb_path)
    client.post("/api/review/normalise/decide", json={
        "kb": "test",
        "item_id": token_id,
        "action": "ignore",
    })

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT status FROM analyse_tokens WHERE id=?", (token_id,)).fetchone()
    conn.close()
    assert row["status"] == "decided"


def test_decisions_list_reflects_all_kinds(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    id1 = _seed_token(corpus_path, "tok_ignore")
    id2 = _seed_token(corpus_path, "tok_correct")
    id3 = _seed_token(corpus_path, "tok_reject")

    client = _make_client(corpus_path, kb_path)

    client.post("/api/review/normalise/decide", json={"kb": "test", "item_id": id1, "action": "ignore"})
    client.post("/api/review/normalise/decide", json={
        "kb": "test", "item_id": id2, "action": "correct",
        "value": {"canonical_term": "Token Correct", "correction_kind": "typo"},
    })
    client.post("/api/review/normalise/decide", json={"kb": "test", "item_id": id3, "action": "reject"})

    resp = client.get("/api/review/normalise/decisions", params={"kb": "test"})
    assert resp.status_code == 200
    actions = {d["action"] for d in resp.json()["decisions"]}
    assert "ignore" in actions
    assert "correct" in actions
    assert "reject" in actions


def test_delete_decision_reverts_token_to_pending(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    token_id = _seed_token(corpus_path, "revoke_me")

    client = _make_client(corpus_path, kb_path)

    client.post("/api/review/normalise/decide", json={
        "kb": "test",
        "item_id": token_id,
        "action": "capture",
        "value": {
            "pattern": r"^revoke_me$",
            "label": "test_rule",
            "extract_as": "test_field",
        },
    })

    resp = client.get("/api/review/normalise/decisions", params={"kb": "test"})
    decisions = resp.json()["decisions"]
    capture_decision = next(d for d in decisions if d["action"] == "capture")
    decision_id = capture_decision["id"]  # e.g. "capture_rules:1"

    del_resp = client.delete(
        f"/api/review/normalise/decisions/{decision_id}",
        params={"kb": "test"},
    )
    assert del_resp.status_code == 200

    # Token should be back to pending
    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT status FROM analyse_tokens WHERE id=?", (token_id,)).fetchone()
    conn.close()
    assert row["status"] == "pending"

    # Capture rule should be gone
    kb_conn = open_kb(kb_path)
    count = kb_conn.execute("SELECT COUNT(*) FROM capture_rules").fetchone()[0]
    kb_conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------

def test_bulk_accept_all_marks_all_decided_no_kb_rules(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    _seed_token(corpus_path, "lake")
    _seed_token(corpus_path, "river")
    _seed_token(corpus_path, "mountain")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/bulk", json={"kb": "test", "action": "accept_all"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 3

    conn = open_corpus(corpus_path)
    pending = conn.execute("SELECT COUNT(*) FROM analyse_tokens WHERE status='pending'").fetchone()[0]
    conn.close()
    assert pending == 0

    kb_conn = open_kb(kb_path)
    assert kb_conn.execute("SELECT COUNT(*) FROM reject_tokens").fetchone()[0] == 0
    assert kb_conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 0
    assert kb_conn.execute("SELECT COUNT(*) FROM capture_rules").fetchone()[0] == 0
    kb_conn.close()


def test_bulk_ignore_all_adds_all_to_stoplist(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    _seed_token(corpus_path, "jpg")
    _seed_token(corpus_path, "copy")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/bulk", json={"kb": "test", "action": "ignore_all"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2

    conn = open_corpus(corpus_path)
    pending = conn.execute("SELECT COUNT(*) FROM analyse_tokens WHERE status='pending'").fetchone()[0]
    conn.close()
    assert pending == 0

    kb_conn = open_kb(kb_path)
    rows = kb_conn.execute("SELECT term FROM stoplist WHERE term IN ('jpg','copy')").fetchall()
    kb_conn.close()
    assert {r["term"] for r in rows} == {"jpg", "copy"}


def test_bulk_reject_all_adds_all_to_reject_tokens(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    _seed_token(corpus_path, "img0042")
    _seed_token(corpus_path, "dsc9999")

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/bulk", json={"kb": "test", "action": "reject_all"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2

    conn = open_corpus(corpus_path)
    pending = conn.execute("SELECT COUNT(*) FROM analyse_tokens WHERE status='pending'").fetchone()[0]
    conn.close()
    assert pending == 0

    kb_conn = open_kb(kb_path)
    rows = kb_conn.execute("SELECT pattern FROM reject_tokens WHERE pattern IN ('img0042','dsc9999')").fetchall()
    kb_conn.close()
    assert {r["pattern"] for r in rows} == {"img0042", "dsc9999"}


def test_bulk_invalid_action_returns_400(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/bulk", json={"kb": "test", "action": "delete_everything"})
    assert resp.status_code == 400


def test_bulk_accept_all_on_empty_queue_returns_zero(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post("/api/review/normalise/bulk", json={"kb": "test", "action": "accept_all"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# get_decision_token helper
# ---------------------------------------------------------------------------

def test_get_decision_token_stoplist(tmp_path):
    from src.db.kb import add_to_stoplist, get_decision_token

    kb_path = tmp_path / "knowledge.db"
    kb_conn = open_kb(kb_path)
    add_to_stoplist(kb_conn, "construction")
    row = kb_conn.execute("SELECT rowid FROM stoplist WHERE term='construction'").fetchone()
    result = get_decision_token(kb_conn, "stoplist", row["rowid"])
    kb_conn.close()
    assert result == "construction"


def test_get_decision_token_corrections(tmp_path):
    from src.db.kb import add_correction, get_decision_token

    kb_path = tmp_path / "knowledge.db"
    kb_conn = open_kb(kb_path)
    add_correction(kb_conn, raw_term="tuckinleted", canonical_term="Tuck Inlet", correction_kind="typo")
    row = kb_conn.execute("SELECT id FROM corrections WHERE raw_term='tuckinleted'").fetchone()
    result = get_decision_token(kb_conn, "corrections", row["id"])
    kb_conn.close()
    assert result == "tuckinleted"


def test_get_decision_token_reject_tokens(tmp_path):
    from src.db.kb import add_reject_token, get_decision_token

    kb_path = tmp_path / "knowledge.db"
    kb_conn = open_kb(kb_path)
    add_reject_token(kb_conn, pattern="img0042", is_regex=False, label="img0042")
    row = kb_conn.execute("SELECT id FROM reject_tokens WHERE pattern='img0042'").fetchone()
    result = get_decision_token(kb_conn, "reject_tokens", row["id"])
    kb_conn.close()
    assert result == "img0042"


def test_get_decision_token_missing_returns_none(tmp_path):
    from src.db.kb import get_decision_token

    kb_path = tmp_path / "knowledge.db"
    kb_conn = open_kb(kb_path)
    result = get_decision_token(kb_conn, "stoplist", 9999)
    kb_conn.close()
    assert result is None
