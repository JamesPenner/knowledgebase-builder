"""Integration tests for KB.R1 — Summarize Stage (3c)."""
import csv
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import app
from src.db.corpus import (
    get_export_summaries,
    get_file_summary,
    get_pending_summarize_files,
    open_corpus,
    upsert_file_summary,
)
from src.db.kb import open_kb
from src.pipeline.dag import DEPENDENCIES, INVALIDATES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_source(conn) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES ('/src', 'all', 1)"
    )
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE path='/src'").fetchone()["id"]


def _seed_file(conn, path: str = "/src/a.jpg") -> int:
    sid = _seed_source(conn)
    conn.execute(
        "INSERT INTO files (source_id, path, filename) VALUES (?, ?, 'a.jpg')",
        (sid, path),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def _seed_description(conn, file_id: int, status: str = "done") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO descriptions"
        " (file_id, pass1_status, description_normalized, model, processed_at)"
        " VALUES (?, ?, 'A nice photo.', 'test-model', datetime('now'))",
        (file_id, status),
    )
    conn.commit()


def _seed_transcription(conn, file_id: int, status: str = "done") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO transcriptions"
        " (file_id, transcribe_status, transcript_text, model, processed_at)"
        " VALUES (?, ?, 'Hello world.', 'whisper', datetime('now'))",
        (file_id, status),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_file_summaries_table_exists(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(file_summaries)")}
    assert "file_id" in cols
    assert "summary_text" in cols
    assert "status" in cols


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def test_get_pending_summarize_files_empty(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    result = get_pending_summarize_files(conn)
    assert result == []


def test_get_pending_summarize_files_with_description(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    _seed_description(conn, fid, status="done")
    rows = get_pending_summarize_files(conn)
    assert any(r["id"] == fid for r in rows)


def test_get_pending_summarize_files_with_transcription(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    _seed_transcription(conn, fid, status="done")
    rows = get_pending_summarize_files(conn)
    assert any(r["id"] == fid for r in rows)


def test_get_pending_summarize_files_skips_done(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    _seed_description(conn, fid, status="done")
    upsert_file_summary(conn, fid, "A summary.", "m", "v1", "done")
    conn.commit()
    rows = get_pending_summarize_files(conn)
    assert not any(r["id"] == fid for r in rows)


def test_upsert_file_summary_insert(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    upsert_file_summary(conn, fid, "Summary text.", "model-x", "v1", "done")
    conn.commit()
    row = conn.execute("SELECT * FROM file_summaries WHERE file_id=?", (fid,)).fetchone()
    assert row["summary_text"] == "Summary text."
    assert row["model"] == "model-x"
    assert row["status"] == "done"


def test_upsert_file_summary_update(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    upsert_file_summary(conn, fid, "First.", "m", "v1", "done")
    conn.commit()
    upsert_file_summary(conn, fid, "Second.", "m", "v1", "done")
    conn.commit()
    rows = conn.execute("SELECT * FROM file_summaries WHERE file_id=?", (fid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["summary_text"] == "Second."


def test_get_file_summary_found(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    upsert_file_summary(conn, fid, "Hello.", "m", "v1", "done")
    conn.commit()
    row = get_file_summary(conn, fid)
    assert row is not None
    assert row["summary_text"] == "Hello."


def test_get_file_summary_missing(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    row = get_file_summary(conn, fid)
    assert row is None


def test_get_export_summaries_filters_done(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    fid1 = _seed_file(conn, "/src/a.jpg")
    fid2 = _seed_file(conn, "/src/b.jpg")
    upsert_file_summary(conn, fid1, "Summary A.", "m", "v1", "done")
    upsert_file_summary(conn, fid2, None, "m", "v1", "skipped")
    conn.commit()
    rows = get_export_summaries(conn)
    paths = [r["file_path"] for r in rows]
    assert "/src/a.jpg" in paths
    assert "/src/b.jpg" not in paths


def test_get_export_summaries_empty(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    assert get_export_summaries(conn) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

client = TestClient(app, raise_server_exceptions=False)


def test_summarize_run_returns_job_id(tmp_path, monkeypatch):
    import src.api.pipeline as _mod

    def fake_folder(kb: str) -> Path:
        folder = tmp_path / kb
        folder.mkdir(exist_ok=True)
        open_corpus(folder / "corpus.db").close()
        open_kb(folder / "knowledge.db").close()
        return folder

    monkeypatch.setattr(_mod, "_get_kb_folder", fake_folder)

    resp = client.post("/api/stages/summarize/run", json={"kb": "testdb"})
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "started"


def test_summarize_cancel_returns_cancelled():
    resp = client.post("/api/stages/summarize/cancel", params={"kb": "testdb"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_summarize_status_endpoint():
    resp = client.get("/api/stages/summarize/status", params={"kb": "testdb"})
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_summarize_command_exists():
    from typer.testing import CliRunner
    from src.cli.pipeline import app as cli_app

    runner = CliRunner()
    result = runner.invoke(cli_app, ["summarize", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_write_summaries_produces_csv(tmp_path):
    from src.stages.export import _write_summaries

    conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(conn)
    upsert_file_summary(conn, fid, "A good summary.", "model-x", "v1", "done")
    conn.commit()

    export_dir = tmp_path / "export"
    export_dir.mkdir()
    _write_summaries(export_dir, conn)

    csv_path = export_dir / "summaries.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["summary_text"] == "A good summary."


def test_write_summaries_skipped_when_empty(tmp_path):
    from src.stages.export import _write_summaries

    conn = open_corpus(tmp_path / "corpus.db")
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    _write_summaries(export_dir, conn)
    assert not (export_dir / "summaries.csv").exists()


# ---------------------------------------------------------------------------
# Write-back
# ---------------------------------------------------------------------------

def _make_field_map(kb_folder: Path, canonical: str, field_name: str) -> None:
    ref = kb_folder / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    with open(ref / "field_map.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["field_name", "canonical_name", "value_type", "write_back"])
        writer.writeheader()
        writer.writerow({"field_name": field_name, "canonical_name": canonical, "value_type": "text", "write_back": "false"})


def test_resolve_summarize_field_found(tmp_path):
    from src.stages.writeback import _resolve_summarize_field

    _make_field_map(tmp_path, "summary", "XMP:Description")
    result = _resolve_summarize_field(tmp_path, "summary")
    assert result is not None
    assert result["field_name"] == "XMP:Description"
    assert result["canonical_name"] == "summary"


def test_resolve_summarize_field_missing(tmp_path):
    from src.stages.writeback import _resolve_summarize_field

    _make_field_map(tmp_path, "summary", "XMP:Description")
    result = _resolve_summarize_field(tmp_path, "nonexistent_field")
    assert result is None


def test_resolve_summarize_field_empty_config(tmp_path):
    from src.stages.writeback import _resolve_summarize_field

    result = _resolve_summarize_field(tmp_path, "")
    assert result is None


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------

def test_summarize_in_dependencies():
    assert "summarize" in DEPENDENCIES
    assert "describe" in DEPENDENCIES["summarize"]
    assert "transcribe" in DEPENDENCIES["summarize"]


def test_describe_invalidates_summarize():
    assert "summarize" in INVALIDATES["describe"]


def test_transcribe_invalidates_summarize():
    assert "summarize" in INVALIDATES["transcribe"]


def test_summarize_invalidates_suggest():
    assert "suggest" in INVALIDATES["summarize"]


# ---------------------------------------------------------------------------
# Suggest text pool
# ---------------------------------------------------------------------------

def test_suggest_level_a_includes_summary_text(tmp_path):
    """Summary text of a done file must be assembled into the per-file text for Level A."""
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    fid = _seed_file(corpus_conn)
    upsert_file_summary(corpus_conn, fid, "Unique xylophonist term present.", "m", "v1", "done")
    corpus_conn.commit()

    # Verify the query in suggest Level A would pick up the summary
    row = corpus_conn.execute(
        "SELECT summary_text FROM file_summaries WHERE file_id=? AND status='done'",
        (fid,),
    ).fetchone()
    assert row is not None
    assert "xylophonist" in row["summary_text"]
