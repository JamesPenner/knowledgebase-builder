"""Integration tests for the corpus file validation pipeline (KB.P23)."""
import hashlib
import threading

from src.db.corpus import (
    get_latest_validation_summary,
    open_corpus,
)
from src.pipeline.progress import NullProgressReporter
from src.stages.validate import run_validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seed_file(corpus_conn, source_id: int, path: str, sha256: str | None):
    corpus_conn.execute(
        "INSERT OR IGNORE INTO files (source_id, path, filename, sha256) VALUES (?, ?, ?, ?)",
        (source_id, path, path.split("/")[-1], sha256),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def _seed_source(corpus_conn, path: str = "/fake/source") -> int:
    corpus_conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", (path,)
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT id FROM sources WHERE path=?", (path,)).fetchone()["id"]


def _cancel() -> threading.Event:
    return threading.Event()


# ---------------------------------------------------------------------------
# Happy path — all ok
# ---------------------------------------------------------------------------

def test_validate_all_ok(tmp_path):
    data = b"image data"
    f = tmp_path / "photo.jpg"
    f.write_bytes(data)
    h = _sha256(data)

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, str(f), h)
    conn.close()

    kb_folder = tmp_path / "kb"
    result = run_validate(corpus_path, kb_folder, NullProgressReporter(), _cancel())

    assert result["ok"] == 1
    assert result["changed"] == 0
    assert result["moved"] == 0
    assert result["missing"] == 0

    conn = open_corpus(corpus_path)
    summary = get_latest_validation_summary(conn)
    assert summary is not None
    assert summary["ok_count"] == 1
    assert summary["files_checked"] == 1
    conn.close()


# ---------------------------------------------------------------------------
# Changed file
# ---------------------------------------------------------------------------

def test_validate_changed_file(tmp_path):
    old_data = b"original content"
    new_data = b"modified content"
    f = tmp_path / "photo.jpg"
    f.write_bytes(new_data)          # file on disk now has new content
    stored_hash = _sha256(old_data)  # corpus recorded the old hash

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    file_id = _seed_file(conn, src_id, str(f), stored_hash)
    conn.close()

    result = run_validate(corpus_path, tmp_path / "kb", NullProgressReporter(), _cancel())
    assert result["changed"] == 1
    assert result["ok"] == 0

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT * FROM validation_results WHERE file_id=? AND status='changed'", (file_id,)
    ).fetchone()
    assert row is not None
    assert row["detail"] == _sha256(new_data)
    conn.close()


# ---------------------------------------------------------------------------
# Moved file
# ---------------------------------------------------------------------------

def test_validate_moved_file(tmp_path):
    data = b"my photo"
    h = _sha256(data)

    original = tmp_path / "original.jpg"
    new_loc = tmp_path / "moved.jpg"
    new_loc.write_bytes(data)  # file exists at new location, not original

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    file_id_orig = _seed_file(conn, src_id, str(original), h)
    _seed_file(conn, src_id, str(new_loc), h)
    conn.close()

    result = run_validate(corpus_path, tmp_path / "kb", NullProgressReporter(), _cancel())
    # original is missing from its recorded path → moved (new_loc has the same hash and exists)
    assert result["moved"] == 1
    # new_loc exists and matches its hash → ok
    assert result["ok"] == 1

    conn = open_corpus(corpus_path)
    moved_row = conn.execute(
        "SELECT * FROM validation_results WHERE file_id=? AND status='moved'", (file_id_orig,)
    ).fetchone()
    assert moved_row is not None
    assert moved_row["detail"] == str(new_loc)
    conn.close()


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------

def test_validate_missing_file(tmp_path):
    h = _sha256(b"gone")
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    file_id = _seed_file(conn, src_id, str(tmp_path / "gone.jpg"), h)
    conn.close()

    result = run_validate(corpus_path, tmp_path / "kb", NullProgressReporter(), _cancel())
    assert result["missing"] == 1
    assert result["ok"] == 0

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT * FROM validation_results WHERE file_id=? AND status='missing'", (file_id,)
    ).fetchone()
    assert row is not None
    conn.close()


