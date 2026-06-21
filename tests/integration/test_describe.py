"""Integration tests for Stage 3a describe DB helpers and normalization logic."""
import sqlite3
from pathlib import Path

from src.db.corpus import (
    delete_video_frames_for_file,
    get_pending_describe_files,
    insert_video_frame,
    open_corpus,
    reset_describe_to_pending,
    upsert_description,
)
from src.db.kb import open_kb
from src.stages.describe import _normalize_description


def _seed_file(conn: sqlite3.Connection, path: str, file_type: str, canonical_id=None) -> int:
    conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, ?, 1)",
        ("/sources", "all"),
    )
    conn.execute("SELECT id FROM sources")
    source_id = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime, sha256, canonical_id)"
        " VALUES (?, ?, ?, ?, ?, 1000, 0.0, 'abc123', ?)",
        (source_id, path, Path(path).name, Path(path).suffix.lower(), file_type, canonical_id),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


# ---------------------------------------------------------------------------
# get_pending_describe_files
# ---------------------------------------------------------------------------

def test_get_pending_describe_returns_canonical_files(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()

    # canonical file (canonical_id IS NULL)
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime, sha256)"
        " VALUES (?, '/src/a.jpg', 'a.jpg', '.jpg', 'image', 1000, 0.0, 'sha1')",
        (source_id,),
    )
    # duplicate file (canonical_id IS NOT NULL)
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime, sha256, canonical_id)"
        " VALUES (?, '/src/b.jpg', 'b.jpg', '.jpg', 'image', 1000, 0.0, 'sha1', 1)",
        (source_id,),
    )
    conn.commit()

    rows = get_pending_describe_files(conn)
    assert len(rows) == 1
    assert rows[0]["path"] == "/src/a.jpg"
    conn.close()


def test_get_pending_describe_excludes_done(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()

    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/src/done.jpg', 'done.jpg', '.jpg', 'image', 1000, 0.0)",
        (source_id,),
    )
    conn.commit()
    file_id = conn.execute("SELECT id FROM files WHERE path='/src/done.jpg'").fetchone()["id"]

    upsert_description(conn, file_id, "raw desc", "norm desc", "model", "done")
    conn.commit()

    rows = get_pending_describe_files(conn)
    assert all(r["path"] != "/src/done.jpg" for r in rows)
    conn.close()


