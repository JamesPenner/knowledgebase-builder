from pathlib import Path

from src.config import Config
from src.db.corpus import add_source, open_corpus
from src.pipeline.cancel import make_cancel_event
from src.pipeline.progress import NullProgressReporter
from src.stages.ingest import run_ingest


def _make_images(directory: Path, count: int = 3) -> list[Path]:
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        p = directory / f"img_{i:03d}.jpg"
        Image.new("RGB", (4, 4)).save(p)
        paths.append(p)
    return paths


def _run(corpus_path: Path, kb_path: Path) -> None:
    run_ingest(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())


def test_ingest_populates_files_table(tmp_path):
    """Happy path: 3 images in a source dir → 3 rows in files table."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, 3)

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()

    _run(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    conn.close()

    assert count == 3


def test_ingest_skips_unchanged_file(tmp_path):
    """Second run with unchanged files adds 0 new rows."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, 2)

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()

    _run(corpus_path, kb_path)
    conn = open_corpus(corpus_path)
    count_before = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    conn.close()

    _run(corpus_path, kb_path)
    conn = open_corpus(corpus_path)
    count_after = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    conn.close()

    assert count_after == count_before


def test_ingest_detects_file_type(tmp_path):
    """file_type column is set from extension: .jpg → images."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, 2)

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()

    _run(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT file_type, ext FROM files").fetchall()
    conn.close()

    for row in rows:
        assert row["file_type"] == "images"
        assert row["ext"] in {".jpg", ".jpeg", ".png"}


def test_ingest_updates_pipeline_checkpoint(tmp_path):
    """pipeline_checkpoints has a row for 'ingest' after run."""
    src_dir = tmp_path / "sources"
    _make_images(src_dir, 1)

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()

    _run(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT * FROM pipeline_checkpoints WHERE stage='ingest'").fetchone()
    conn.close()

    assert row is not None
    assert row["files_processed"] >= 0


def test_ingest_no_sources_is_noop(tmp_path):
    """No sources configured → run exits cleanly, files table empty."""
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    open_corpus(corpus_path).close()
    _run(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    conn.close()

    assert count == 0
