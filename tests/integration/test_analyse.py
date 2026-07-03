from pathlib import Path

from src.config import Config
from src.db.corpus import add_source, open_corpus, set_token_decided
from src.pipeline.cancel import make_cancel_event
from src.pipeline.progress import NullProgressReporter
from src.stages.analyse import run_analyse
from src.stages.ingest import run_ingest


def _make_images(directory: Path, names: list[str]) -> None:
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", (4, 4)).save(directory / name)


def _ingest_and_analyse(corpus_path: Path, kb_path: Path, src_dir: Path, file_type: str = "images") -> None:
    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), file_type, True)
    conn.close()

    cfg = Config()
    run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())
    run_analyse(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())


def test_analyse_populates_analyse_tokens(tmp_path):
    """After ingest, run_analyse populates analyse_tokens."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["footage_001.jpg", "footage_002.jpg", "footage_003.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    count = conn.execute("SELECT COUNT(*) FROM analyse_tokens").fetchone()[0]
    conn.close()

    assert count > 0


def test_analyse_strips_common_prefix(tmp_path):
    """Tokens from deep path prefix dirs should not appear as standalone tokens.

    Files at sources/root/proj/footage/file.jpg — after stripping the common prefix
    the relative path is short. 'uniqueXXX' tokens from filenames should appear.
    """
    src_dir = tmp_path / "sources" / "root" / "project" / "footage"
    _make_images(src_dir, [f"uniquetoken{i:03d}.jpg" for i in range(3)])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    token_rows = conn.execute("SELECT token FROM analyse_tokens").fetchall()
    tokens = {r["token"] for r in token_rows}
    conn.close()

    assert any("uniquetoken" in t for t in tokens)


def test_analyse_classifies_date_token(tmp_path):
    """Token '160929' in filename → pattern_class='6digit_numeric', semantic_type='date'."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["160929_clip001.jpg", "160929_clip002.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT pattern_class, semantic_type FROM analyse_tokens WHERE token='160929'"
    ).fetchone()
    conn.close()

    assert row is not None, "Token '160929' not found in analyse_tokens"
    assert row["pattern_class"] == "6digit_numeric"
    assert row["semantic_type"] == "date"


def test_analyse_reruns_are_idempotent(tmp_path):
    """Running analyse twice produces the same token count — no duplicates."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["clip_001.jpg", "clip_002.jpg", "clip_003.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()

    cfg = Config()
    run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())
    run_analyse(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    count1 = conn.execute("SELECT COUNT(*) FROM analyse_tokens").fetchone()[0]
    conn.close()

    run_analyse(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    count2 = conn.execute("SELECT COUNT(*) FROM analyse_tokens").fetchone()[0]
    conn.close()

    assert count2 == count1


def test_analyse_rerun_preserves_decided_status(tmp_path):
    """Re-running analyse does not reset status='decided' on previously reviewed tokens."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["clip_001.jpg", "clip_002.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT id FROM analyse_tokens WHERE token='clip'").fetchone()
    assert row is not None, "Token 'clip' should exist after analyse"
    token_id = row["id"]
    set_token_decided(conn, token_id)
    conn.close()

    run_analyse(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT status FROM analyse_tokens WHERE id=?", (token_id,)).fetchone()
    conn.close()
    assert row is not None, "Token should still exist after re-analyse"
    assert row["status"] == "decided", "decided status must survive a re-run of analyse"


def test_analyse_removes_stale_tokens(tmp_path):
    """Tokens from files removed from the corpus DB are cleaned up on the next analyse run."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["vanishing_001.jpg", "keeper_002.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()

    cfg = Config()
    run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())
    run_analyse(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    assert conn.execute("SELECT id FROM analyse_tokens WHERE token='vanishing'").fetchone() is not None
    # Simulate file removal from the corpus by deleting its DB row
    conn.execute("DELETE FROM files WHERE filename='vanishing_001.jpg'")
    conn.commit()
    conn.close()

    run_analyse(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    stale = conn.execute("SELECT id FROM analyse_tokens WHERE token='vanishing'").fetchone()
    conn.close()
    assert stale is None, "Token from removed corpus entry should be cleaned up after re-analyse"


def test_analyse_updates_pipeline_checkpoint(tmp_path):
    """pipeline_checkpoints has a row for 'analyse' with files_processed > 0."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["img_a.jpg", "img_b.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT * FROM pipeline_checkpoints WHERE stage='analyse'").fetchone()
    conn.close()

    assert row is not None
    assert row["files_processed"] > 0


def test_analyse_generates_bigrams_for_adjacent_word_tokens(tmp_path):
    """Adjacent word tokens in filenames produce a bigram entry with pattern_class='ngram'."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["colquitz_creek_001.jpg", "colquitz_creek_002.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT pattern_class, semantic_type FROM analyse_tokens WHERE token='colquitz creek'"
    ).fetchone()
    conn.close()

    assert row is not None, "Bigram 'colquitz creek' should appear in analyse_tokens"
    assert row["pattern_class"] == "ngram"
    assert row["semantic_type"] == "compound"


def test_analyse_bigrams_require_min_two_files(tmp_path):
    """Bigrams that appear in only one file are not stored."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["colquitz_creek.jpg", "something_else.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT id FROM analyse_tokens WHERE token='colquitz creek'"
    ).fetchone()
    conn.close()

    assert row is None, "Single-file bigrams should not be stored"


def test_analyse_bigrams_exclude_numeric_tokens(tmp_path):
    """Numeric/sequential tokens do not participate in bigrams."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, ["project_20230101_001.jpg", "project_20230101_002.jpg"])

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    _ingest_and_analyse(corpus_path, kb_path, src_dir)

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT id FROM analyse_tokens WHERE token='project 20230101' OR token='20230101 001'"
    ).fetchone()
    conn.close()

    assert row is None, "Numeric tokens should not appear in bigrams"
