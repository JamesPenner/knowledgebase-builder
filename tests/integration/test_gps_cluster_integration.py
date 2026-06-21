"""Integration tests for GPS cluster analysis (KB.P24).

Tests that require scikit-learn are skipped when it is not installed.
"""
import threading

import pytest

sklearn = pytest.importorskip("sklearn", reason="scikit-learn not installed")

from src.config import Config
from src.db.corpus import (
    get_gps_clusters,
    open_corpus,
)
from src.pipeline.progress import NullProgressReporter
from src.stages.gps_cluster import run_gps_cluster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cancel() -> threading.Event:
    return threading.Event()


def _cfg(**kwargs) -> Config:
    return Config(gps_cluster_eps_km=kwargs.get("eps_km", 1.0),
                  gps_cluster_min_samples=kwargs.get("min_samples", 2))


def _seed_source(conn, path: str = "/src") -> int:
    conn.execute(
        "INSERT OR IGNORE INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", (path,)
    )
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE path=?", (path,)).fetchone()["id"]


def _seed_file(conn, src_id: int, path: str, lat: float, lon: float) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO files (source_id, path, filename) VALUES (?, ?, ?)",
        (src_id, path, path.split("/")[-1]),
    )
    conn.commit()
    file_id = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    for canonical, value in [("exif_gps_lat", str(lat)), ("exif_gps_lon", str(lon))]:
        conn.execute(
            "INSERT OR IGNORE INTO file_metadata_fields (file_id, canonical_name, value)"
            " VALUES (?, ?, ?)",
            (file_id, canonical, value),
        )
    conn.commit()
    return file_id


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_gps_cluster_tables_present(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "gps_clusters" in tables
    assert "file_gps_cluster_assignments" in tables
    conn.close()


# ---------------------------------------------------------------------------
# Happy path — two clusters
# ---------------------------------------------------------------------------

def test_two_distinct_clusters(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    # Edinburgh cluster (~55.95 N, -3.19 W)
    _seed_file(conn, src_id, "/ed/1.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/ed/2.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/ed/3.jpg", 55.952, -3.188)
    # Glasgow cluster (~55.86 N, -4.25 W)
    _seed_file(conn, src_id, "/gl/1.jpg", 55.860, -4.250)
    _seed_file(conn, src_id, "/gl/2.jpg", 55.861, -4.251)
    _seed_file(conn, src_id, "/gl/3.jpg", 55.862, -4.249)
    conn.close()

    result = run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(), NullProgressReporter(), _cancel())
    assert result["clusters"] == 2
    assert result["assigned"] == 6
    assert result["noise"] == 0

    conn = open_corpus(corpus_path)
    clusters = get_gps_clusters(conn)
    assert len(clusters) == 2
    conn.close()


# ---------------------------------------------------------------------------
# Noise files (isolated GPS points)
# ---------------------------------------------------------------------------

def test_noise_files_recorded_with_null_cluster(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    # Three tightly grouped files
    _seed_file(conn, src_id, "/a.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/b.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/c.jpg", 55.952, -3.188)
    # One isolated file far away
    _seed_file(conn, src_id, "/isolated.jpg", 10.000, 20.000)
    conn.close()

    result = run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(min_samples=3), NullProgressReporter(), _cancel())
    assert result["noise"] == 1
    assert result["clusters"] >= 1

    conn = open_corpus(corpus_path)
    noise_row = conn.execute(
        "SELECT a.cluster_id FROM file_gps_cluster_assignments a"
        " JOIN files f ON f.id = a.file_id WHERE f.path = '/isolated.jpg'"
    ).fetchone()
    assert noise_row is not None
    assert noise_row["cluster_id"] is None
    conn.close()


# ---------------------------------------------------------------------------
# Files without GPS are skipped
# ---------------------------------------------------------------------------

def test_files_without_gps_are_skipped(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, "/a.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/b.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/c.jpg", 55.952, -3.188)
    # File with no GPS
    conn.execute(
        "INSERT INTO files (source_id, path, filename) VALUES (?, '/nogps.jpg', 'nogps.jpg')",
        (src_id,),
    )
    conn.commit()
    conn.close()

    result = run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(min_samples=2), NullProgressReporter(), _cancel())
    # /nogps.jpg has no entry in file_metadata_fields for lat/lon so not returned by get_files_with_gps
    total = result["assigned"] + result["noise"]
    assert total == 3


# ---------------------------------------------------------------------------
# Re-run clears and rebuilds
# ---------------------------------------------------------------------------

def test_rerun_clears_and_rebuilds(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, "/a.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/b.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/c.jpg", 55.952, -3.188)
    conn.close()

    run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(min_samples=2), NullProgressReporter(), _cancel())
    run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(min_samples=2), NullProgressReporter(), _cancel())

    conn = open_corpus(corpus_path)
    cluster_count = conn.execute("SELECT COUNT(*) FROM gps_clusters").fetchone()[0]
    assignment_count = conn.execute("SELECT COUNT(*) FROM file_gps_cluster_assignments").fetchone()[0]
    assert cluster_count == 1   # only one cluster, no duplicates from re-run
    assert assignment_count == 3
    conn.close()


# ---------------------------------------------------------------------------
# All-noise corpus (every file isolated)
# ---------------------------------------------------------------------------

