"""Unit tests for src/stages/validate.py — status classification logic and DB helpers."""
import hashlib
import sqlite3

from src.stages.validate import _classify_file, _sha256_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(file_id: int, path: str, sha256: str | None) -> dict:
    return {"id": file_id, "path": path, "sha256": sha256}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# _sha256_file
# ---------------------------------------------------------------------------

def test_sha256_file_correct(tmp_path):
    data = b"hello world"
    f = tmp_path / "file.txt"
    f.write_bytes(data)
    assert _sha256_file(f) == _sha256_bytes(data)


def test_sha256_file_large(tmp_path):
    data = b"x" * 200_000
    f = tmp_path / "large.bin"
    f.write_bytes(data)
    assert _sha256_file(f) == _sha256_bytes(data)


# ---------------------------------------------------------------------------
# _classify_file — ok
# ---------------------------------------------------------------------------

def test_classify_ok_matching_hash(tmp_path):
    data = b"content"
    f = tmp_path / "img.jpg"
    f.write_bytes(data)
    h = _sha256_bytes(data)
    row = _make_row(1, str(f), h)
    status, detail = _classify_file(row, {h: [(1, str(f))]})
    assert status == "ok"
    assert detail is None


def test_classify_ok_no_stored_hash(tmp_path):
    f = tmp_path / "img.jpg"
    f.write_bytes(b"anything")
    row = _make_row(1, str(f), None)
    status, detail = _classify_file(row, {})
    assert status == "ok"
    assert detail is None


# ---------------------------------------------------------------------------
# _classify_file — changed
# ---------------------------------------------------------------------------

def test_classify_changed_hash_mismatch(tmp_path):
    f = tmp_path / "img.jpg"
    f.write_bytes(b"new content")
    old_hash = _sha256_bytes(b"old content")
    new_hash = _sha256_bytes(b"new content")
    row = _make_row(1, str(f), old_hash)
    status, detail = _classify_file(row, {old_hash: [(1, str(f))]})
    assert status == "changed"
    assert detail == new_hash


# ---------------------------------------------------------------------------
# _classify_file — moved
# ---------------------------------------------------------------------------

def test_classify_moved_when_hash_found_at_new_path(tmp_path):
    original = tmp_path / "original.jpg"
    new_loc = tmp_path / "moved.jpg"
    data = b"same content"
    h = _sha256_bytes(data)
    new_loc.write_bytes(data)
    # original no longer exists; new_loc does
    row = _make_row(1, str(original), h)
    lookup = {h: [(1, str(original)), (2, str(new_loc))]}
    status, detail = _classify_file(row, lookup)
    assert status == "moved"
    assert detail == str(new_loc)


def test_classify_moved_ignores_self(tmp_path):
    f = tmp_path / "file.jpg"
    # File does not exist on disk
    h = _sha256_bytes(b"data")
    # Only self in lookup — no other file at this hash exists
    row = _make_row(1, str(f), h)
    lookup = {h: [(1, str(f))]}
    status, detail = _classify_file(row, lookup)
    assert status == "missing"


# ---------------------------------------------------------------------------
# _classify_file — missing
# ---------------------------------------------------------------------------

def test_classify_missing_no_hash(tmp_path):
    row = _make_row(1, str(tmp_path / "gone.jpg"), None)
    status, detail = _classify_file(row, {})
    assert status == "missing"
    assert detail is None


def test_classify_missing_hash_not_found_elsewhere(tmp_path):
    h = _sha256_bytes(b"unique")
    row = _make_row(1, str(tmp_path / "gone.jpg"), h)
    lookup = {h: [(1, str(tmp_path / "gone.jpg"))]}
    status, detail = _classify_file(row, lookup)
    assert status == "missing"


def test_classify_missing_candidate_path_not_on_disk(tmp_path):
    h = _sha256_bytes(b"data")
    row = _make_row(1, str(tmp_path / "gone.jpg"), h)
    # Other file in corpus has same hash but also doesn't exist on disk
    lookup = {h: [(1, str(tmp_path / "gone.jpg")), (2, str(tmp_path / "also_gone.jpg"))]}
    status, detail = _classify_file(row, lookup)
    assert status == "missing"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE validation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            files_checked INTEGER NOT NULL DEFAULT 0,
            ok_count INTEGER NOT NULL DEFAULT 0,
            changed_count INTEGER NOT NULL DEFAULT 0,
            moved_count INTEGER NOT NULL DEFAULT 0,
            missing_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE validation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT
        )
        """
    )
    return conn


def test_insert_validation_run():
    from src.db.corpus import insert_validation_run

    conn = _open_mem()
    run_id = insert_validation_run(conn, "2026-06-21T00:00:00Z", 10, 8, 1, 0, 1)
    assert isinstance(run_id, int)
    row = conn.execute("SELECT * FROM validation_runs WHERE id=?", (run_id,)).fetchone()
    assert row["files_checked"] == 10
    assert row["ok_count"] == 8
    assert row["changed_count"] == 1
    assert row["missing_count"] == 1
    conn.close()


def test_insert_validation_result():
    from src.db.corpus import insert_validation_result

    conn = _open_mem()
    conn.execute(
        "INSERT INTO validation_runs (run_at, files_checked, ok_count, changed_count, moved_count, missing_count)"
        " VALUES ('2026-06-21', 0, 0, 0, 0, 0)"
    )
    conn.commit()
    run_id = conn.execute("SELECT MAX(id) FROM validation_runs").fetchone()[0]

    insert_validation_result(conn, run_id, 42, "changed", "abc123")
    conn.commit()
    row = conn.execute("SELECT * FROM validation_results WHERE file_id=42").fetchone()
    assert row["status"] == "changed"
    assert row["detail"] == "abc123"
    conn.close()


def test_get_latest_validation_summary_none():
    from src.db.corpus import get_latest_validation_summary

    conn = _open_mem()
    assert get_latest_validation_summary(conn) is None
    conn.close()


def test_get_latest_validation_summary_returns_last():
    from src.db.corpus import get_latest_validation_summary

    conn = _open_mem()
    conn.execute(
        "INSERT INTO validation_runs (run_at, files_checked, ok_count, changed_count, moved_count, missing_count)"
        " VALUES ('2026-06-20', 5, 5, 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO validation_runs (run_at, files_checked, ok_count, changed_count, moved_count, missing_count)"
        " VALUES ('2026-06-21', 10, 9, 1, 0, 0)"
    )
    conn.commit()
    summary = get_latest_validation_summary(conn)
    assert summary is not None
    assert summary["files_checked"] == 10
    assert summary["changed_count"] == 1
    conn.close()


def test_insert_validation_result_null_detail():
    from src.db.corpus import insert_validation_result

    conn = _open_mem()
    conn.execute(
        "INSERT INTO validation_runs (run_at, files_checked, ok_count, changed_count, moved_count, missing_count)"
        " VALUES ('2026-06-21', 0, 0, 0, 0, 0)"
    )
    conn.commit()
    run_id = conn.execute("SELECT MAX(id) FROM validation_runs").fetchone()[0]
    insert_validation_result(conn, run_id, 99, "missing", None)
    conn.commit()
    row = conn.execute("SELECT * FROM validation_results WHERE file_id=99").fetchone()
    assert row["status"] == "missing"
    assert row["detail"] is None
    conn.close()
