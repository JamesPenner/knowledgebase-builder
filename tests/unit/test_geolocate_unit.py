"""Unit tests for geolocate DB helpers."""
from src.db.corpus import (
    get_geolocated_file_ids,
    get_geolabels_for_export,
    upsert_geolabel,
)
from src.geo.resolver import GeoLabel


def _seed_file_with_gps(conn, lat: float, lon: float) -> int:
    conn.execute(
        "INSERT INTO sources (path, added_at) VALUES ('test_source', datetime('now'))"
    )
    src_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_size) "
        "VALUES (?, 'test/img.jpg', 'img.jpg', '.jpg', 1000)",
        (src_id,),
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


def _make_label(**kwargs) -> GeoLabel:
    defaults = dict(
        country="Canada",
        country_code="CA",
        state="British Columbia",
        custom_region="",
        method="shapefile",
        confidence="high",
    )
    defaults.update(kwargs)
    return GeoLabel(**defaults)


class TestUpsertGeolabel:
    def test_inserts_row(self, corpus_db):
        file_id = _seed_file_with_gps(corpus_db, 49.25, -123.1)
        label = _make_label()
        upsert_geolabel(corpus_db, file_id, label)
        corpus_db.commit()

        row = corpus_db.execute(
            "SELECT country, country_code, state, custom_region, method, confidence "
            "FROM file_geolabels WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        assert row["country"] == "Canada"
        assert row["country_code"] == "CA"
        assert row["state"] == "British Columbia"
        assert row["method"] == "shapefile"
        assert row["confidence"] == "high"

    def test_replace_on_rerun(self, corpus_db):
        file_id = _seed_file_with_gps(corpus_db, 49.25, -123.1)
        upsert_geolabel(corpus_db, file_id, _make_label(country="Canada"))
        corpus_db.commit()
        upsert_geolabel(corpus_db, file_id, _make_label(country="Updated"))
        corpus_db.commit()

        rows = corpus_db.execute(
            "SELECT country FROM file_geolabels WHERE file_id = ?", (file_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["country"] == "Updated"

    def test_custom_region_stored(self, corpus_db):
        file_id = _seed_file_with_gps(corpus_db, 49.25, -123.1)
        label = _make_label(custom_region="Home Zone", method="custom")
        upsert_geolabel(corpus_db, file_id, label)
        corpus_db.commit()

        row = corpus_db.execute(
            "SELECT custom_region, method FROM file_geolabels WHERE file_id = ?", (file_id,)
        ).fetchone()
        assert row["custom_region"] == "Home Zone"
        assert row["method"] == "custom"


class TestGetGeolocatedFileIds:
    def test_returns_empty_set_when_no_rows(self, corpus_db):
        result = get_geolocated_file_ids(corpus_db)
        assert result == set()

    def test_returns_set_of_ids(self, corpus_db):
        fid = _seed_file_with_gps(corpus_db, 49.25, -123.1)
        upsert_geolabel(corpus_db, fid, _make_label())
        corpus_db.commit()
        result = get_geolocated_file_ids(corpus_db)
        assert fid in result


class TestGetGeolabelsForExport:
    def test_returns_path_joined(self, corpus_db):
        fid = _seed_file_with_gps(corpus_db, 49.25, -123.1)
        upsert_geolabel(corpus_db, fid, _make_label())
        corpus_db.commit()
        rows = get_geolabels_for_export(corpus_db)
        assert len(rows) == 1
        assert rows[0]["country"] == "Canada"
        assert "path" in rows[0].keys()

    def test_returns_empty_when_no_geolabels(self, corpus_db):
        rows = get_geolabels_for_export(corpus_db)
        assert rows == []