def test_get_pending_describe_includes_failed_and_skipped(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()

    for name, status in [("fail.jpg", "failed"), ("skip.jpg", "skipped")]:
        conn.execute(
            f"INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (?, '/src/{name}', '{name}', '.jpg', 'image', 1000, 0.0)",
            (source_id,),
        )
        conn.commit()
        fid = conn.execute(f"SELECT id FROM files WHERE path='/src/{name}'").fetchone()["id"]
        upsert_description(conn, fid, None, None, "model", status)
        conn.commit()

    rows = get_pending_describe_files(conn)
    paths = {r["path"] for r in rows}
    assert "/src/fail.jpg" in paths
    assert "/src/skip.jpg" in paths
    conn.close()


# ---------------------------------------------------------------------------
# upsert_description
# ---------------------------------------------------------------------------

def test_upsert_description_idempotent(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/src/x.jpg', 'x.jpg', '.jpg', 'image', 100, 0.0)",
        (source_id,),
    )
    conn.commit()
    file_id = conn.execute("SELECT id FROM files WHERE path='/src/x.jpg'").fetchone()["id"]

    upsert_description(conn, file_id, "first raw", "first norm", "model-v1", "done")
    upsert_description(conn, file_id, "second raw", "second norm", "model-v2", "done")
    conn.commit()

    rows = conn.execute("SELECT * FROM descriptions WHERE file_id=?", (file_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["description_raw"] == "second raw"
    assert rows[0]["model"] == "model-v2"
    conn.close()


def test_upsert_description_preserves_raw_on_update(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/src/y.jpg', 'y.jpg', '.jpg', 'image', 100, 0.0)",
        (source_id,),
    )
    conn.commit()
    file_id = conn.execute("SELECT id FROM files WHERE path='/src/y.jpg'").fetchone()["id"]

    upsert_description(conn, file_id, "original raw", "original norm", "m", "done")
    conn.commit()

    row = conn.execute("SELECT description_raw FROM descriptions WHERE file_id=?", (file_id,)).fetchone()
    assert row["description_raw"] == "original raw"
    conn.close()


# ---------------------------------------------------------------------------
# insert_video_frame / delete_video_frames_for_file
# ---------------------------------------------------------------------------

def test_insert_video_frame_inserts_row(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/src/v.mp4', 'v.mp4', '.mp4', 'video', 100, 0.0)",
        (source_id,),
    )
    conn.commit()
    file_id = conn.execute("SELECT id FROM files WHERE path='/src/v.mp4'").fetchone()["id"]

    insert_video_frame(conn, file_id, 0, 1000, "abc123", "frame desc", "vision-model")
    insert_video_frame(conn, file_id, 1, 2000, "def456", "frame desc 2", "vision-model")
    conn.commit()

    rows = conn.execute("SELECT * FROM video_frames WHERE file_id=?", (file_id,)).fetchall()
    assert len(rows) == 2
    assert rows[0]["timestamp_ms"] == 1000
    assert rows[1]["frame_index"] == 1
    conn.close()


def test_delete_video_frames_for_file(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()

    for name in ["v1.mp4", "v2.mp4"]:
        conn.execute(
            f"INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (?, '/src/{name}', '{name}', '.mp4', 'video', 100, 0.0)",
            (source_id,),
        )
    conn.commit()

    fid1 = conn.execute("SELECT id FROM files WHERE path='/src/v1.mp4'").fetchone()["id"]
    fid2 = conn.execute("SELECT id FROM files WHERE path='/src/v2.mp4'").fetchone()["id"]

    insert_video_frame(conn, fid1, 0, 1000, None, "desc", "m")
    insert_video_frame(conn, fid2, 0, 1000, None, "desc", "m")
    conn.commit()

    delete_video_frames_for_file(conn, fid1)
    conn.commit()

    remaining = conn.execute("SELECT file_id FROM video_frames").fetchall()
    assert all(r["file_id"] == fid2 for r in remaining)
    conn.close()


# ---------------------------------------------------------------------------
# reset_describe_to_pending
# ---------------------------------------------------------------------------

def test_reset_describe_to_pending(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    source_id = conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",)
    ).lastrowid
    conn.commit()

    for name in ["a.jpg", "b.jpg"]:
        conn.execute(
            f"INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (?, '/src/{name}', '{name}', '.jpg', 'image', 100, 0.0)",
            (source_id,),
        )
    conn.commit()

    for row in conn.execute("SELECT id FROM files").fetchall():
        upsert_description(conn, row["id"], "desc", "desc", "m", "done")
    conn.commit()

    count = reset_describe_to_pending(conn)
    assert count == 2

    statuses = conn.execute("SELECT pass1_status FROM descriptions").fetchall()
    assert all(r["pass1_status"] == "pending" for r in statuses)
    conn.close()


# ---------------------------------------------------------------------------
# Description normalization
# ---------------------------------------------------------------------------

def test_description_normalization_applies_rule(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    kb_conn.execute(
        "INSERT INTO substitute_rules (pattern, replacement, applies_to)"
        " VALUES (?, ?, ?)",
        (r"\bhighway\b", "Highway 1", "description"),
    )
    kb_conn.commit()

    result = _normalize_description("Driving along the highway today.", kb_conn)
    assert "Highway 1" in result
    assert "highway" not in result
    kb_conn.close()


def test_description_normalization_no_rules_returns_raw(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    raw = "No rules to apply here."
    result = _normalize_description(raw, kb_conn)
    assert result == raw
    kb_conn.close()


def test_description_normalization_filename_rule_skipped(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    kb_conn.execute(
        "INSERT INTO substitute_rules (pattern, replacement, applies_to)"
        " VALUES (?, ?, ?)",
        (r"\bhwy\b", "Highway", "filename"),  # applies_to='filename' — should NOT apply to descriptions
    )
    kb_conn.commit()

    result = _normalize_description("Along the hwy today.", kb_conn)
    assert "hwy" in result  # rule was not applied
    kb_conn.close()


def test_description_normalization_both_scope_applies(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    kb_conn.execute(
        "INSERT INTO substitute_rules (pattern, replacement, applies_to)"
        " VALUES (?, ?, ?)",
        (r"\bTCH\b", "Trans-Canada Highway", "both"),
    )
    kb_conn.commit()

    result = _normalize_description("Driving on TCH today.", kb_conn)
    assert "Trans-Canada Highway" in result
    kb_conn.close()
