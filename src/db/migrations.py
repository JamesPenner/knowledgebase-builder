import sqlite3
from pathlib import Path


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "id TEXT PRIMARY KEY, "
        "applied_at DATETIME DEFAULT (datetime('now')))"
    )
    conn.commit()

    applied = {row[0] for row in conn.execute("SELECT id FROM _migrations")}
    pending = sorted(p for p in migrations_dir.glob("*.sql") if p.stem not in applied)

    for path in pending:
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute("INSERT INTO _migrations (id) VALUES (?)", (path.stem,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(f"Migration {path.name} failed: {exc}") from exc
