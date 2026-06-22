"""Unit tests for KB.Q2 — location registry DB helpers and near-duplicate logic."""
import sqlite3

import pytest

from src.db.kb import (
    create_entity_table,
    delete_entity_table_entry,
    find_location_near_duplicates,
    get_entity_location_tables,
    get_entity_table_entries,
    merge_entity_table_entries,
    open_kb,
    register_entity_table,
    update_entity_table_entry,
    upsert_entity_row,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_kb(tmp_path):
    kb_path = tmp_path / "knowledge.db"
    conn = open_kb(kb_path)
    return conn


def _seed_loc_table(conn: sqlite3.Connection, table_name: str = "cluster_locs") -> None:
    create_entity_table(conn, table_name, ["location", "latitude", "longitude", "threshold_m"], "location")
    register_entity_table(
        conn,
        table_name=table_name,
        display_name="Test Locs",
        trigger_word="",
        trigger_aliases_json="[]",
        key_column="location",
        match_type="gps",
    )


def _seed_row(conn, table_name, location, lat=None, lon=None, threshold_m=None):
    upsert_entity_row(conn, table_name, {
        "location": location,
        "latitude": str(lat) if lat is not None else "",
        "longitude": str(lon) if lon is not None else "",
        "threshold_m": str(threshold_m) if threshold_m is not None else "",
    })
    conn.commit()


# ---------------------------------------------------------------------------
# get_entity_location_tables
# ---------------------------------------------------------------------------

def test_get_entity_location_tables_returns_tables(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    tables = get_entity_location_tables(conn)
    names = [t["name"] for t in tables]
    assert "entity_cluster_locs" in names
    conn.close()


def test_get_entity_location_tables_filters_no_location_col(tmp_path):
    conn = _make_kb(tmp_path)
    # Table with no 'location' column
    create_entity_table(conn, "no_loc", ["name", "code"], "name")
    register_entity_table(conn, table_name="no_loc", display_name="No Loc",
                          trigger_word="", trigger_aliases_json="[]",
                          key_column="name", match_type="gps")
    tables = get_entity_location_tables(conn)
    names = [t["name"] for t in tables]
    assert "entity_no_loc" not in names
    conn.close()


# ---------------------------------------------------------------------------
# get_entity_table_entries
# ---------------------------------------------------------------------------

def test_get_entity_table_entries_returns_rows(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    _seed_row(conn, "cluster_locs", "Edinburgh", lat=55.95, lon=-3.19, threshold_m=500)
    _seed_row(conn, "cluster_locs", "Glasgow", lat=55.86, lon=-4.25, threshold_m=1000)
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    assert len(rows) == 2
    locs = [r["location"] for r in rows]
    assert "Edinburgh" in locs
    assert "Glasgow" in locs
    conn.close()


def test_get_entity_table_entries_unknown_table_raises(tmp_path):
    conn = _make_kb(tmp_path)
    with pytest.raises(ValueError):
        get_entity_table_entries(conn, "entity_nonexistent")
    conn.close()


# ---------------------------------------------------------------------------
# update_entity_table_entry
# ---------------------------------------------------------------------------

def test_update_entity_table_entry_valid(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    _seed_row(conn, "cluster_locs", "Old Name")
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    eid = rows[0]["id"]
    updated = update_entity_table_entry(conn, "entity_cluster_locs", eid, {"location": "New Name"})
    assert updated["location"] == "New Name"
    conn.close()


def test_update_entity_table_entry_unknown_table(tmp_path):
    conn = _make_kb(tmp_path)
    with pytest.raises(ValueError):
        update_entity_table_entry(conn, "entity_ghost", 1, {"location": "X"})
    conn.close()


def test_update_entity_table_entry_unknown_id(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    with pytest.raises(ValueError):
        update_entity_table_entry(conn, "entity_cluster_locs", 9999, {"location": "X"})
    conn.close()


# ---------------------------------------------------------------------------
# delete_entity_table_entry
# ---------------------------------------------------------------------------

def test_delete_entity_table_entry_removes_row(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    _seed_row(conn, "cluster_locs", "ToDelete")
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    eid = rows[0]["id"]
    delete_entity_table_entry(conn, "entity_cluster_locs", eid)
    rows_after = get_entity_table_entries(conn, "entity_cluster_locs")
    assert len(rows_after) == 0
    conn.close()


def test_delete_entity_table_entry_unknown_id(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    with pytest.raises(ValueError):
        delete_entity_table_entry(conn, "entity_cluster_locs", 9999)
    conn.close()


# ---------------------------------------------------------------------------
# merge_entity_table_entries
# ---------------------------------------------------------------------------

def test_merge_entity_table_entries_backfills_nulls(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    # keep has no lat/lon; drop has lat/lon
    _seed_row(conn, "cluster_locs", "Keep Entry")
    _seed_row(conn, "cluster_locs", "Drop Entry", lat=55.0, lon=-3.0, threshold_m=500)
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    keep_id = next(r["id"] for r in rows if r["location"] == "Keep Entry")
    drop_id = next(r["id"] for r in rows if r["location"] == "Drop Entry")
    merge_entity_table_entries(conn, "entity_cluster_locs", keep_id, drop_id)
    rows_after = get_entity_table_entries(conn, "entity_cluster_locs")
    assert len(rows_after) == 1
    kept = rows_after[0]
    assert kept["location"] == "Keep Entry"
    assert kept["latitude"] == "55.0"
    assert kept["longitude"] == "-3.0"
    conn.close()


def test_merge_entity_table_entries_same_id_raises(tmp_path):
    conn = _make_kb(tmp_path)
    _seed_loc_table(conn)
    _seed_row(conn, "cluster_locs", "Entry A")
    rows = get_entity_table_entries(conn, "entity_cluster_locs")
    eid = rows[0]["id"]
    with pytest.raises(ValueError):
        merge_entity_table_entries(conn, "entity_cluster_locs", eid, eid)
    conn.close()


def test_merge_entity_table_entries_unknown_table(tmp_path):
    conn = _make_kb(tmp_path)
    with pytest.raises(ValueError):
        merge_entity_table_entries(conn, "entity_ghost", 1, 2)
    conn.close()


# ---------------------------------------------------------------------------
# find_location_near_duplicates
# ---------------------------------------------------------------------------

def test_near_dup_above_threshold_detected():
    entries = [
        {"id": 1, "location": "Edinburgh Castle"},
        {"id": 2, "location": "Edinburh Castle"},  # typo
    ]
    results = find_location_near_duplicates(entries, threshold=0.85)
    assert len(results) == 1
    assert results[0]["a_id"] == 1
    assert results[0]["b_id"] == 2
    assert results[0]["score"] >= 0.85


def test_near_dup_below_threshold_not_returned():
    entries = [
        {"id": 1, "location": "Edinburgh"},
        {"id": 2, "location": "Glasgow"},
    ]
    results = find_location_near_duplicates(entries, threshold=0.85)
    assert results == []


def test_near_dup_normalisation_lowercase():
    entries = [
        {"id": 1, "location": "EDINBURGH CASTLE"},
        {"id": 2, "location": "edinburgh castle"},
    ]
    # Exact after normalisation → not flagged
    results = find_location_near_duplicates(entries, threshold=0.85)
    assert results == []


def test_near_dup_normalisation_punctuation():
    # Punctuation stripped: "St. Andrew's" → "st andrews"; "St. Andrews" → "st andrews"
    # They become identical after normalization → correctly skipped (not flagged as near-dup)
    entries = [
        {"id": 1, "location": "St. Andrew's Cathedral"},
        {"id": 2, "location": "St. Andrews Cathedral"},
    ]
    results = find_location_near_duplicates(entries, threshold=0.85)
    assert results == []


def test_near_dup_empty_table():
    results = find_location_near_duplicates([])
    assert results == []
