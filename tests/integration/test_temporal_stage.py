"""Integration tests for Stage: Temporal Field Derivation."""
import threading
from pathlib import Path

from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import open_kb
from src.pipeline.progress import NullProgressReporter
from src.stages.temporal import run_temporal


def _cancel():
    return threading.Event()


def _seed_file(conn, path="/img.jpg", exif_date=None, file_date=None) -> int:
    conn.execute("INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, ?, ?, '.jpg', 'image', 1, 0.0)",
        (path, Path(path).name),
    )
    file_id = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()["id"]
    if exif_date:
        conn.execute(
            "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
            " VALUES (?, 'exif_date_taken', ?, 'datetime')",
            (file_id, exif_date),
        )
    if file_date:
        conn.execute(
            "INSERT INTO file_captured_fields (file_id, field_name, value)"
            " VALUES (?, 'file_date', ?)",
            (file_id, file_date),
        )
    conn.commit()
    return file_id


def _get_temporal(conn, file_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM file_temporal_fields WHERE file_id = ?", (file_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_temporal_derives_from_exif_date(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(conn, exif_date="2023:07:04 14:30:00")
    conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    row = _get_temporal(conn, fid)
    conn.close()

    assert row is not None
    assert row["year"] == 2023
    assert row["decade"] == "2020s"
    assert row["month_name"] == "July"
    assert row["day_name"] == "Tuesday"
    assert row["season"] == "Summer"
    assert row["time_of_day"] == "afternoon"
    assert row["holiday"] is None


def test_temporal_derives_holiday(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(conn, exif_date="2023:12:25 10:00:00")
    conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    row = _get_temporal(conn, fid)
    conn.close()

    assert row["holiday"] == "Christmas Day"
    assert row["season"] == "Winter"


def test_temporal_fallback_to_file_date(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(conn, file_date="2022-03-20")
    conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    row = _get_temporal(conn, fid)
    conn.close()

    assert row is not None
    assert row["year"] == 2022
    assert row["month_name"] == "March"
    assert row["season"] == "Spring"
    assert row["time_of_day"] is None


def test_temporal_no_date_still_writes_row(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(conn)
    conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    row = _get_temporal(conn, fid)
    conn.close()

    assert row is not None
    assert row["year"] is None
    assert row["season"] is None


# ---------------------------------------------------------------------------
# Resume (idempotence)
# ---------------------------------------------------------------------------

def test_temporal_skips_already_processed(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid = _seed_file(conn, exif_date="2023:07:04 10:00:00")
    conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    # Mutate the row manually to verify second run does not overwrite
    conn = open_corpus(corpus_path)
    conn.execute("UPDATE file_temporal_fields SET year = 9999 WHERE file_id = ?", (fid,))
    conn.commit()
    conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    row = _get_temporal(conn, fid)
    conn.close()

    assert row["year"] == 9999  # unchanged — second run skipped


# ---------------------------------------------------------------------------
# Multiple files
# ---------------------------------------------------------------------------

def test_temporal_multiple_files(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    open_kb(kb_path).close()
    fid1 = _seed_file(conn, "/a.jpg", exif_date="2020:01:01 00:00:00")
    fid2 = _seed_file(conn, "/b.jpg", exif_date="2020:07:04 12:00:00")
    fid3 = _seed_file(conn, "/c.jpg")  # no date
    conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    rows = {
        fid1: _get_temporal(conn, fid1),
        fid2: _get_temporal(conn, fid2),
        fid3: _get_temporal(conn, fid3),
    }
    conn.close()

    assert rows[fid1]["season"] == "Winter"
    assert rows[fid2]["season"] == "Summer"
    assert rows[fid3]["year"] is None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_temporal_export_csv(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_file(conn, exif_date="2023:12:25 14:00:00")
    conn.close()
    kb_conn.close()

    run_temporal(corpus_path, kb_path, Config(), NullProgressReporter(), _cancel())

    from src.pipeline.cancel import make_cancel_event
    from src.stages.export import run_export

    run_export(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    csv_path = kb_path.parent / "export" / "temporal_fields.csv"
    assert csv_path.exists()
    content = csv_path.read_text(encoding="utf-8")
    assert "December" in content
    assert "Christmas Day" in content
    assert "Winter" in content


def test_temporal_export_skipped_when_no_rows(tmp_path):
    """temporal_fields.csv is not created if no temporal rows exist."""
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_file(conn)
    conn.close()
    kb_conn.close()

    # Run export without running temporal first
    from src.pipeline.cancel import make_cancel_event
    from src.stages.export import run_export

    run_export(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    csv_path = kb_path.parent / "export" / "temporal_fields.csv"
    assert not csv_path.exists()
