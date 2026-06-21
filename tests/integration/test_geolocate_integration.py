"""Integration tests for the geolocate stage."""
import json
import threading
from pathlib import Path

from src.db.corpus import get_geolabels_for_export, open_corpus
from src.pipeline.progress import NullProgressReporter
from src.stages.geolocate import run_geolocate


def _seed_file_with_gps(conn, lat: float, lon: float, path: str = "test/img.jpg") -> int:
    conn.execute(
        "INSERT INTO sources (path, added_at) VALUES ('test_source', datetime('now'))"
    )
    src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_size) "
        "VALUES (?, ?, 'img.jpg', '.jpg', 1000)",
        (src_id, path),
    )
    file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, raw_field_name, canonical_name, value, value_type) "
        "VALUES (?, 'GPS:GPSLatitude', 'exif_gps_lat', ?, 'numeric')",
        (file_id, str(lat)),
    )
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, raw_field_name, canonical_name, value, value_type) "
        "VALUES (?, 'GPS:GPSLongitude', 'exif_gps_lon', ?, 'numeric')",
        (file_id, str(lon)),
    )
    conn.commit()
    return file_id


def _seed_file_no_gps(conn) -> int:
    conn.execute(
        "INSERT INTO sources (path, added_at) VALUES ('test_source2', datetime('now'))"
    )
    src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_size) "
        "VALUES (?, 'no_gps/img2.jpg', 'img2.jpg', '.jpg', 1000)",
        (src_id,),
    )
    file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return file_id


def _make_geojson_region(kb_folder: Path, lon_min, lat_min, lon_max, lat_max, name: str) -> None:
    custom_dir = kb_folder / "reference" / "geo" / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)
    geojson = {
        "type": "Feature",
        "properties": {"name": name},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[lon_min, lat_min], [lon_max, lat_min],
                 [lon_max, lat_max], [lon_min, lat_max], [lon_min, lat_min]]
            ],
        },
    }
    (custom_dir / f"{name.replace(' ', '_')}.geojson").write_text(
        json.dumps(geojson), encoding="utf-8"
    )


def _run(corpus_path, kb_path):
    from src.config import load_config
    config = load_config(None)
    cancel = threading.Event()
    run_geolocate(corpus_path, kb_path, config, NullProgressReporter(), cancel)


class TestGeolocateHappyPath:
    def test_gps_file_resolved_to_custom_region(self, dbs, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        kb_folder = kb_path.parent

        _make_geojson_region(kb_folder, -130, 48, -120, 55, "BC Coast")
        _seed_file_with_gps(corpus_conn, 50.0, -125.0)
        corpus_conn.close()
        kb_conn.close()

        _run(corpus_path, kb_path)

        conn = open_corpus(corpus_path)
        rows = get_geolabels_for_export(conn)
        conn.close()
        assert len(rows) == 1
        assert rows[0]["custom_region"] == "BC Coast"
        assert rows[0]["method"] == "custom"

    def test_file_outside_all_regions_not_added(self, dbs, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        kb_folder = kb_path.parent

        _make_geojson_region(kb_folder, 10, 10, 20, 20, "Far Zone")
        _seed_file_with_gps(corpus_conn, 0.0, 0.0)  # outside the region
        corpus_conn.close()
        kb_conn.close()

        _run(corpus_path, kb_path)

        conn = open_corpus(corpus_path)
        rows = get_geolabels_for_export(conn)
        conn.close()
        assert len(rows) == 0

    def test_no_gps_file_not_added(self, dbs):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        kb_folder = kb_path.parent

        _make_geojson_region(kb_folder, -180, -90, 180, 90, "Whole World")
        _seed_file_no_gps(corpus_conn)
        corpus_conn.close()
        kb_conn.close()

        _run(corpus_path, kb_path)

        conn = open_corpus(corpus_path)
        rows = get_geolabels_for_export(conn)
        conn.close()
        assert len(rows) == 0


class TestGeolocateResume:
    def test_resume_skips_already_done(self, dbs):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        kb_folder = kb_path.parent

        _make_geojson_region(kb_folder, -180, -90, 180, 90, "World")
        _seed_file_with_gps(corpus_conn, 50.0, -125.0)
        corpus_conn.close()
        kb_conn.close()

        _run(corpus_path, kb_path)

        # Run again — should not produce duplicate rows
        _run(corpus_path, kb_path)

        conn = open_corpus(corpus_path)
        count = conn.execute("SELECT COUNT(*) FROM file_geolabels").fetchone()[0]
        conn.close()
        assert count == 1

    def test_no_regions_returns_without_error(self, dbs):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        _seed_file_with_gps(corpus_conn, 50.0, -125.0)
        corpus_conn.close()
        kb_conn.close()

        # No region files — should return gracefully
        _run(corpus_path, kb_path)

        conn = open_corpus(corpus_path)
        rows = get_geolabels_for_export(conn)
        conn.close()
        assert rows == []


class TestGeolocateExport:
    def test_write_geolabels_csv(self, dbs, tmp_path):
        import csv
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        kb_folder = kb_path.parent

        _make_geojson_region(kb_folder, -180, -90, 180, 90, "World")
        _seed_file_with_gps(corpus_conn, 50.0, -125.0)
        corpus_conn.close()
        kb_conn.close()

        _run(corpus_path, kb_path)

        export_dir = tmp_path / "export"
        export_dir.mkdir()

        conn2 = open_corpus(corpus_path)
        from src.stages.export import _write_geolabels
        _write_geolabels(export_dir, conn2)
        conn2.close()

        csv_path = export_dir / "geolabels.csv"
        assert csv_path.exists()

        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            csv_rows = list(reader)

        assert len(csv_rows) == 1
        assert csv_rows[0]["custom_region"] == "World"
        assert "path" in csv_rows[0]
        assert csv_rows[0]["method"] == "custom"
        assert csv_rows[0]["confidence"] == "high"

    def test_write_geolabels_empty_csv(self, dbs, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        corpus_conn.close()
        kb_conn.close()

        export_dir = tmp_path / "export"
        export_dir.mkdir()

        conn = open_corpus(corpus_path)
        from src.stages.export import _write_geolabels
        _write_geolabels(export_dir, conn)
        conn.close()

        csv_path = export_dir / "geolabels.csv"
        assert csv_path.exists()
        lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1  # header only
