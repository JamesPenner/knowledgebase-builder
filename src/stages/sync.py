"""KB sync utilities: version stamps, staleness detection, dirty-set computation."""
import sqlite3


def get_current_kb_version(kb_conn: sqlite3.Connection) -> int | None:
    row = kb_conn.execute("SELECT MAX(id) AS v FROM kb_version").fetchone()
    return row["v"] if row else None


def get_stale_files(corpus_conn: sqlite3.Connection, kb_conn: sqlite3.Connection) -> list[sqlite3.Row]:
    from src.db.corpus import get_stale_files_for_writeback
    current_version = get_current_kb_version(kb_conn)
    return get_stale_files_for_writeback(corpus_conn, current_version)


def mark_files_written(
    corpus_conn: sqlite3.Connection,
    file_ids: list[int],
    version_id: int,
) -> None:
    from src.db.corpus import update_writeback_kb_version
    update_writeback_kb_version(corpus_conn, file_ids, version_id)
    corpus_conn.commit()
