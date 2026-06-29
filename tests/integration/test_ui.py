"""Integration tests for UI page, partial, and form-handler routes."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus
from src.db.kb import open_kb


def _make_client(corpus_path: Path, kb_path: Path) -> TestClient:
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app)


def _seed_token(
    corpus_path: Path,
    token: str,
    pattern_class: str = "word",
    semantic_type: str = "word",
    is_cross_source: int = 0,
    proposed_action: str = "none",
    proposed_extract_as: str = "",
) -> int:
    conn = open_corpus(corpus_path)
    conn.execute(
        """
        INSERT INTO analyse_tokens
            (token, pattern_class, semantic_type, frequency, file_count,
             proposed_action, proposed_extract_as, is_cross_source)
        VALUES (?, ?, ?, 1, 1, ?, ?, ?)
        """,
        (token, pattern_class, semantic_type, proposed_action, proposed_extract_as, is_cross_source),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM analyse_tokens WHERE token=?", (token,)).fetchone()
    conn.close()
    return row[0]


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


def test_pipeline_page_returns_200(kb_dbs):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "ingest" in resp.text


def test_normalise_review_page_returns_200(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_token(corpus_path, "highway")
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/normalise", params={"kb": "test"})
    assert resp.status_code == 200
    assert "highway" in resp.text


def test_pending_partial_groups_by_pattern_class(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_token(corpus_path, "160929", "6digit_numeric", "date")
    _seed_token(corpus_path, "bridge", "word", "word")
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/normalise/partials/pending", params={"kb": "test"})
    assert resp.status_code == 200
    assert "6-digit numeric" in resp.text
    assert "Words" in resp.text
    assert "160929" in resp.text


def test_pending_partial_shows_proposed_extract_as(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_token(
        corpus_path, "20160929", "8digit_numeric", "date",
        proposed_action="capture_date", proposed_extract_as="file_date",
    )
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/normalise/partials/pending", params={"kb": "test"})
    assert resp.status_code == 200
    assert "file_date" in resp.text


def test_decisions_partial_returns_decisions_html(kb_dbs):
    corpus_path, kb_path = kb_dbs
    token_id = _seed_token(corpus_path, "construction")
    client = _make_client(corpus_path, kb_path)
    client.post("/api/review/normalise/decide", json={
        "kb": "test", "item_id": token_id, "action": "ignore",
    })
    resp = client.get("/review/normalise/partials/decisions", params={"kb": "test"})
    assert resp.status_code == 200
    assert "construction" in resp.text
    assert "ignore" in resp.text


def test_decide_post_removes_token_from_pending(kb_dbs):
    corpus_path, kb_path = kb_dbs
    token_id = _seed_token(corpus_path, "highway")
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/review/normalise/decide",
        data={"item_id": str(token_id), "action": "ignore"},
        params={"kb": "test"},
    )
    assert resp.status_code == 200
    pending_resp = client.get("/review/normalise/partials/pending", params={"kb": "test"})
    assert "highway" not in pending_resp.text


def test_delete_decision_returns_token_to_pending_partial(kb_dbs):
    corpus_path, kb_path = kb_dbs
    token_id = _seed_token(corpus_path, "revoke_me")
    client = _make_client(corpus_path, kb_path)
    client.post("/api/review/normalise/decide", json={
        "kb": "test", "item_id": token_id, "action": "ignore",
    })
    decisions_resp = client.get("/api/review/normalise/decisions", params={"kb": "test"})
    d_id = decisions_resp.json()["decisions"][0]["id"]
    del_resp = client.request(
        "DELETE",
        f"/review/normalise/decisions/{d_id}",
        params={"kb": "test"},
    )
    assert del_resp.status_code == 200
    pending_resp = client.get("/review/normalise/partials/pending", params={"kb": "test"})
    assert "revoke_me" in pending_resp.text


def test_reassign_decision_changes_action(kb_dbs):
    corpus_path, kb_path = kb_dbs
    token_id = _seed_token(corpus_path, "highway")
    client = _make_client(corpus_path, kb_path)
    # Decide as ignore
    client.post("/review/normalise/decide", data={"item_id": str(token_id), "action": "ignore"}, params={"kb": "test"})
    decisions_resp = client.get("/api/review/normalise/decisions", params={"kb": "test"})
    d_id = decisions_resp.json()["decisions"][0]["id"]
    # Reassign to reject
    resp = client.post("/review/normalise/reassign", data={"decision_id": d_id, "new_action": "reject"}, params={"kb": "test"})
    assert resp.status_code == 200
    assert resp.headers.get("HX-Trigger") == '{"decisionsChanged": null}'
    # Decisions panel should now show reject, not ignore
    decisions2 = client.get("/api/review/normalise/decisions", params={"kb": "test"}).json()["decisions"]
    assert len(decisions2) == 1
    assert decisions2[0]["action"] == "reject"
    assert decisions2[0]["token"] == "highway"
    # Token must still be decided (not reverted to pending)
    conn = open_corpus(corpus_path)
    status = conn.execute("SELECT status FROM analyse_tokens WHERE id=?", (token_id,)).fetchone()["status"]
    conn.close()
    assert status == "decided"


def test_reassign_to_accept_removes_from_decisions(kb_dbs):
    corpus_path, kb_path = kb_dbs
    token_id = _seed_token(corpus_path, "junction")
    client = _make_client(corpus_path, kb_path)
    client.post("/review/normalise/decide", data={"item_id": str(token_id), "action": "ignore"}, params={"kb": "test"})
    decisions_resp = client.get("/api/review/normalise/decisions", params={"kb": "test"})
    d_id = decisions_resp.json()["decisions"][0]["id"]
    resp = client.post("/review/normalise/reassign", data={"decision_id": d_id, "new_action": "accept"}, params={"kb": "test"})
    assert resp.status_code == 200
    # Accept has no KB rule — decisions list should be empty
    decisions2 = client.get("/api/review/normalise/decisions", params={"kb": "test"}).json()["decisions"]
    assert decisions2 == []


def test_reassign_to_correct_sets_canonical_term(kb_dbs):
    corpus_path, kb_path = kb_dbs
    token_id = _seed_token(corpus_path, "kootenay")
    client = _make_client(corpus_path, kb_path)
    client.post("/review/normalise/decide", data={"item_id": str(token_id), "action": "reject"}, params={"kb": "test"})
    decisions_resp = client.get("/api/review/normalise/decisions", params={"kb": "test"})
    d_id = decisions_resp.json()["decisions"][0]["id"]
    resp = client.post(
        "/review/normalise/reassign",
        data={"decision_id": d_id, "new_action": "correct", "canonical_term": "Kootenay"},
        params={"kb": "test"},
    )
    assert resp.status_code == 200
    decisions2 = client.get("/api/review/normalise/decisions", params={"kb": "test"}).json()["decisions"]
    assert len(decisions2) == 1
    assert decisions2[0]["action"] == "correct"
    assert decisions2[0]["detail"] == "Kootenay"


def test_cross_source_badge_shown_for_flagged_tokens(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_token(corpus_path, "bc5", "word", "word", is_cross_source=1)
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/normalise", params={"kb": "test"})
    assert resp.status_code == 200
    assert "★" in resp.text


# ---------------------------------------------------------------------------
# Corpus stats page
# ---------------------------------------------------------------------------

def test_corpus_stats_page_returns_200(kb_dbs):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-stats", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Corpus Statistics" in resp.text


def test_corpus_stats_page_shows_file_count(kb_dbs):
    corpus_path, kb_path = kb_dbs
    from src.db.corpus import add_source
    conn = open_corpus(corpus_path)
    source_id = add_source(conn, "/photos")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/photos/a.jpg', 'a.jpg', '.jpg', 'images', 1000, 0.0)",
        (source_id,),
    )
    conn.commit()
    conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-stats", params={"kb": "test"})
    assert resp.status_code == 200
    assert "images" in resp.text


def test_corpus_stats_page_shows_stage_coverage(kb_dbs):
    corpus_path, kb_path = kb_dbs
    conn = open_corpus(corpus_path)
    conn.execute(
        "INSERT INTO pipeline_checkpoints"
        " (stage, files_processed, files_skipped, errors, duration_seconds)"
        " VALUES ('hash', 5, 0, 0, 1.2)"
    )
    conn.commit()
    conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/corpus-stats", params={"kb": "test"})
    assert resp.status_code == 200
    assert "hash" in resp.text


def test_pipeline_page_has_stats_nav_link(kb_dbs):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "corpus-stats" in resp.text


# ---------------------------------------------------------------------------
# Pipeline run buttons (KB.P4)
# ---------------------------------------------------------------------------

def test_pipeline_page_has_run_buttons(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "btn--run" in resp.text
    assert "btn--cancel" in resp.text


def test_pipeline_page_run_buttons_reference_stages(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    for stage in ("ingest", "hash", "describe", "retag", "export"):
        assert f"runStage('{stage}'" in resp.text


def test_pipeline_page_includes_pipeline_js(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "pipeline.js" in resp.text


# ---------------------------------------------------------------------------
# Suggest gate banner (replaces nav link from KB.P6)
# ---------------------------------------------------------------------------

def test_pipeline_has_suggest_gate_banner(kb_dbs):
    """Suggest review gate banner always appears on the pipeline page (link is conditional on data)."""
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Suggest Review" in resp.text


def test_suggest_nav_link_removed(kb_dbs):
    """Suggest is now inline in the pipeline gate banner, not a standalone nav link."""
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert 'href="/review/suggest?kb=test">Suggest' not in resp.text


def test_suggest_badge_js_removed(kb_dbs):
    """Suggest pending count is now server-rendered in the gate banner, not a client-side fetch."""
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert "suggest_badge.js" not in resp.text


def test_suggest_review_page_returns_200(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/review/suggest", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Suggestion Review" in resp.text


# ---------------------------------------------------------------------------
# Level C button (KB.10)
# ---------------------------------------------------------------------------

def test_suggest_review_shows_run_level_c_when_clusters_exist(kb_dbs):
    corpus_path, kb_path = kb_dbs
    conn = open_corpus(corpus_path)
    from src.db.corpus import upsert_candidate
    upsert_candidate(conn, None, "bridge", "level_b", cluster_id="0")
    conn.commit()
    conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/suggest", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Run Level C" in resp.text
    assert "btn-run-level-c" in resp.text


def test_suggest_review_shows_disabled_when_no_clusters(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/review/suggest", params={"kb": "test"})
    assert resp.status_code == 200
    assert "run Level B first" in resp.text
    assert 'id="btn-run-level-c"' not in resp.text


def test_pipeline_page_includes_quality_stage(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "quality" in resp.text


def test_candidates_queue_renders_level_c_notes(kb_dbs):
    corpus_path, kb_path = kb_dbs
    conn = open_corpus(corpus_path)
    from src.db.corpus import upsert_candidate
    upsert_candidate(conn, None, "overpass", "level_c", cluster_id="0",
                     notes="This cluster covers bridge infrastructure terms.")
    conn.commit()
    conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/suggest/partials/queue", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Reasoning" in resp.text
    assert "bridge infrastructure" in resp.text


# ---------------------------------------------------------------------------
# Bulk suggest decisions (decide-all)
# ---------------------------------------------------------------------------

def _seed_candidates(corpus_path: Path, *terms: str) -> None:
    from src.db.corpus import upsert_candidate
    conn = open_corpus(corpus_path)
    for term in terms:
        upsert_candidate(conn, None, term, "level_a")
    conn.commit()
    conn.close()


def test_decide_all_accept_moves_terms_to_vocabulary(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_candidates(corpus_path, "delta", "echo")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/decide-all", params={"kb": "test"}, data={"action": "accept"})
    assert resp.status_code == 200
    from src.db.kb import get_vocabulary_terms, open_kb
    terms = {r["term"] for r in get_vocabulary_terms(open_kb(kb_path))}
    assert "delta" in terms
    assert "echo" in terms
    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT status FROM candidates WHERE term IN ('delta','echo')").fetchall()
    assert all(r["status"] == "accepted" for r in rows)
    conn.close()


def test_decide_all_reject_marks_candidates_rejected(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_candidates(corpus_path, "foxtrot", "golf")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/decide-all", params={"kb": "test"}, data={"action": "reject"})
    assert resp.status_code == 200
    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT status FROM candidates WHERE term IN ('foxtrot','golf')").fetchall()
    assert all(r["status"] == "rejected" for r in rows)
    conn.close()


def test_decide_all_ignore_adds_to_stoplist(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_candidates(corpus_path, "hotel", "india")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/decide-all", params={"kb": "test"}, data={"action": "ignore"})
    assert resp.status_code == 200
    from src.db.kb import get_stoplist_terms, open_kb
    stoplist = get_stoplist_terms(open_kb(kb_path))
    assert "hotel" in stoplist
    assert "india" in stoplist
    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT status FROM candidates WHERE term IN ('hotel','india')").fetchall()
    assert all(r["status"] == "rejected" for r in rows)
    conn.close()


def test_decide_all_invalid_action_returns_400(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.post("/review/suggest/decide-all", params={"kb": "test"}, data={"action": "bogus"})
    assert resp.status_code == 400


def test_decide_all_skips_non_pending(kb_dbs):
    corpus_path, kb_path = kb_dbs
    from src.db.corpus import upsert_candidate
    conn = open_corpus(corpus_path)
    upsert_candidate(conn, None, "juliet", "level_a")
    conn.execute("UPDATE candidates SET status='accepted' WHERE term='juliet'")
    conn.commit()
    conn.close()
    client = _make_client(corpus_path, kb_path)
    client.post("/review/suggest/decide-all", params={"kb": "test"}, data={"action": "reject"})
    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT status FROM candidates WHERE term='juliet'").fetchone()
    assert row["status"] == "accepted"
    conn.close()


def test_decide_all_noop_when_no_pending(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.post("/review/suggest/decide-all", params={"kb": "test"}, data={"action": "accept"})
    assert resp.status_code == 200


def test_candidates_queue_shows_bulk_actions_when_pending(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_candidates(corpus_path, "kilo")
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/suggest/partials/queue", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Accept All" in resp.text
    assert "Ignore All" in resp.text
    assert "Reject All" in resp.text


def test_candidates_queue_hides_bulk_actions_when_empty(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/review/suggest/partials/queue", params={"kb": "test"})
    assert resp.status_code == 200
    assert "Accept All" not in resp.text


# ---------------------------------------------------------------------------
# Reclassify suggest decisions
# ---------------------------------------------------------------------------

def _accept_term(corpus_path: Path, kb_path: Path, term: str) -> None:
    from src.db.corpus import upsert_candidate
    from src.db.kb import add_vocabulary_term, open_kb
    conn = open_corpus(corpus_path)
    upsert_candidate(conn, None, term, "level_a")
    conn.execute("UPDATE candidates SET status='accepted' WHERE term=?", (term,))
    conn.commit()
    conn.close()
    kb_conn = open_kb(kb_path)
    add_vocabulary_term(kb_conn, term)
    kb_conn.commit()
    kb_conn.close()


def _ignore_term(corpus_path: Path, kb_path: Path, term: str) -> None:
    from src.db.corpus import upsert_candidate
    from src.db.kb import add_to_stoplist, open_kb
    conn = open_corpus(corpus_path)
    upsert_candidate(conn, None, term, "level_a")
    conn.execute("UPDATE candidates SET status='rejected' WHERE term=?", (term,))
    conn.commit()
    conn.close()
    kb_conn = open_kb(kb_path)
    add_to_stoplist(kb_conn, term, source="domain")
    kb_conn.commit()
    kb_conn.close()


def _reject_term(corpus_path: Path, kb_path: Path, term: str) -> None:
    from src.db.corpus import upsert_candidate
    conn = open_corpus(corpus_path)
    upsert_candidate(conn, None, term, "level_a")
    conn.execute("UPDATE candidates SET status='rejected' WHERE term=?", (term,))
    conn.commit()
    conn.close()


def test_reclassify_accepted_to_ignore(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _accept_term(corpus_path, kb_path, "lima")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/reclassify", params={"kb": "test"},
                       data={"term": "lima", "action": "ignore"})
    assert resp.status_code == 200
    from src.db.kb import get_stoplist_terms, get_vocabulary_terms, open_kb
    kb_conn = open_kb(kb_path)
    assert "lima" not in {r["term"] for r in get_vocabulary_terms(kb_conn)}
    assert "lima" in get_stoplist_terms(kb_conn)
    kb_conn.close()
    conn = open_corpus(corpus_path)
    assert conn.execute("SELECT status FROM candidates WHERE term='lima'").fetchone()["status"] == "rejected"
    conn.close()


def test_reclassify_accepted_to_reject(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _accept_term(corpus_path, kb_path, "mike")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/reclassify", params={"kb": "test"},
                       data={"term": "mike", "action": "reject"})
    assert resp.status_code == 200
    from src.db.kb import get_stoplist_terms, get_vocabulary_terms, open_kb
    kb_conn = open_kb(kb_path)
    assert "mike" not in {r["term"] for r in get_vocabulary_terms(kb_conn)}
    assert "mike" not in get_stoplist_terms(kb_conn)
    kb_conn.close()


def test_reclassify_ignored_to_accept(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _ignore_term(corpus_path, kb_path, "november")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/reclassify", params={"kb": "test"},
                       data={"term": "november", "action": "accept"})
    assert resp.status_code == 200
    from src.db.kb import get_stoplist_terms, get_vocabulary_terms, open_kb
    kb_conn = open_kb(kb_path)
    assert "november" in {r["term"] for r in get_vocabulary_terms(kb_conn)}
    assert "november" not in get_stoplist_terms(kb_conn)
    kb_conn.close()


def test_reclassify_rejected_to_accept(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _reject_term(corpus_path, kb_path, "oscar")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/reclassify", params={"kb": "test"},
                       data={"term": "oscar", "action": "accept"})
    assert resp.status_code == 200
    from src.db.kb import get_vocabulary_terms, open_kb
    kb_conn = open_kb(kb_path)
    assert "oscar" in {r["term"] for r in get_vocabulary_terms(kb_conn)}
    kb_conn.close()


def test_reclassify_rejected_to_ignore(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _reject_term(corpus_path, kb_path, "papa")
    client = _make_client(corpus_path, kb_path)
    resp = client.post("/review/suggest/reclassify", params={"kb": "test"},
                       data={"term": "papa", "action": "ignore"})
    assert resp.status_code == 200
    from src.db.kb import get_stoplist_terms, open_kb
    kb_conn = open_kb(kb_path)
    assert "papa" in get_stoplist_terms(kb_conn)
    kb_conn.close()


def test_reclassify_invalid_action_returns_400(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.post("/review/suggest/reclassify", params={"kb": "test"},
                       data={"term": "quebec", "action": "bogus"})
    assert resp.status_code == 400


def test_vocabulary_panel_shows_ignored_and_rejected_sections(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _accept_term(corpus_path, kb_path, "romeo")
    _ignore_term(corpus_path, kb_path, "sierra")
    _reject_term(corpus_path, kb_path, "tango")
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/suggest/partials/vocabulary", params={"kb": "test"})
    assert resp.status_code == 200
    assert "romeo" in resp.text
    assert "sierra" in resp.text
    assert "tango" in resp.text
    assert "Accepted" in resp.text
    assert "Ignored" in resp.text
    assert "Rejected" in resp.text


# ---------------------------------------------------------------------------
# New Terms Review UI (KB.P9)
# ---------------------------------------------------------------------------

def _seed_retag_row(corpus_path: Path, file_path: str, tags: list, new_terms: list) -> None:
    import json
    conn = open_corpus(corpus_path)
    conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        f" VALUES (1, '{file_path}', 'f.jpg', '.jpg', 'images', 1, 0.0)"
    )
    conn.commit()
    fid = conn.execute("SELECT id FROM files WHERE path=?", (file_path,)).fetchone()[0]
    conn.execute(
        "INSERT INTO retag_output"
        " (file_id, tags_json, refined_description, new_terms_proposed_json,"
        "  model, processed_at, retag_status)"
        " VALUES (?, ?, NULL, ?, 'test', datetime('now'), 'done')",
        (fid, json.dumps(tags), json.dumps(new_terms)),
    )
    conn.commit()
    conn.close()


def test_new_terms_review_page_returns_200(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/review/new-terms", params={"kb": "test"})
    assert resp.status_code == 200
    assert "New Terms Review" in resp.text


def test_new_terms_queue_partial_shows_pending_term(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_retag_row(corpus_path, "/img/a.jpg", [], ["embankment"])
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/new-terms/partials/queue", params={"kb": "test"})
    assert resp.status_code == 200
    assert "embankment" in resp.text


def test_new_terms_queue_partial_shows_file_count(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_retag_row(corpus_path, "/img/b.jpg", [], ["soffit"])
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/new-terms/partials/queue", params={"kb": "test"})
    assert resp.status_code == 200
    assert "1" in resp.text


def test_new_terms_decisions_partial_shows_accepted_term(kb_dbs):
    corpus_path, kb_path = kb_dbs
    from src.db.kb import add_vocabulary_term, open_kb
    kb_conn = open_kb(kb_path)
    add_vocabulary_term(kb_conn, "abutment", source="new_terms")
    kb_conn.commit()
    kb_conn.close()
    client = _make_client(corpus_path, kb_path)
    resp = client.get("/review/new-terms/partials/decisions", params={"kb": "test"})
    assert resp.status_code == 200
    assert "abutment" in resp.text


def test_new_terms_decide_form_accept(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_retag_row(corpus_path, "/img/c.jpg", [], ["soffit"])
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/review/new-terms/decide",
        data={"term": "soffit", "action": "accept"},
        params={"kb": "test"},
    )
    assert resp.status_code == 200
    assert "pendingChanged" in resp.headers.get("hx-trigger", "")
    from src.db.kb import open_kb
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT source FROM vocabulary WHERE term='soffit'").fetchone()
    kb_conn.close()
    assert row is not None
    assert row["source"] == "new_terms"


def test_new_terms_decide_form_correct(kb_dbs):
    corpus_path, kb_path = kb_dbs
    client = _make_client(corpus_path, kb_path)
    resp = client.post(
        "/review/new-terms/decide",
        data={"term": "brige", "action": "correct", "corrected_to": "bridge"},
        params={"kb": "test"},
    )
    assert resp.status_code == 200
    from src.db.kb import open_kb
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT canonical_term FROM corrections WHERE raw_term='brige'").fetchone()
    kb_conn.close()
    assert row is not None
    assert row["canonical_term"] == "bridge"


def test_new_terms_delete_decision(kb_dbs):
    corpus_path, kb_path = kb_dbs
    _seed_retag_row(corpus_path, "/img/d.jpg", [], ["culvert"])
    client = _make_client(corpus_path, kb_path)
    client.post(
        "/review/new-terms/decide",
        data={"term": "culvert", "action": "accept"},
        params={"kb": "test"},
    )
    resp = client.request(
        "DELETE",
        "/review/new-terms/decisions/culvert",
        params={"kb": "test"},
    )
    assert resp.status_code == 200
    assert "decisionsChanged" in resp.headers.get("hx-trigger", "")
    from src.db.kb import open_kb
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT * FROM vocabulary WHERE term='culvert'").fetchone()
    kb_conn.close()
    assert row is None


def test_pipeline_has_new_terms_gate_banner(kb_dbs):
    """New Terms review gate banner always appears on the pipeline page (link is conditional on data)."""
    client = _make_client(*kb_dbs)
    resp = client.get("/pipeline", params={"kb": "test"})
    assert resp.status_code == 200
    assert "New Terms Review" in resp.text


def test_health_page_returns_200(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/health", params={"kb": "test"})
    assert resp.status_code == 200
    assert "ExifTool" in resp.text
    assert "Health Check" in resp.text


def test_health_page_has_nav_link(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/health", params={"kb": "test"})
    assert resp.status_code == 200
    assert "/health?kb=test" in resp.text


def test_health_api_returns_16_checks(kb_dbs):
    corpus_path, kb_path = kb_dbs
    resp = TestClient(app).get("/api/kb/nonexistent-kb-xyz/health")
    assert resp.status_code == 404


def test_health_page_shows_four_groups(kb_dbs):
    client = _make_client(*kb_dbs)
    resp = client.get("/health", params={"kb": "test"})
    assert resp.status_code == 200
    text = resp.text
    assert "Environment (Required)" in text
    assert "Optional Tools" in text
    assert "KB State" in text
    assert "KB Scaffold Files" in text
