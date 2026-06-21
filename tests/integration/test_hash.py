"""Integration tests for Stage 2 (Hash)."""
from pathlib import Path

from src.config import Config
from src.db.corpus import add_source, open_corpus
from src.db.kb import open_kb
from src.pipeline.cancel import make_cancel_event
from src.pipeline.progress import NullProgressReporter
from src.stages.hash import run_hash
from src.stages.ingest import run_ingest


def _make_image(path: Path, color=(128, 64, 32)) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color=color).save(path, "JPEG")


def _setup(tmp_path: Path, images: dict) -> tuple[Path, Path]:
    """images: {filename: color_tuple}"""
    src_dir = tmp_path / "sources"
    for name, color in images.items():
        _make_image(src_dir / name, color)
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()
    open_kb(kb_path).close()
    run_ingest(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())
    return corpus_path, kb_path


def test_hash_updates_files_sha256(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, {"a.jpg": (10, 20, 30), "b.jpg": (40, 50, 60)})
    run_hash(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT sha256 FROM files").fetchall()
    conn.close()

    assert all(r["sha256"] is not None for r in rows)
    assert len(rows) == 2


def test_hash_marks_duplicate_canonical_id(tmp_path):
    src_dir = tmp_path / "sources"
    _make_image(src_dir / "original.jpg", color=(77, 88, 99))
    import shutil
    shutil.copy(src_dir / "original.jpg", src_dir / "duplicate.jpg")

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()
    open_kb(kb_path).close()
    run_ingest(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())
    run_hash(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT id, sha256, canonical_id FROM files ORDER BY id").fetchall()
    conn.close()

    assert rows[0]["sha256"] == rows[1]["sha256"]
    assert rows[0]["canonical_id"] is None
    assert rows[1]["canonical_id"] == rows[0]["id"]


def test_hash_writes_image_hashes(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, {"img.jpg": (100, 150, 200)})
    run_hash(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT * FROM file_hashes").fetchone()
    conn.close()

    assert row is not None
    assert row["sha256_content"] is not None
    assert row["phash"] is not None
    assert row["dhash"] is not None


def test_hash_updates_checkpoint(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, {"img.jpg": (10, 20, 30)})
    run_hash(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT * FROM pipeline_checkpoints WHERE stage='hash'").fetchone()
    conn.close()

    assert row is not None
    assert row["files_processed"] > 0
