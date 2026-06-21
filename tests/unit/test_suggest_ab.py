"""Unit tests for Stage 4 (Suggest) — NPMI computation and pure logic."""
import math


def _npmi(term_counts, pair_counts, doc_count):
    from src.stages.suggest import _compute_npmi
    return _compute_npmi(term_counts, pair_counts, doc_count)


# ---------------------------------------------------------------------------
# NPMI computation
# ---------------------------------------------------------------------------

def test_npmi_known_values():
    """3 docs: a+b always co-occur; a+c never; b+c never."""
    term_counts = {"a": 3, "b": 3, "c": 1}
    pair_counts = {("a", "b"): 3, ("a", "c"): 0, ("b", "c"): 0}
    scores = _npmi(term_counts, pair_counts, doc_count=3)
    # a-b: perfect co-occurrence → NPMI near 1.0
    assert ("a", "b") in scores
    assert scores[("a", "b")] > 0.9


def test_npmi_zero_cooccurrence():
    """Pairs with zero co-occurrence count are excluded from output."""
    term_counts = {"x": 2, "y": 2}
    pair_counts = {("x", "y"): 0}
    scores = _npmi(term_counts, pair_counts, doc_count=3)
    assert ("x", "y") not in scores


def test_npmi_perfect_cooccurrence():
    """Term always appears with another → NPMI should be 1.0."""
    term_counts = {"p": 3, "q": 3}
    pair_counts = {("p", "q"): 3}
    scores = _npmi(term_counts, pair_counts, doc_count=3)
    assert math.isclose(scores[("p", "q")], 1.0, abs_tol=1e-9)


def test_npmi_partial_cooccurrence():
    """m in 4/6 docs, n in 4/6 docs, co-occur in 3/6 → NPMI > 0 but < 1."""
    # p_m=4/6, p_n=4/6, p_mn=3/6=0.5 → p_mn > p_m*p_n (0.444) → positive NPMI
    term_counts = {"m": 4, "n": 4}
    pair_counts = {("m", "n"): 3}
    scores = _npmi(term_counts, pair_counts, doc_count=6)
    score = scores[("m", "n")]
    assert 0 < score < 1.0


# ---------------------------------------------------------------------------
# Candidate DB functions
# ---------------------------------------------------------------------------

def test_delete_pending_preserves_accepted(tmp_path):
    from src.db.corpus import delete_pending_candidates, open_corpus, upsert_candidate

    db = tmp_path / "corpus.db"
    conn = open_corpus(db)
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/a.jpg', 'a.jpg', '.jpg', 'image', 1, 0.0)"
    )
    conn.commit()

    upsert_candidate(conn, 1, "highway", "level_a")
    upsert_candidate(conn, None, "gravel", "level_b")
    conn.execute("UPDATE candidates SET status='accepted' WHERE term='highway'")
    conn.commit()

    deleted = delete_pending_candidates(conn)
    conn.commit()

    assert deleted == 1
    remaining = conn.execute("SELECT term, status FROM candidates").fetchall()
    assert len(remaining) == 1
    assert remaining[0]["term"] == "highway"
    assert remaining[0]["status"] == "accepted"
    conn.close()


def test_delete_pending_source_filter(tmp_path):
    from src.db.corpus import delete_pending_candidates, open_corpus, upsert_candidate

    db = tmp_path / "corpus.db"
    conn = open_corpus(db)
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/a.jpg', 'a.jpg', '.jpg', 'image', 1, 0.0)"
    )
    conn.commit()

    upsert_candidate(conn, 1, "highway", "level_a")
    upsert_candidate(conn, None, "bridge", "level_b")
    conn.commit()

    deleted = delete_pending_candidates(conn, source_filter="level_b")
    conn.commit()

    assert deleted == 1
    remaining = conn.execute("SELECT term FROM candidates").fetchall()
    assert len(remaining) == 1
    assert remaining[0]["term"] == "highway"
    conn.close()


def test_iter_file_term_sets_streams(tmp_path):
    from src.db.corpus import iter_file_term_sets, open_corpus, upsert_candidate

    db = tmp_path / "corpus.db"
    conn = open_corpus(db)
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    for i in range(3):
        conn.execute(
            "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (1, '/f{i}.jpg', 'f{i}.jpg', '.jpg', 'image', 1, 0.0)"
        )
    conn.commit()

    upsert_candidate(conn, 1, "highway", "level_a")
    upsert_candidate(conn, 1, "bridge", "level_a")
    upsert_candidate(conn, 2, "highway", "level_a")
    upsert_candidate(conn, 3, "river", "level_a")
    conn.commit()

    sets = list(iter_file_term_sets(conn))
    conn.close()

    assert len(sets) == 3
    assert {"highway", "bridge"} in sets
    assert {"highway"} in sets
    assert {"river"} in sets


