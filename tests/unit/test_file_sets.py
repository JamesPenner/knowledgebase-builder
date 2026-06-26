import sqlite3
import pytest

from src.db.corpus import (
    add_source,
    create_file_set,
    delete_file_set,
    get_file_sets,
    open_corpus,
    remove_source,
    resolve_set_file_ids,
    upsert_file,
)


def _seed(conn: sqlite3.Connection, n: int = 3) -> tuple[int, list[int]]:
    """Insert a source and n files; return (source_id, [file_ids])."""
    src_id = add_source(conn, "/test/source", "all", True)
    file_ids = []
    for i in range(n):
        fid = upsert_file(conn, src_id, f"/test/source/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000 + i, 0.0)
        file_ids.append(fid)
    conn.commit()
    return src_id, file_ids


# ---------------------------------------------------------------------------
# create_file_set / get_file_sets
# ---------------------------------------------------------------------------

def test_create_file_set_returns_id(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _, file_ids = _seed(conn)
    set_id = create_file_set(conn, "vacation", "Summer 2024", file_ids)
    assert isinstance(set_id, int)
    assert set_id > 0


def test_get_file_sets_empty(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    assert get_file_sets(conn) == []


def test_get_file_sets_returns_file_count(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _, file_ids = _seed(conn, 4)
    create_file_set(conn, "s1", "", file_ids[:2])
    create_file_set(conn, "s2", "desc", file_ids)
    sets = get_file_sets(conn)
    by_name = {s["name"]: s for s in sets}
    assert by_name["s1"]["file_count"] == 2
    assert by_name["s2"]["file_count"] == 4


def test_get_file_sets_returns_both_sets(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _, file_ids = _seed(conn)
    create_file_set(conn, "alpha", "", file_ids)
    create_file_set(conn, "beta", "", file_ids)
    sets = get_file_sets(conn)
    assert len(sets) == 2
    names = {s["name"] for s in sets}
    assert names == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# resolve_set_file_ids
# ---------------------------------------------------------------------------

def test_resolve_set_file_ids(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _, file_ids = _seed(conn, 5)
    chosen = file_ids[:3]
    set_id = create_file_set(conn, "subset", "", chosen)
    resolved = resolve_set_file_ids(conn, set_id)
    assert resolved == frozenset(chosen)


def test_resolve_set_file_ids_empty_set(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _seed(conn)
    set_id = create_file_set(conn, "empty", "", [])
    assert resolve_set_file_ids(conn, set_id) == frozenset()


# ---------------------------------------------------------------------------
# delete_file_set
# ---------------------------------------------------------------------------

def test_delete_file_set(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _, file_ids = _seed(conn)
    set_id = create_file_set(conn, "to_delete", "", file_ids)
    delete_file_set(conn, set_id)
    assert get_file_sets(conn) == []
    # Members should also be gone (ON DELETE CASCADE)
    count = conn.execute("SELECT COUNT(*) FROM file_set_members WHERE set_id=?", (set_id,)).fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# Duplicate name rejected
# ---------------------------------------------------------------------------

def test_create_file_set_duplicate_name_raises(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _, file_ids = _seed(conn)
    create_file_set(conn, "dup", "", file_ids)
    with pytest.raises(sqlite3.IntegrityError):
        create_file_set(conn, "dup", "other desc", file_ids)


# ---------------------------------------------------------------------------
# add_source with filters_json
# ---------------------------------------------------------------------------

def test_add_source_stores_filters_json(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    filters = {"glob": "2024-*", "count_limit": 50}
    src_id = add_source(conn, "/photos", "images", True, filters)
    row = conn.execute("SELECT filters_json FROM sources WHERE id=?", (src_id,)).fetchone()
    import json
    stored = json.loads(row["filters_json"])
    assert stored["glob"] == "2024-*"
    assert stored["count_limit"] == 50


def test_add_source_no_filters_defaults_empty(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id = add_source(conn, "/photos", "all", True)
    row = conn.execute("SELECT filters_json FROM sources WHERE id=?", (src_id,)).fetchone()
    import json
    assert json.loads(row["filters_json"]) == {}


# ---------------------------------------------------------------------------
# remove_source
# ---------------------------------------------------------------------------

def test_remove_source_soft_delete(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id, _ = _seed(conn)
    deleted = remove_source(conn, src_id, cascade=False)
    assert deleted == 0
    row = conn.execute("SELECT removed_at FROM sources WHERE id=?", (src_id,)).fetchone()
    assert row["removed_at"] is not None
    # Files still present
    count = conn.execute("SELECT COUNT(*) FROM files WHERE source_id=?", (src_id,)).fetchone()[0]
    assert count == 3


def test_remove_source_cascade_deletes_files(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id, file_ids = _seed(conn, 4)
    deleted = remove_source(conn, src_id, cascade=True)
    assert deleted == 4
    count = conn.execute("SELECT COUNT(*) FROM files WHERE source_id=?", (src_id,)).fetchone()[0]
    assert count == 0
