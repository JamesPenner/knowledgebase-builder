"""Integration tests for Stage 6 (Write-back) — requires ExifTool on PATH."""
import json
import shutil
import threading

import pytest

from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import add_vocabulary_term, bump_kb_version, open_kb
from src.pipeline.progress import NullProgressReporter
from src.stages.writeback import run_writeback


def _exiftool_exe():
    exe = shutil.which("exiftool")
    if exe:
        return exe
    from pathlib import Path
    candidate = Path("tools/exiftool.exe")
    return str(candidate) if candidate.exists() else None


@pytest.fixture(autouse=True)
def require_exiftool():
    if _exiftool_exe() is None:
        pytest.skip("ExifTool not found")


def _seed(corpus_conn, kb_conn, image_path, tags=None, refined_desc=None, new_terms=None):
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, ?, 'test.jpg', '.jpg', 'image', 1, 0.0)",
        (str(image_path),),
    )
    corpus_conn.commit()

    if tags is not None or refined_desc is not None:
        corpus_conn.execute(
            "INSERT INTO retag_output"
            " (file_id, tags_json, refined_description, new_terms_proposed_json,"
            "  model, processed_at, retag_status)"
            " VALUES (1, ?, ?, ?, 'test', datetime('now'), 'done')",
            (
                json.dumps(tags or []),
                refined_desc,
                json.dumps(new_terms or []),
            ),
        )
        corpus_conn.commit()

    if tags:
        for t in tags:
            add_vocabulary_term(kb_conn, t)
    bump_kb_version(kb_conn, "vocabulary_term_added")
    kb_conn.commit()


def _make_config(tmp_path):
    exe = _exiftool_exe()
    return Config(exiftool=exe)


def _run(corpus_path, kb_path, config=None, force=False):
    if config is None:
        config = Config(exiftool=_exiftool_exe())
    run_writeback(
        corpus_path,
        kb_path,
        config,
        NullProgressReporter(),
        threading.Event(),
        force=force,
    )


def test_writeback_writes_keywords_to_file(tmp_path, sample_image):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    _seed(corpus_conn, kb_conn, sample_image, tags=["bridge", "highway"])
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    from src.exiftool import ExifTool
    with ExifTool(_exiftool_exe()) as et:
        meta = et.get_metadata([sample_image])

    assert meta
    flat = json.dumps(meta[0]).lower()
    assert "bridge" in flat or "highway" in flat


def test_writeback_skips_in_sync_files(tmp_path, sample_image):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    _seed(corpus_conn, kb_conn, sample_image, tags=["bridge"])

    from src.stages.sync import get_current_kb_version
    from src.db.corpus import update_writeback_kb_version
    version = get_current_kb_version(kb_conn)
    update_writeback_kb_version(corpus_conn, [1], version)
    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    corpus_conn = open_corpus(corpus_path)
    log_count = corpus_conn.execute("SELECT COUNT(*) FROM writeback_log").fetchone()[0]
    corpus_conn.close()
    assert log_count == 0


def test_writeback_marks_files_in_sync_after_write(tmp_path, sample_image):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    _seed(corpus_conn, kb_conn, sample_image, tags=["highway"])

    from src.stages.sync import get_current_kb_version
    expected_version = get_current_kb_version(kb_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    corpus_conn = open_corpus(corpus_path)
    row = corpus_conn.execute(
        "SELECT writeback_kb_version FROM files WHERE id=1"
    ).fetchone()
    corpus_conn.close()
    assert row["writeback_kb_version"] == expected_version


def test_writeback_logs_to_writeback_log(tmp_path, sample_image):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    _seed(corpus_conn, kb_conn, sample_image, tags=["bridge"])
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    corpus_conn = open_corpus(corpus_path)
    rows = corpus_conn.execute("SELECT * FROM writeback_log").fetchall()
    corpus_conn.close()
    assert len(rows) > 0
    assert all(r["status"] in ("success", "failed") for r in rows)


def test_writeback_handles_no_retag_output(tmp_path, sample_image):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    # Seed file + kb_version but NO retag_output
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, ?, 'test.jpg', '.jpg', 'image', 1, 0.0)",
        (str(sample_image),),
    )
    corpus_conn.commit()
    bump_kb_version(kb_conn, "vocabulary_term_added")
    kb_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    # Should complete without error; file may be skipped (no tags to write)
    _run(corpus_path, kb_path)

    corpus_conn = open_corpus(corpus_path)
    checkpoint = corpus_conn.execute(
        "SELECT * FROM pipeline_checkpoints WHERE stage='writeback'"
    ).fetchone()
    corpus_conn.close()
    assert checkpoint is not None


def test_writeback_writes_description_field(tmp_path, sample_image):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    refined = "A steel highway bridge over a river."
    _seed(corpus_conn, kb_conn, sample_image, tags=[], refined_desc=refined)
    corpus_conn.close()
    kb_conn.close()

    # Provide a field_map.csv so the description field is recognised
    ref_dir = kb_path.parent / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    field_map = ref_dir / "field_map.csv"
    field_map.write_text(
        "field_name,canonical_name,priority,enrichment_text,write_back,value_type,notes\n"
        "XMP-dc:Description,description,1,true,true,text,\n",
        encoding="utf-8",
    )

    _run(corpus_path, kb_path)

    from src.exiftool import ExifTool
    with ExifTool(_exiftool_exe()) as et:
        meta = et.get_metadata([sample_image])

    assert meta
    flat = json.dumps(meta[0])
    assert "bridge" in flat.lower() or "highway" in flat.lower()