def test_all_noise_corpus(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    # 3 files far apart; min_samples=2 so each is noise
    _seed_file(conn, src_id, "/a.jpg", 10.0, 10.0)
    _seed_file(conn, src_id, "/b.jpg", 20.0, 20.0)
    _seed_file(conn, src_id, "/c.jpg", 30.0, 30.0)
    conn.close()

    result = run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(min_samples=2), NullProgressReporter(), _cancel())
    assert result["clusters"] == 0
    assert result["noise"] == 3

    conn = open_corpus(corpus_path)
    assert conn.execute("SELECT COUNT(*) FROM gps_clusters").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM file_gps_cluster_assignments").fetchone()[0] == 3
    conn.close()


# ---------------------------------------------------------------------------
# Empty corpus (no GPS files)
# ---------------------------------------------------------------------------

def test_empty_corpus_returns_zeros(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    open_corpus(corpus_path).close()
    result = run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(), NullProgressReporter(), _cancel())
    assert result == {"clusters": 0, "assigned": 0, "noise": 0}


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def test_export_csv_written(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, "/a.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/b.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/c.jpg", 55.952, -3.188)
    conn.close()

    kb_folder = tmp_path / "kb"
    run_gps_cluster(corpus_path, kb_folder, _cfg(min_samples=2), NullProgressReporter(), _cancel(), export=True)

    report = kb_folder / "export" / "gps_clusters.csv"
    assert report.exists()
    content = report.read_text(encoding="utf-8")
    assert "path" in content
    assert "/a.jpg" in content


def test_export_csv_includes_noise_rows(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, "/a.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/b.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/c.jpg", 55.952, -3.188)
    _seed_file(conn, src_id, "/iso.jpg", 10.0, 20.0)
    conn.close()

    kb_folder = tmp_path / "kb"
    run_gps_cluster(corpus_path, kb_folder, _cfg(min_samples=3), NullProgressReporter(), _cancel(), export=True)

    content = (kb_folder / "export" / "gps_clusters.csv").read_text(encoding="utf-8")
    lines = [l for l in content.splitlines() if l.strip()]
    assert len(lines) == 5  # header + 4 files


# ---------------------------------------------------------------------------
# seed-clusters
# ---------------------------------------------------------------------------

def test_seed_clusters_creates_entity_table(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, "/a.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/b.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/c.jpg", 55.952, -3.188)
    conn.close()

    run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(min_samples=2), NullProgressReporter(), _cancel())

    from src.db.kb import open_kb
    kb_conn = open_kb(kb_path)

    # Seed clusters
    from src.db.corpus import get_gps_clusters
    from src.db.kb import create_entity_table, register_entity_table, upsert_entity_row

    corpus_conn = open_corpus(corpus_path)
    clusters = get_gps_clusters(corpus_conn)
    corpus_conn.close()

    columns = ["location", "latitude", "longitude", "threshold_m", "file_count"]
    create_entity_table(kb_conn, "gps_cluster_locations", columns, "location")
    register_entity_table(
        kb_conn, "gps_cluster_locations", "GPS Cluster Locations", "", "[]", "location", "gps", "gps_clusters"
    )
    for row in clusters:
        upsert_entity_row(kb_conn, "gps_cluster_locations", {
            "location": row["label"],
            "latitude": str(row["centroid_lat"]),
            "longitude": str(row["centroid_lon"]),
            "threshold_m": str(row["eps_km"] * 1000),
            "file_count": str(row["file_count"]),
        })
    kb_conn.commit()

    tables = {r[0] for r in kb_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "entity_gps_cluster_locations" in tables

    rows = kb_conn.execute("SELECT * FROM entity_gps_cluster_locations").fetchall()
    assert len(rows) == 1
    kb_conn.close()


def test_seed_clusters_idempotent(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    src_id = _seed_source(conn)
    _seed_file(conn, src_id, "/a.jpg", 55.950, -3.190)
    _seed_file(conn, src_id, "/b.jpg", 55.951, -3.191)
    _seed_file(conn, src_id, "/c.jpg", 55.952, -3.188)
    conn.close()

    run_gps_cluster(corpus_path, tmp_path / "kb", _cfg(min_samples=2), NullProgressReporter(), _cancel())

    from src.db.corpus import get_gps_clusters
    from src.db.kb import create_entity_table, open_kb, register_entity_table, upsert_entity_row

    columns = ["location", "latitude", "longitude", "threshold_m", "file_count"]

    def _seed(kb_conn):
        corpus_conn = open_corpus(corpus_path)
        clusters = get_gps_clusters(corpus_conn)
        corpus_conn.close()
        create_entity_table(kb_conn, "gps_cluster_locations", columns, "location")
        register_entity_table(
            kb_conn, "gps_cluster_locations", "GPS Cluster Locations", "", "[]", "location", "gps", ""
        )
        for row in clusters:
            upsert_entity_row(kb_conn, "gps_cluster_locations", {
                "location": row["label"],
                "latitude": str(row["centroid_lat"]),
                "longitude": str(row["centroid_lon"]),
                "threshold_m": str(row["eps_km"] * 1000),
                "file_count": str(row["file_count"]),
            })
        kb_conn.commit()

    kb_conn = open_kb(kb_path)
    _seed(kb_conn)
    _seed(kb_conn)  # second seed should not duplicate
    rows = kb_conn.execute("SELECT * FROM entity_gps_cluster_locations").fetchall()
    assert len(rows) == 1
    kb_conn.close()
