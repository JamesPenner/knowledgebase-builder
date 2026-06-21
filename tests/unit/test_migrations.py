import sqlite3
from pathlib import Path

import pytest

from src.db.migrations import apply_migrations


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrations_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    for name, sql in files.items():
        (d / name).write_text(sql, encoding="utf-8")
    return d


def test_migrations_table_created_by_first_migration(tmp_path):
    d = _migrations_dir(tmp_path, {"0001_init.sql": "CREATE TABLE t (id INTEGER PRIMARY KEY);"})
    conn = _make_db()
    apply_migrations(conn, d)

    row = conn.execute("SELECT id FROM _migrations").fetchone()
    assert row[0] == "0001_init"


def test_migration_applied_only_once(tmp_path):
    d = _migrations_dir(tmp_path, {"0001_init.sql": "CREATE TABLE t (id INTEGER PRIMARY KEY);"})
    conn = _make_db()
    apply_migrations(conn, d)
    apply_migrations(conn, d)

    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == 1


def test_pending_migration_applied_after_partial_run(tmp_path):
    d = _migrations_dir(tmp_path, {
        "0001_first.sql": "CREATE TABLE a (id INTEGER PRIMARY KEY);",
        "0002_second.sql": "CREATE TABLE b (id INTEGER PRIMARY KEY);",
    })
    conn = _make_db()

    # Manually apply only the first migration
    conn.execute("CREATE TABLE IF NOT EXISTS _migrations (id TEXT PRIMARY KEY, applied_at DATETIME DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO _migrations (id) VALUES ('0001_first')")
    conn.commit()

    apply_migrations(conn, d)

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "b" in tables
    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == 2


def test_failed_migration_rolls_back(tmp_path):
    d = _migrations_dir(tmp_path, {
        "0001_good.sql": "CREATE TABLE good (id INTEGER PRIMARY KEY);",
        "0002_bad.sql": "THIS IS NOT VALID SQL ;;;",
    })
    conn = _make_db()

    with pytest.raises(RuntimeError):
        apply_migrations(conn, d)

    # 0001 committed before 0002 was attempted — table must exist
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "good" in tables
    # 0002 was never recorded as applied
    applied = {r[0] for r in conn.execute("SELECT id FROM _migrations")}
    assert "0002_bad" not in applied


def test_error_message_includes_filename(tmp_path):
    d = _migrations_dir(tmp_path, {"0001_broken.sql": "INVALID;"})
    conn = _make_db()

    with pytest.raises(RuntimeError, match="0001_broken.sql"):
        apply_migrations(conn, d)
