"""Integration tests for Stage 1.5 (Extract Metadata) — skipped without ExifTool."""
import shutil
from pathlib import Path

import pytest

from src.config import Config
from src.db.corpus import add_source, open_corpus, reset_file_exif, reset_file_fields, reset_file_hashes
from src.db.kb import open_kb
from src.pipeline.cancel import make_cancel_event
from src.pipeline.progress import NullProgressReporter
from src.stages.ingest import run_ingest

def _exiftool_available() -> bool:
    from src.config import Config
    cfg = Config()
    return shutil.which("exiftool") is not None or Path(cfg.exiftool).exists()


exiftool_required = pytest.mark.skipif(
    not _exiftool_available(),
    reason="ExifTool not found on PATH or at configured tools.exiftool path",
)


def _make_images(directory, names):
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", (32, 32), color=(100, 150, 200)).save(directory / name)


def _setup(tmp_path, filenames):
    src_dir = tmp_path / "sources"
    _make_images(src_dir, filenames)
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()
    open_kb(kb_path).close()
    run_ingest(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())
    return corpus_path, kb_path


@exiftool_required
def test_extract_meta_populates_file_exif(tmp_path):
    from src.stages.extract_meta import run_extract_meta

    corpus_path, kb_path = _setup(tmp_path, ["img_001.jpg", "img_002.jpg"])
    run_extract_meta(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    count = conn.execute("SELECT COUNT(*) FROM file_exif").fetchone()[0]
    conn.close()
    assert count == 2


@exiftool_required
def test_extract_meta_skips_already_extracted(tmp_path):
    from src.stages.extract_meta import run_extract_meta

    corpus_path, kb_path = _setup(tmp_path, ["img_001.jpg"])
    run_extract_meta(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    ts1 = conn.execute("SELECT extracted_at FROM file_exif").fetchone()[0]
    conn.close()

    run_extract_meta(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    ts2 = conn.execute("SELECT extracted_at FROM file_exif").fetchone()[0]
    count = conn.execute("SELECT COUNT(*) FROM file_exif").fetchone()[0]
    conn.close()

    assert count == 1
    assert ts2 == ts1  # not re-extracted; timestamp unchanged


@exiftool_required
def test_extract_meta_updates_checkpoint(tmp_path):
    from src.stages.extract_meta import run_extract_meta

    corpus_path, kb_path = _setup(tmp_path, ["img_001.jpg"])
    run_extract_meta(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT * FROM pipeline_checkpoints WHERE stage='extract_meta'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["files_processed"] > 0


@exiftool_required
def test_generate_field_map_writes_csv(tmp_path):
    from src.stages.extract_meta import run_extract_meta

    corpus_path, kb_path = _setup(tmp_path, ["img_001.jpg"])
    run_extract_meta(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    csv_path = kb_path.parent / "reference" / "field_map.csv"
    assert csv_path.exists()
    assert csv_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Reset helpers (no ExifTool needed)
# ---------------------------------------------------------------------------

def _seed_file(conn, path="/a/b.jpg"):
    from src.db.corpus import add_source
    src_id = add_source(conn, "/a", "images", True)
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime) VALUES (?, ?, 'b.jpg', '.jpg', 'images', 1, 1.0)",
        (src_id, path),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]


def test_reset_file_exif_clears_table(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    fid = _seed_file(conn)
    conn.execute("INSERT INTO file_exif (file_id, metadata_json, extracted_at) VALUES (?, '{}', datetime('now'))", (fid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM file_exif").fetchone()[0] == 1
    n = reset_file_exif(conn)
    conn.close()
    assert n == 1
    conn2 = open_corpus(corpus_path)
    assert conn2.execute("SELECT COUNT(*) FROM file_exif").fetchone()[0] == 0
    conn2.close()


def test_reset_file_fields_clears_tables(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    fid = _seed_file(conn)
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, raw_field_name, value, value_type, extracted_at) VALUES (?, 'title', 'Title', 'x', 'text', datetime('now'))",
        (fid,),
    )
    conn.execute("INSERT INTO file_metadata_keywords (file_id, canonical_name, keyword) VALUES (?, 'keywords', 'sunset')", (fid,))
    conn.commit()
    n = reset_file_fields(conn)
    conn.close()
    assert n == 1
    conn2 = open_corpus(corpus_path)
    assert conn2.execute("SELECT COUNT(*) FROM file_metadata_fields").fetchone()[0] == 0
    assert conn2.execute("SELECT COUNT(*) FROM file_metadata_keywords").fetchone()[0] == 0
    conn2.close()


def test_reset_file_hashes_clears_table_and_sha256(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    fid = _seed_file(conn)
    conn.execute("UPDATE files SET sha256 = 'abc123' WHERE id = ?", (fid,))
    conn.execute(
        "INSERT INTO file_hashes (file_id, sha256_content, phash, dhash, area_hash, hashed_at) VALUES (?, 'abc123', 'ph', 'dh', '[]', datetime('now'))",
        (fid,),
    )
    conn.commit()
    n = reset_file_hashes(conn)
    conn.close()
    assert n == 1
    conn2 = open_corpus(corpus_path)
    assert conn2.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0] == 0
    assert conn2.execute("SELECT sha256 FROM files WHERE id=?", (fid,)).fetchone()["sha256"] is None
    conn2.close()
