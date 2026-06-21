import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kbs (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    name       TEXT     NOT NULL UNIQUE,
    path       TEXT     NOT NULL,
    is_active  INTEGER  NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT (datetime('now'))
);
"""


def open_registry(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(root / "registry.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def register_kb(conn: sqlite3.Connection, name: str, path: Path) -> None:
    try:
        conn.execute(
            "INSERT INTO kbs (name, path) VALUES (?, ?)",
            (name, str(path)),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"KB '{name}' already registered") from exc


def get_kb_path(conn: sqlite3.Connection, name: str) -> Path:
    row = conn.execute("SELECT path FROM kbs WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"KB '{name}' not found in registry")
    return Path(row["path"])


def list_kbs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT name, path, is_active, created_at FROM kbs ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def set_active(conn: sqlite3.Connection, name: str) -> None:
    get_kb_path(conn, name)  # validates name exists
    conn.execute("UPDATE kbs SET is_active = 0")
    conn.execute("UPDATE kbs SET is_active = 1 WHERE name = ?", (name,))
    conn.commit()


def get_active_kb_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute(
        "SELECT path FROM kbs WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return Path(row["path"]) if row else None


def delete_kb(conn: sqlite3.Connection, name: str) -> str:
    """Remove KB from registry; returns its path string. Raises ValueError if not found."""
    row = conn.execute("SELECT path FROM kbs WHERE name=?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"KB '{name}' not found in registry")
    path = row["path"]
    conn.execute("DELETE FROM kbs WHERE name=?", (name,))
    conn.commit()
    return path
