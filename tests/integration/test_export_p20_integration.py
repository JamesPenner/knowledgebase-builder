"""Integration tests for KB.P20 — Coverage Analytics + Near-Duplicate Grouping."""
import csv
from pathlib import Path

import pytest

from src.db.corpus import open_corpus
from src.db.kb import open_kb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs):
    from src.config import Config
    return Config(**kwargs)


def _ensure_source(conn) -> int:
    row = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid


def _ingest(conn, file_id: int, path: str, file_type: str = "image") -> None:
    src = _ensure_source(conn)
    conn.execute(
        "INSERT OR IGNORE INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
        "VALUES (?, ?, ?, ?, '.jpg', ?, 1000, 0.0)",
        (file_id, src, path, Path(path).name, file_type),
    )
    conn.commit()


def _add_phash(conn, file_id: int, phash: str, canonical_id=None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO file_hashes(file_id, phash, hashed_at) VALUES (?, ?, datetime('now'))",
        (file_id, phash),
    )
    if canonical_id is not None:
        conn.execute("UPDATE files SET canonical_id = ? WHERE id = ?", (canonical_id, file_id))
    conn.commit()


def _add_description(conn, file_id: int, status: str = "done") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO descriptions(file_id, pass1_status) VALUES (?, ?)",
        (file_id, status),
    )
    conn.commit()


def _add_tag(conn, file_id: int, tag: str = "beach") -> None:
    conn.execute(
        "INSERT INTO file_derived_tags(file_id, tag, category, source) VALUES (?, ?, 'general', 'classify')",
        (file_id, tag),
    )
    conn.commit()


@pytest.fixture
def p20_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    return corpus_conn, kb_conn, corpus_path, kb_path, tmp_path


# ---------------------------------------------------------------------------
# Coverage CSV tests
# ---------------------------------------------------------------------------

class TestWriteCoverage:
    def test_coverage_csv_written(self, p20_dbs):
        from src.stages.export import _write_coverage
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _write_coverage(export_dir, corpus_conn)
        assert (export_dir / "coverage.csv").exists()

    def test_coverage_headers_correct(self, p20_dbs):
        from src.stages.export import _write_coverage
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _write_coverage(export_dir, corpus_conn)
        with open(export_dir / "coverage.csv", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames
        expected = [
            "path", "has_description", "has_tags", "has_entities", "has_gps",
            "has_aesthetic_score", "has_asset_date", "has_quality_score",
            "has_transcript", "has_face", "has_voice", "tag_count", "entity_count",
        ]
        assert headers == expected

    def test_bare_file_all_zeros(self, p20_dbs):
        from src.stages.export import _write_coverage
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _write_coverage(export_dir, corpus_conn)
        with open(export_dir / "coverage.csv", newline="", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["has_description"] == "0"
        assert row["has_tags"] == "0"
        assert row["tag_count"] == "0"

    def test_enriched_file_flags(self, p20_dbs):
        from src.stages.export import _write_coverage
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _add_description(corpus_conn, 1, "done")
        _add_tag(corpus_conn, 1, "beach")
        _add_tag(corpus_conn, 1, "sunset")
        _write_coverage(export_dir, corpus_conn)
        with open(export_dir / "coverage.csv", newline="", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["has_description"] == "1"
        assert row["has_tags"] == "1"
        assert row["tag_count"] == "2"

    def test_empty_corpus_writes_headers_only(self, p20_dbs):
        from src.stages.export import _write_coverage
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _write_coverage(export_dir, corpus_conn)
        with open(export_dir / "coverage.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows == []


# ---------------------------------------------------------------------------
# Near-Duplicate CSV tests
# ---------------------------------------------------------------------------

class TestWriteNearDuplicates:
    def test_near_dup_csv_written(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _write_near_duplicates(export_dir, corpus_conn, 10)
        assert (export_dir / "near_duplicate_groups.csv").exists()

    def test_near_dup_headers_correct(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _write_near_duplicates(export_dir, corpus_conn, 10)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            headers = csv.DictReader(fh).fieldnames
        assert headers == ["group_id", "path", "rank", "nima_score", "hamming_distance", "confidence"]

    def test_two_near_duplicate_files_grouped(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _ingest(corpus_conn, 2, "/b.jpg")
        # Identical pHash → distance 0
        _add_phash(corpus_conn, 1, "aaaa000000000000")
        _add_phash(corpus_conn, 2, "aaaa000000000000")
        _write_near_duplicates(export_dir, corpus_conn, 10)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        assert rows[0]["group_id"] == rows[1]["group_id"]
        assert {rows[0]["rank"], rows[1]["rank"]} == {"1", "2"}

    def test_distinct_files_not_grouped(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _ingest(corpus_conn, 2, "/b.jpg")
        # Distance 64 (all bits flipped)
        _add_phash(corpus_conn, 1, "0000000000000000")
        _add_phash(corpus_conn, 2, "ffffffffffffffff")
        _write_near_duplicates(export_dir, corpus_conn, 10)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows == []

    def test_threshold_zero_stops_grouping(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _ingest(corpus_conn, 2, "/b.jpg")
        # Distance 1
        _add_phash(corpus_conn, 1, "0000000000000000")
        _add_phash(corpus_conn, 2, "0000000000000001")
        _write_near_duplicates(export_dir, corpus_conn, 0)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows == []

    def test_canonical_duplicate_excluded(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _ingest(corpus_conn, 2, "/a_copy.jpg")
        _add_phash(corpus_conn, 1, "aaaa000000000000")
        # file 2 is a canonical duplicate of file 1
        _add_phash(corpus_conn, 2, "aaaa000000000000", canonical_id=1)
        _write_near_duplicates(export_dir, corpus_conn, 10)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows == []

    def test_empty_corpus_writes_headers_only(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _write_near_duplicates(export_dir, corpus_conn, 10)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows == []


# ---------------------------------------------------------------------------
# run_export integration
# ---------------------------------------------------------------------------

class TestRunExportP20:
    def test_run_export_writes_both_new_csvs(self, p20_dbs):
        import threading
        from src.pipeline.progress import NullProgressReporter
        from src.stages.export import run_export

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = p20_dbs
        _ingest(corpus_conn, 1, "/a.jpg")
        corpus_conn.close()
        kb_conn.close()

        config = _make_config()
        run_export(corpus_path, kb_path, config, NullProgressReporter(), threading.Event())

        export_dir = kb_path.parent / "export"
        assert (export_dir / "coverage.csv").exists()
        assert (export_dir / "near_duplicate_groups.csv").exists()

    def test_config_threshold_respected(self, p20_dbs):
        from src.stages.export import _write_near_duplicates
        corpus_conn, _, _, _, tmp_path = p20_dbs
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        _ingest(corpus_conn, 1, "/a.jpg")
        _ingest(corpus_conn, 2, "/b.jpg")
        # Distance 5 — within threshold=10 but not threshold=0
        base = 0x0000000000000000
        dist5 = base ^ ((1 << 5) - 1)
        _add_phash(corpus_conn, 1, format(base, "016x"))
        _add_phash(corpus_conn, 2, format(dist5, "016x"))

        # At default threshold (10) → grouped
        _write_near_duplicates(export_dir, corpus_conn, 10)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            rows_default = list(csv.DictReader(fh))
        assert len(rows_default) == 2

        # At threshold 0 → not grouped
        _write_near_duplicates(export_dir, corpus_conn, 0)
        with open(export_dir / "near_duplicate_groups.csv", newline="", encoding="utf-8") as fh:
            rows_strict = list(csv.DictReader(fh))
        assert rows_strict == []
