"""Integration tests for Stage 1.5 (Extract Metadata) — skipped without ExifTool."""
import shutil
from pathlib import Path

import pytest

from src.config import Config
from src.db.corpus import add_source, open_corpus
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
