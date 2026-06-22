"""Unit tests for KB.Q1 knowledge locations DB helpers and promote logic."""
import sqlite3

from src.db.corpus import (
    get_gps_cluster_with_assignments,
    open_corpus,
    rename_gps_cluster,
)
from src.db.kb import (
    create_entity_table,
    get_entity_table_rows,
    open_kb,
    register_entity_table,
    upsert_entity_row,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_cluster(conn: sqlite3.Connection, label: str, lat: float, lon: float) -> int:
    cur = conn.execute(
        "INSERT INTO gps_clusters (label, centroid_lat, centroid_lon, file_count, eps_km, min_samples)"
        " VALUES (?, ?, ?, 3, 1.0, 2)",
        (label, lat, lon),
    )
    conn.commit()
    return cur.lastrowid


def _seed_file_in_cluster(conn: sqlite3.Connection, cluster_id: int, path: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES ('src', 'all', 1)"
    )
    conn.commit()
    src_id = conn.execute("SELECT id FROM sources WHERE path='src'").fetchone()["id"]
    conn.execute(
        "INSERT INTO files (source_id, path, filename) VALUES (?, ?, 'f.jpg')",
        (src_id, path),
    )
    file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO file_gps_cluster_assignments (file_id, cluster_id, distance_m)"
        " VALUES (?, ?, 50.0)",
        (file_id, cluster_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# rename_gps_cluster
# ---------------------------------------------------------------------------

def test_rename_gps_cluster_updates_label(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    cid = _seed_cluster(conn, "Old Label", 55.9, -3.2)
    rename_gps_cluster(conn, cid, "New Label")
    row = conn.execute("SELECT label FROM gps_clusters WHERE id=?", (cid,)).fetchone()
    assert row["label"] == "New Label"
    conn.close()


def test_rename_gps_cluster_missing_id_noop(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    # No clusters seeded — should not raise
    rename_gps_cluster(conn, 9999, "Ghost")
    assert conn.execute("SELECT COUNT(*) FROM gps_clusters").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# get_gps_cluster_with_assignments
# ---------------------------------------------------------------------------

def test_get_gps_cluster_with_assignments_returns_data(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    cid = _seed_cluster(conn, "Edinburgh", 55.95, -3.19)
    _seed_file_in_cluster(conn, cid, "/ed/1.jpg")
    _seed_file_in_cluster(conn, cid, "/ed/2.jpg")
    result = get_gps_cluster_with_assignments(conn, cid)
    conn.close()
    assert result["label"] == "Edinburgh"
    assert len(result["file_paths"]) == 2
    assert "/ed/1.jpg" in result["file_paths"]


def test_get_gps_cluster_with_assignments_missing_id(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    result = get_gps_cluster_with_assignments(conn, 9999)
    conn.close()
    assert result == {}


# ---------------------------------------------------------------------------
# Promote logic (create_entity_table + upsert_entity_row)
# ---------------------------------------------------------------------------

def _promote(kb_conn: sqlite3.Connection, label: str, lat: float, lon: float,
              eps_km: float = 1.0, file_count: int = 3) -> None:
    create_entity_table(
        kb_conn, "gps_cluster_locations",
        ["location", "latitude", "longitude", "threshold_m", "file_count"],
        "location",
    )
    register_entity_table(
        kb_conn,
        table_name="gps_cluster_locations",
        display_name="GPS Cluster Locations",
        trigger_word="",
        trigger_aliases_json="[]",
        key_column="location",
        match_type="gps",
        source_csv="gps_clusters",
    )
    upsert_entity_row(kb_conn, "gps_cluster_locations", {
        "location": label,
        "latitude": str(lat),
        "longitude": str(lon),
        "threshold_m": str(eps_km * 1000),
        "file_count": str(file_count),
    })
    kb_conn.commit()


def test_promote_creates_entity_row(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _promote(kb_conn, "Edinburgh", 55.95, -3.19)
    rows = get_entity_table_rows(kb_conn, "gps_cluster_locations")
    kb_conn.close()
    assert len(rows) == 1
    assert rows[0]["location"] == "Edinburgh"
    assert rows[0]["latitude"] == "55.95"


def test_promote_is_idempotent(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    _promote(kb_conn, "Edinburgh", 55.95, -3.19, file_count=3)
    _promote(kb_conn, "Edinburgh", 55.95, -3.19, file_count=5)  # second call updates count
    rows = get_entity_table_rows(kb_conn, "gps_cluster_locations")
    kb_conn.close()
    assert len(rows) == 1
    assert rows[0]["file_count"] == "5"
