"""Shared SQLite connection configuration."""
import sqlite3


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply standard PRAGMA settings to a SQLite connection."""
    conn.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA cache_size = -32000;
        PRAGMA temp_store = MEMORY;
    """)
