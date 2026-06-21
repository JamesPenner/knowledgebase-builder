"""Integration tests for the New Terms Review API endpoints."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import add_vocabulary_term, open_kb


def _make_client(corpus_path: Path, kb_path: Path) -> TestClient:
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


def _seed_retag_row(corpus_conn, file_id, tags, new_terms, status="done"):
    corpus_conn.execute(
        "INSERT INTO retag_output"
        " (file_id, tags_json, refined_description, new_terms_proposed_json,"
        "  model, processed_at, retag_status)"
        " VALUES (?, ?, NULL, ?, 'test', datetime('now'), ?)",
        (file_id, json.dumps(tags), json.dumps(new_terms), status),
    )
    corpus_conn.commit()


def _seed_file(corpus_conn, path="/f1.jpg"):
    corpus_conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        f" VALUES (1, '{path}', 'f.jpg', '.jpg', 'image', 1, 0.0)"
    )
    corpus_conn.commit()
    row = corpus_conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    return row["id"]


# ---------------------------------------------------------------------------
# GET /api/review/new-terms/pending
# ---------------------------------------------------------------------------

def test_pending_empty_when_no_retag_output(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/review/new-terms/pending?kb=test-kb")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_pending_shows_new_terms_by_frequency(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()

    fid1 = _seed_file(corpus_conn, "/f1.jpg")
    fid2 = _seed_file(corpus_conn, "/f2.jpg")
    _seed_retag_row(corpus_conn, fid1, [], ["embankment", "abutment"])
    _seed_retag_row(corpus_conn, fid2, [], ["embankment"])
    corpus_conn.close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/review/new-terms/pending?kb=test-kb")
    assert resp.status_code == 200
    items = resp.json()["items"]
    terms = [i["term"] for i in items]
    assert "embankment" in terms
    assert "abutment" in terms
    # embankment appears in 2 files → should come before abutment
    assert terms.index("embankment") < terms.index("abutment")


def test_pending_excludes_existing_vocabulary_terms(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    add_vocabulary_term(kb_conn, "embankment")
    kb_conn.commit()
    kb_conn.close()

    fid = _seed_file(corpus_conn)
    _seed_retag_row(corpus_conn, fid, [], ["embankment", "soffit"])
    corpus_conn.close()

    client = _make_client(corpus_path, kb_path)
    resp = client.get("/api/review/new-terms/pending?kb=test-kb")
    items = resp.json()["items"]
    terms = [i["term"] for i in items]
    assert "embankment" not in terms
    assert "soffit" in terms


# ---------------------------------------------------------------------------
# POST /api/review/new-terms/decide — accept
# ---------------------------------------------------------------------------

def test_accept_adds_to_vocabulary(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(corpus_conn)
    _seed_retag_row(corpus_conn, fid, [], ["soffit"])
    corpus_conn.close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/review/new-terms/decide",
        json={"kb": "test-kb", "term": "soffit", "action": "accept"},
    )
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT * FROM vocabulary WHERE term='soffit'").fetchone()
    kb_conn.close()
    assert row is not None
    assert row["source"] == "new_terms"


def test_accept_merges_term_into_tags_json(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(corpus_conn)
    _seed_retag_row(corpus_conn, fid, ["bridge"], ["soffit"])
    corpus_conn.close()

    client = _make_client(corpus_path, kb_path)
    client.post(
        "/api/review/new-terms/decide",
        json={"kb": "test-kb", "term": "soffit", "action": "accept"},
    )

    corpus_conn = open_corpus(corpus_path)
    row = corpus_conn.execute(
        "SELECT tags_json FROM retag_output WHERE file_id=?", (fid,)
    ).fetchone()
    corpus_conn.close()
    tags = json.loads(row["tags_json"])
    assert "soffit" in tags
    assert "bridge" in tags


def test_accept_bumps_kb_version(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(corpus_conn)
    _seed_retag_row(corpus_conn, fid, [], ["abutment"])
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    before = kb_conn.execute("SELECT COUNT(*) FROM kb_version").fetchone()[0]
    kb_conn.close()

    client = _make_client(corpus_path, kb_path)
    client.post(
        "/api/review/new-terms/decide",
        json={"kb": "test-kb", "term": "abutment", "action": "accept"},
    )

    kb_conn = open_kb(kb_path)
    after = kb_conn.execute("SELECT COUNT(*) FROM kb_version").fetchone()[0]
    kb_conn.close()
    assert after > before


# ---------------------------------------------------------------------------
# POST /api/review/new-terms/decide — other actions
# ---------------------------------------------------------------------------

def test_ignore_adds_to_stoplist(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/review/new-terms/decide",
        json={"kb": "test-kb", "term": "photograph", "action": "ignore"},
    )
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT * FROM stoplist WHERE term='photograph'").fetchone()
    kb_conn.close()
    assert row is not None


def test_reject_adds_to_reject_tokens(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/review/new-terms/decide",
        json={"kb": "test-kb", "term": "xyzzy", "action": "reject"},
    )
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT * FROM reject_tokens WHERE pattern='xyzzy'").fetchone()
    kb_conn.close()
    assert row is not None


def test_correct_adds_correction(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/review/new-terms/decide",
        json={
            "kb": "test-kb",
            "term": "brige",
            "action": "correct",
            "value": {"correct_to": "bridge"},
        },
    )
    assert resp.status_code == 200

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT * FROM corrections WHERE raw_term='brige'").fetchone()
    kb_conn.close()
    assert row is not None
    assert row["canonical_term"] == "bridge"


def test_unknown_action_returns_400(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/api/review/new-terms/decide",
        json={"kb": "test-kb", "term": "foo", "action": "frobnicate"},
    )
    assert resp.status_code == 400