def test_get_candidate_counts(tmp_path):
    from src.db.corpus import (
        get_candidate_counts,
        open_corpus,
        set_candidate_status,
        upsert_candidate,
    )

    db = tmp_path / "corpus.db"
    conn = open_corpus(db)
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/a.jpg', 'a.jpg', '.jpg', 'image', 1, 0.0)"
    )
    conn.commit()

    upsert_candidate(conn, 1, "highway", "level_a")
    upsert_candidate(conn, 1, "bridge", "level_a")
    upsert_candidate(conn, None, "gravel road", "level_b")
    set_candidate_status(conn, 1, "accepted")
    conn.commit()

    counts = get_candidate_counts(conn)
    conn.close()

    assert counts["total"] == 3
    assert counts["accepted"] == 1
    assert counts["pending"] == 2


# ---------------------------------------------------------------------------
# Vocabulary DB functions
# ---------------------------------------------------------------------------

def test_add_and_get_vocabulary_terms(tmp_path):
    from src.db.kb import add_vocabulary_term, get_vocabulary_terms, open_kb

    db = tmp_path / "knowledge.db"
    conn = open_kb(db)
    add_vocabulary_term(conn, "highway")
    add_vocabulary_term(conn, "bridge")
    conn.commit()

    terms = get_vocabulary_terms(conn)
    conn.close()

    term_names = {t["term"] for t in terms}
    assert "highway" in term_names
    assert "bridge" in term_names


def test_add_vocabulary_term_is_idempotent(tmp_path):
    from src.db.kb import add_vocabulary_term, get_vocabulary_terms, open_kb

    db = tmp_path / "knowledge.db"
    conn = open_kb(db)
    add_vocabulary_term(conn, "highway")
    add_vocabulary_term(conn, "highway")
    conn.commit()

    terms = get_vocabulary_terms(conn)
    conn.close()
    assert len([t for t in terms if t["term"] == "highway"]) == 1


def test_delete_vocabulary_term(tmp_path):
    from src.db.kb import add_vocabulary_term, delete_vocabulary_term, get_vocabulary_terms, open_kb

    db = tmp_path / "knowledge.db"
    conn = open_kb(db)
    add_vocabulary_term(conn, "highway")
    conn.commit()
    delete_vocabulary_term(conn, "highway")
    conn.commit()

    terms = get_vocabulary_terms(conn)
    conn.close()
    assert not any(t["term"] == "highway" for t in terms)


def test_get_stoplist_terms(tmp_path):
    from src.db.kb import add_to_stoplist, get_stoplist_terms, open_kb

    db = tmp_path / "knowledge.db"
    conn = open_kb(db)
    add_to_stoplist(conn, "photo")
    add_to_stoplist(conn, "image")
    conn.commit()

    terms = get_stoplist_terms(conn)
    conn.close()
    assert "photo" in terms
    assert "image" in terms


# ---------------------------------------------------------------------------
# has_level_b_clusters
# ---------------------------------------------------------------------------

def test_has_level_b_clusters_false_when_empty(tmp_path):
    from src.db.corpus import has_level_b_clusters, open_corpus
    conn = open_corpus(tmp_path / "corpus.db")
    assert has_level_b_clusters(conn) is False
    conn.close()


def test_has_level_b_clusters_true_when_level_b_exists(tmp_path):
    from src.db.corpus import has_level_b_clusters, open_corpus, upsert_candidate
    conn = open_corpus(tmp_path / "corpus.db")
    upsert_candidate(conn, None, "bridge", "level_b", cluster_id="0")
    conn.commit()
    assert has_level_b_clusters(conn) is True
    conn.close()


def test_has_level_b_clusters_ignores_level_a(tmp_path):
    from src.db.corpus import has_level_b_clusters, open_corpus, upsert_candidate
    conn = open_corpus(tmp_path / "corpus.db")
    upsert_candidate(conn, None, "bridge", "level_a")
    conn.commit()
    assert has_level_b_clusters(conn) is False
    conn.close()
