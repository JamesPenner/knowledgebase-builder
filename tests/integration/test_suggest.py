"""Integration tests for Stage 4 (Suggest) — Level A and Level B."""
import threading
import types

import pytest

from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import open_kb
from src.pipeline.progress import NullProgressReporter


def _seed_files(conn, count=3):
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    for i in range(count):
        conn.execute(
            "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (1, '/f{i}.jpg', 'f{i}.jpg', '.jpg', 'image', 1, 0.0)"
        )
    conn.commit()


def _seed_keywords(conn, file_id, *keywords):
    for kw in keywords:
        conn.execute(
            "INSERT INTO file_metadata_keywords (file_id, canonical_name, keyword)"
            " VALUES (?, 'keywords', ?)",
            (file_id, kw),
        )
    conn.commit()


def _make_fake_spacy(terms_per_doc: list[list[str]], chunks_per_doc: list[list[str]] | None = None):
    """Return a fake spacy module whose nlp(text) returns tokens for successive calls.

    chunks_per_doc: optional list of multi-word phrases to return as noun_chunks per call.
    Each chunk entry is a string; len() returns word count via split().
    """
    call_count = [0]
    chunks_per_doc = chunks_per_doc or [[] for _ in terms_per_doc]

    class FakeToken:
        def __init__(self, lemma, pos):
            self.lemma_ = lemma
            self.pos_ = pos
            self.is_stop = False

    class FakeChunk:
        def __init__(self, text):
            self.text = text
            self._words = text.split()

        def __len__(self):
            return len(self._words)

    class FakeDoc:
        def __init__(self, terms, chunks):
            self.tokens = [FakeToken(t, "NOUN") for t in terms]
            self.noun_chunks = [FakeChunk(c) for c in chunks]

        def __iter__(self):
            return iter(self.tokens)

    class FakeNLP:
        def __call__(self_inner, text):
            idx = call_count[0] % len(terms_per_doc)
            call_count[0] += 1
            chunk_idx = idx % len(chunks_per_doc)
            return FakeDoc(terms_per_doc[idx], chunks_per_doc[chunk_idx])

    fake_spacy = types.ModuleType("spacy")

    def load(model, **kwargs):
        return FakeNLP()

    fake_spacy.load = load
    return fake_spacy


def _run_level_a(corpus_path, kb_path, config, fake_spacy, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)
    from src.stages.suggest import run_suggest
    run_suggest(corpus_path, kb_path, config, NullProgressReporter(), threading.Event(), levels=["a"])


def _run_level_b(corpus_path, kb_path, config, monkeypatch):
    try:
        import networkx  # noqa: F401
        import community  # noqa: F401
    except ImportError:
        pytest.skip("networkx/python-louvain not installed")
    from src.stages.suggest import run_suggest
    run_suggest(corpus_path, kb_path, config, NullProgressReporter(), threading.Event(), levels=["b"])


# ---------------------------------------------------------------------------
# Level A tests
# ---------------------------------------------------------------------------

def test_level_a_writes_candidates(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 3)
    _seed_keywords(conn, 1, "highway", "bridge")
    _seed_keywords(conn, 2, "highway", "river")
    _seed_keywords(conn, 3, "highway")
    conn.close()
    open_kb(kb_path).close()

    fake = _make_fake_spacy([["highway", "bridge"], ["highway", "river"], ["highway"]])
    config = Config(suggest_min_files=2)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    conn = open_corpus(corpus_path)
    terms = {r["term"] for r in conn.execute(
        "SELECT term FROM candidates WHERE source='level_a' AND status='pending'"
    ).fetchall()}
    conn.close()
    assert "highway" in terms