# ---------------------------------------------------------------------------
# Mixed corpus
# ---------------------------------------------------------------------------

def test_validate_mixed_results(tmp_path):
    ok_data = b"ok file"
    ok_file = tmp_path / "ok.jpg"
    ok_file.write_bytes(ok_data)

    changed_file = tmp_path / "changed.jpg"
    changed_file.write_bytes(b"new bytes")

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, str(ok_file), _sha256(ok_data))
    _seed_file(conn, src_id, str(changed_file), _sha256(b"old bytes"))
    _seed_file(conn, src_id, str(tmp_path / "gone.jpg"), _sha256(b"gone"))
    conn.close()

    result = run_validate(corpus_path, tmp_path / "kb", NullProgressReporter(), _cancel())
    assert result["ok"] == 1
    assert result["changed"] == 1
    assert result["missing"] == 1
    assert result["moved"] == 0


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def test_validate_export_csv(tmp_path):
    changed_file = tmp_path / "changed.jpg"
    changed_file.write_bytes(b"new content")

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, str(changed_file), _sha256(b"old content"))
    conn.close()

    kb_folder = tmp_path / "kb"
    run_validate(corpus_path, kb_folder, NullProgressReporter(), _cancel(), export=True)

    report = kb_folder / "export" / "validation_report.csv"
    assert report.exists()
    content = report.read_text(encoding="utf-8")
    assert "changed" in content
    assert str(changed_file) in content


def test_validate_export_csv_excludes_ok_files(tmp_path):
    data = b"ok content"
    f = tmp_path / "ok.jpg"
    f.write_bytes(data)

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, str(f), _sha256(data))
    conn.close()

    kb_folder = tmp_path / "kb"
    run_validate(corpus_path, kb_folder, NullProgressReporter(), _cancel(), export=True)

    report = kb_folder / "export" / "validation_report.csv"
    # Only header line — no data rows since everything is ok
    lines = [l for l in report.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# Schema migration — tables exist
# ---------------------------------------------------------------------------

def test_validation_tables_exist_after_open(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "validation_runs" in tables
    assert "validation_results" in tables
    conn.close()


# ---------------------------------------------------------------------------
# Resume on restart — second run creates a new run record
# ---------------------------------------------------------------------------

def test_validate_resume_creates_new_run(tmp_path):
    data = b"content"
    f = tmp_path / "img.jpg"
    f.write_bytes(data)

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, str(f), _sha256(data))
    conn.close()

    kb_folder = tmp_path / "kb"
    run_validate(corpus_path, kb_folder, NullProgressReporter(), _cancel())
    run_validate(corpus_path, kb_folder, NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    count = conn.execute("SELECT COUNT(*) FROM validation_runs").fetchone()[0]
    assert count == 2
    conn.close()


# ---------------------------------------------------------------------------
# Health check integration
# ---------------------------------------------------------------------------

def test_health_check_no_run(tmp_path):
    from src.health import _check_validation_freshness

    conn = open_corpus(tmp_path / "corpus.db")
    chk = _check_validation_freshness(conn)
    assert chk.ok is True
    assert "consider running" in chk.detail
    conn.close()


def test_health_check_after_clean_run(tmp_path):
    from src.health import _check_validation_freshness

    data = b"file"
    f = tmp_path / "f.jpg"
    f.write_bytes(data)
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, str(f), _sha256(data))
    conn.close()

    run_validate(corpus_path, tmp_path / "kb", NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    chk = _check_validation_freshness(conn)
    assert chk.ok is True
    assert "all ok" in chk.detail
    conn.close()


def test_health_check_after_dirty_run(tmp_path):
    from src.health import _check_validation_freshness

    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, str(tmp_path / "gone.jpg"), _sha256(b"data"))
    conn.close()

    run_validate(corpus_path, tmp_path / "kb", NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    chk = _check_validation_freshness(conn)
    assert chk.ok is False
    assert chk.severity == "warning"
    assert "missing" in chk.detail
    conn.close()
