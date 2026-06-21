"""Unit tests for src/stages/gps_cluster.py."""
import sqlite3

from src.stages.gps_cluster import _name_cluster


# ---------------------------------------------------------------------------
# _name_cluster — label derivation
# ---------------------------------------------------------------------------

def test_name_cluster_uses_custom_region():
    geolabels = {1: ("Glen Coe", "Scotland", "United Kingdom")}
    label = _name_cluster([1], geolabels, 56.6, -4.9)
    assert label == "Glen Coe"


def test_name_cluster_falls_back_to_state():
    geolabels = {1: (None, "Scotland", "United Kingdom")}
    label = _name_cluster([1], geolabels, 56.6, -4.9)
    assert label == "Scotland"


def test_name_cluster_falls_back_to_country():
    geolabels = {1: (None, None, "United Kingdom")}
    label = _name_cluster([1], geolabels, 56.6, -4.9)
    assert label == "United Kingdom"


def test_name_cluster_falls_back_to_coordinates():
    label = _name_cluster([1, 2], {}, 56.123, -4.567)
    assert "56.123" in label
    assert "-4.567" in label


def test_name_cluster_picks_plurality():
    geolabels = {
        1: ("Glen Coe", "Scotland", "United Kingdom"),
        2: ("Glen Coe", "Scotland", "United Kingdom"),
        3: ("Fort William", "Scotland", "United Kingdom"),
    }
    label = _name_cluster([1, 2, 3], geolabels, 56.8, -4.9)
    assert label == "Glen Coe"


def test_name_cluster_empty_string_geolabel_skipped():
    geolabels = {1: ("", "", "United Kingdom")}
    label = _name_cluster([1], geolabels, 51.5, -0.1)
    assert label == "United Kingdom"


def test_name_cluster_partial_members_no_geolabels():
    geolabels = {1: ("Edinburgh", "Scotland", "United Kingdom")}
    label = _name_cluster([1, 2, 3], geolabels, 55.9, -3.2)
    assert label == "Edinburgh"


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

def test_config_gps_cluster_defaults():
    from src.config import Config
    cfg = Config()
    assert cfg.gps_cluster_eps_km == 1.0
    assert cfg.gps_cluster_min_samples == 3


def test_config_gps_cluster_per_kb_override():
    from src.config import load_config
    import tempfile, pathlib, yaml
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "config.yaml"
        p.write_text(
            yaml.dump({"thresholds": {"gps_cluster_eps_km": 5.0, "gps_cluster_min_samples": 2}}),
            encoding="utf-8",
        )
        cfg = load_config(p)
    assert cfg.gps_cluster_eps_km == 5.0
    assert cfg.gps_cluster_min_samples == 2


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_mem_with_tables() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE gps_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            centroid_lat REAL NOT NULL,
            centroid_lon REAL NOT NULL,
            file_count INTEGER NOT NULL DEFAULT 0,
            eps_km REAL NOT NULL,
            min_samples INTEGER NOT NULL,
            created_at DATETIME DEFAULT (datetime('now'))
        );
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE file_gps_cluster_assignments (
            file_id INTEGER PRIMARY KEY,
            cluster_id INTEGER,
            distance_m REAL
        );
        """
    )
    return conn


def test_get_gps_clusters_empty():
    from src.db.corpus import get_gps_clusters
    conn = _open_mem_with_tables()
    assert get_gps_clusters(conn) == []
    conn.close()


def test_get_gps_clusters_returns_rows():
    from src.db.corpus import get_gps_clusters
    conn = _open_mem_with_tables()
    conn.execute(
        "INSERT INTO gps_clusters (label, centroid_lat, centroid_lon, file_count, eps_km, min_samples)"
        " VALUES ('Edinburgh', 55.95, -3.19, 5, 1.0, 3)"
    )
    conn.commit()
    rows = get_gps_clusters(conn)
    assert len(rows) == 1
    assert rows[0]["label"] == "Edinburgh"
    conn.close()


def test_clear_gps_clusters():
    from src.db.corpus import clear_gps_clusters, get_gps_clusters
    conn = _open_mem_with_tables()
    conn.execute(
        "INSERT INTO gps_clusters (label, centroid_lat, centroid_lon, file_count, eps_km, min_samples)"
        " VALUES ('Test', 0.0, 0.0, 1, 1.0, 3)"
    )
    conn.execute("INSERT INTO files VALUES (1, '/a.jpg')")
    conn.execute(
        "INSERT INTO file_gps_cluster_assignments (file_id, cluster_id, distance_m) VALUES (1, 1, 50.0)"
    )
    conn.commit()
    clear_gps_clusters(conn)
    assert get_gps_clusters(conn) == []
    assignments = conn.execute("SELECT * FROM file_gps_cluster_assignments").fetchall()
    assert assignments == []
    conn.close()
