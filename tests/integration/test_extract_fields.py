"""Integration tests for Stage 1.6 (Extract Fields) — no ExifTool required."""
import csv
import json
from pathlib import Path

from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import open_kb
from src.pipeline.cancel import make_cancel_event
from src.pipeline.progress import NullProgressReporter
from src.stages.extract_fields import run_extract_fields


def _seed_corpus(tmp_path: Path, metadata: dict) -> tuple[Path, Path]:
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'image', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    conn.execute("INSERT INTO file_exif (file_id, metadata_json) VALUES (1, ?)", (json.dumps(metadata),))
    conn.commit()
    conn.close()
    return corpus_path, kb_path


def _write_field_map(kb_path: Path, rows: list[dict]) -> None:
    ref_dir = kb_path.parent / "reference"
    ref_dir.mkdir(exist_ok=True)
    csv_path = ref_dir / "field_map.csv"
    fieldnames = [
        "CanonicalName", "ExifTool_Tag", "Priority", "DataType",
        "Category", "enrichment_text", "write_back", "extract_to_column", "rename_token",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def test_extract_fields_populates_scalar_fields(tmp_path):
    meta = {"XMP-dc:Title": "Test Title", "EXIF:Make": "Sony"}
    corpus_path, kb_path = _seed_corpus(tmp_path, meta)
    _write_field_map(kb_path, [
        {"ExifTool_Tag": "XMP-dc:Title", "CanonicalName": "title",       "Priority": "1", "DataType": "str"},
        {"ExifTool_Tag": "EXIF:Make",    "CanonicalName": "camera_make", "Priority": "1", "DataType": "str"},
    ])

    run_extract_fields(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    title_row = conn.execute(
        "SELECT value FROM file_metadata_fields WHERE canonical_name='title'"
    ).fetchone()
    make_row = conn.execute(
        "SELECT value FROM file_metadata_fields WHERE canonical_name='camera_make'"
    ).fetchone()
    conn.close()

    assert title_row is not None and title_row["value"] == "Test Title"
    assert make_row is not None and make_row["value"] == "Sony"


def test_extract_fields_populates_keyword_list(tmp_path):
    meta = {"XMP-dc:Subject": ["bridge", "highway", "bc"]}
    corpus_path, kb_path = _seed_corpus(tmp_path, meta)
    _write_field_map(kb_path, [
        {"ExifTool_Tag": "XMP-dc:Subject", "CanonicalName": "keywords", "Priority": "1", "DataType": "keyword_list"},
    ])

    run_extract_fields(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    rows = conn.execute(
        "SELECT keyword FROM file_metadata_keywords WHERE canonical_name='keywords' ORDER BY keyword"
    ).fetchall()
    conn.close()

    keywords = {r["keyword"] for r in rows}
    assert keywords == {"bridge", "highway", "bc"}


def test_extract_fields_is_idempotent(tmp_path):
    meta = {"XMP-dc:Title": "Idempotent Test", "XMP-dc:Subject": ["alpha", "beta"]}
    corpus_path, kb_path = _seed_corpus(tmp_path, meta)
    _write_field_map(kb_path, [
        {"ExifTool_Tag": "XMP-dc:Title",   "CanonicalName": "title",    "Priority": "1", "DataType": "str"},
        {"ExifTool_Tag": "XMP-dc:Subject", "CanonicalName": "keywords", "Priority": "1", "DataType": "keyword_list"},
    ])

    run_extract_fields(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())
    conn = open_corpus(corpus_path)
    fields1 = conn.execute("SELECT COUNT(*) FROM file_metadata_fields").fetchone()[0]
    kw1 = conn.execute("SELECT COUNT(*) FROM file_metadata_keywords").fetchone()[0]
    conn.close()

    run_extract_fields(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())
    conn = open_corpus(corpus_path)
    fields2 = conn.execute("SELECT COUNT(*) FROM file_metadata_fields").fetchone()[0]
    kw2 = conn.execute("SELECT COUNT(*) FROM file_metadata_keywords").fetchone()[0]
    conn.close()

    assert fields2 == fields1
    assert kw2 == kw1


def test_extract_fields_updates_checkpoint(tmp_path):
    meta = {"XMP-dc:Title": "Checkpoint Test"}
    corpus_path, kb_path = _seed_corpus(tmp_path, meta)
    _write_field_map(kb_path, [
        {"ExifTool_Tag": "XMP-dc:Title", "CanonicalName": "title", "Priority": "1", "DataType": "str"},
    ])

    run_extract_fields(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT * FROM pipeline_checkpoints WHERE stage='extract_fields'"
    ).fetchone()
    conn.close()
    assert row is not None