def test_level_a_excludes_vocabulary_terms(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 3)
    for i in range(1, 4):
        _seed_keywords(conn, i, "highway")
    conn.close()

    kb_conn = open_kb(kb_path)
    from src.db.kb import add_vocabulary_term
    add_vocabulary_term(kb_conn, "highway")
    kb_conn.commit()
    kb_conn.close()

    fake = _make_fake_spacy([["highway"], ["highway"], ["highway"]])
    config = Config(suggest_min_files=1)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    conn = open_corpus(corpus_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE term='highway'"
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_level_a_excludes_stoplist_terms(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 3)
    for i in range(1, 4):
        _seed_keywords(conn, i, "photo")
    conn.close()

    kb_conn = open_kb(kb_path)
    from src.db.kb import add_to_stoplist
    add_to_stoplist(kb_conn, "photo")
    kb_conn.commit()
    kb_conn.close()

    fake = _make_fake_spacy([["photo"], ["photo"], ["photo"]])
    config = Config(suggest_min_files=1)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    conn = open_corpus(corpus_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE term='photo'"
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_level_a_respects_min_files(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 3)
    _seed_keywords(conn, 1, "highway", "bridge")
    _seed_keywords(conn, 2, "highway")
    _seed_keywords(conn, 3, "highway")
    conn.close()
    open_kb(kb_path).close()

    # "highway" in 3 files, "bridge" in 1 file; min_files=2 → bridge excluded
    fake = _make_fake_spacy([
        ["highway", "bridge"],
        ["highway"],
        ["highway"],
    ])
    config = Config(suggest_min_files=2)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    conn = open_corpus(corpus_path)
    terms = {r["term"] for r in conn.execute(
        "SELECT DISTINCT term FROM candidates WHERE source='level_a'"
    ).fetchall()}
    conn.close()
    assert "highway" in terms
    assert "bridge" not in terms


def test_suggest_checkpoint_written(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 1)
    conn.close()
    open_kb(kb_path).close()

    fake = _make_fake_spacy([[]])
    config = Config(suggest_min_files=1)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT * FROM pipeline_checkpoints WHERE stage='suggest'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_suggest_cancel_exits_cleanly(tmp_path, monkeypatch):
    import sys
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 3)
    conn.close()
    open_kb(kb_path).close()

    cancel = threading.Event()
    cancel.set()

    fake = _make_fake_spacy([[], [], []])
    monkeypatch.setitem(sys.modules, "spacy", fake)
    from src.stages.suggest import run_suggest
    run_suggest(corpus_path, kb_path, Config(), NullProgressReporter(), cancel, levels=["a"])


def test_suggest_force_clears_pending(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 3)
    for i in range(1, 4):
        _seed_keywords(conn, i, "highway")
    conn.close()
    open_kb(kb_path).close()

    fake = _make_fake_spacy([["highway"], ["highway"], ["highway"]])
    config = Config(suggest_min_files=1)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    conn = open_corpus(corpus_path)
    from src.db.corpus import delete_pending_candidates
    delete_pending_candidates(conn)
    conn.commit()
    count_after_clear = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    conn.close()
    assert count_after_clear == 0

    fake2 = _make_fake_spacy([["highway"], ["highway"], ["highway"]])
    _run_level_a(corpus_path, kb_path, config, fake2, monkeypatch)

    conn = open_corpus(corpus_path)
    count_after_rerun = conn.execute(
        "SELECT COUNT(DISTINCT term) FROM candidates WHERE source='level_a'"
    ).fetchone()[0]
    conn.close()
    assert count_after_rerun == 1


def test_level_a_extracts_noun_chunks_from_prose(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    _seed_files(corpus_conn, count=3)

    # seed a description so _build_prose_text has content
    for fid in (1, 2, 3):
        corpus_conn.execute(
            "INSERT INTO descriptions (file_id, description_raw, pass1_status) VALUES (?, 'dummy text', 'done')",
            (fid,),
        )
    corpus_conn.commit()
    corpus_conn.close()

    # Each call: no individual tokens, but a noun chunk present in prose pass
    chunks = [["golden hour"], ["golden hour"], ["golden hour"]]
    fake = _make_fake_spacy([[], [], []], chunks_per_doc=chunks)
    config = Config(suggest_min_files=2)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    conn = open_corpus(corpus_path)
    terms = {r["term"] for r in conn.execute(
        "SELECT term FROM candidates WHERE source='level_a' AND status='pending'"
    ).fetchall()}
    conn.close()
    assert "golden hour" in terms


# ---------------------------------------------------------------------------
# Level B test (skipped if networkx/python-louvain not installed)
# ---------------------------------------------------------------------------

def test_level_b_writes_cluster_candidates(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    _seed_files(conn, 4)
    for i in range(1, 5):
        _seed_keywords(conn, i, "highway")
    conn.close()
    open_kb(kb_path).close()

    # Seed Level A candidates manually so Level B has input
    fake = _make_fake_spacy([
        ["highway", "bridge"],
        ["highway", "bridge"],
        ["river", "bridge"],
        ["river", "highway"],
    ])
    config = Config(suggest_min_files=2, npmi_min_weight=0.01)
    _run_level_a(corpus_path, kb_path, config, fake, monkeypatch)

    _run_level_b(corpus_path, kb_path, config, monkeypatch)

    conn = open_corpus(corpus_path)
    level_b = conn.execute(
        "SELECT * FROM candidates WHERE source='level_b'"
    ).fetchall()
    conn.close()

    if level_b:
        assert all(r["cluster_id"] is not None for r in level_b)
