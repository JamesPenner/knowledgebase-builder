"""Integration tests for sync.py — KB version stamps and staleness detection."""
from src.db.corpus import open_corpus, update_writeback_kb_version
from src.db.kb import bump_kb_version, open_kb
from src.stages.sync import get_current_kb_version, get_stale_files, mark_files_written


def _seed_files(conn, count=3):
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    for i in range(count):
        conn.execute(
            "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (1, '/f{i}.jpg', 'f{i}.jpg', '.jpg', 'image', 1, 0.0)"
        )
    conn.commit()


# ---------------------------------------------------------------------------
# get_current_kb_version
# ---------------------------------------------------------------------------

def test_get_current_kb_version_none_when_empty(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    version = get_current_kb_version(kb_conn)
    kb_conn.close()
    assert version is None


def test_get_current_kb_version_returns_max(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    bump_kb_version(kb_conn, "vocabulary_term_added")
    bump_kb_version(kb_conn, "vocabulary_term_added")
    bump_kb_version(kb_conn, "correction_added")
    version = get_current_kb_version(kb_conn)
    kb_conn.close()
    assert version == 3


# ---------------------------------------------------------------------------
# get_stale_files
# ---------------------------------------------------------------------------

def test_stale_files_all_null_at_start(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _seed_files(corpus_conn, 3)
    bump_kb_version(kb_conn, "vocabulary_term_added")

    stale = get_stale_files(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()
    assert len(stale) == 3


def test_stale_files_partial_after_partial_write(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _seed_files(corpus_conn, 3)

    bump_kb_version(kb_conn, "vocabulary_term_added")
    v1 = get_current_kb_version(kb_conn)

    # Mark file 1 at v1
    update_writeback_kb_version(corpus_conn, [1], v1)
    corpus_conn.commit()

    bump_kb_version(kb_conn, "vocabulary_term_added")

    stale = get_stale_files(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()
    # All 3 stale: file 1 is at v1, current is v2; files 2,3 are null
    assert len(stale) == 3


def test_stale_files_empty_when_all_in_sync(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _seed_files(corpus_conn, 3)

    bump_kb_version(kb_conn, "vocabulary_term_added")
    version = get_current_kb_version(kb_conn)

    for fid in [1, 2, 3]:
        update_writeback_kb_version(corpus_conn, [fid], version)
    corpus_conn.commit()

    stale = get_stale_files(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()
    assert stale == []


def test_stale_files_empty_when_no_kb_version(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _seed_files(corpus_conn, 2)

    stale = get_stale_files(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()
    assert stale == []


def test_stale_detection_respects_version_id(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _seed_files(corpus_conn, 1)

    bump_kb_version(kb_conn, "vocabulary_term_added")
    bump_kb_version(kb_conn, "vocabulary_term_added")
    bump_kb_version(kb_conn, "vocabulary_term_added")
    version = get_current_kb_version(kb_conn)  # = 3

    # Mark file at exact current version
    update_writeback_kb_version(corpus_conn, [1], version)
    corpus_conn.commit()

    stale = get_stale_files(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()
    assert stale == []


# ---------------------------------------------------------------------------
# mark_files_written
# ---------------------------------------------------------------------------

def test_mark_files_written_updates_version(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _seed_files(corpus_conn, 3)

    bump_kb_version(kb_conn, "vocabulary_term_added")
    version = get_current_kb_version(kb_conn)

    mark_files_written(corpus_conn, [1, 2], version)

    row1 = corpus_conn.execute(
        "SELECT writeback_kb_version FROM files WHERE id=1"
    ).fetchone()
    row3 = corpus_conn.execute(
        "SELECT writeback_kb_version FROM files WHERE id=3"
    ).fetchone()
    corpus_conn.close()
    kb_conn.close()

    assert row1["writeback_kb_version"] == version
    assert row3["writeback_kb_version"] is None
