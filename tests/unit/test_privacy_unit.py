"""Unit tests for src/privacy.py and GPS-mask DB helpers."""
import json
import sqlite3
from pathlib import Path
import pytest

from src.privacy import PrivacyZone, apply_gps_mask, find_matching_zone, load_privacy_zones


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_zone(mode="strip", decimal_places=2, lat=51.5, lon=-0.1, radius_m=1000):
    from shapely.geometry import Point
    polygon = Point(lon, lat).buffer(radius_m / 111_320)
    return PrivacyZone(name="test", mode=mode, decimal_places=decimal_places, polygon=polygon)


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_privacy_zones
# ---------------------------------------------------------------------------

class TestLoadPrivacyZones:
    def test_no_yaml_returns_empty(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "reference").mkdir()
        assert load_privacy_zones(kb) == []

    def test_point_radius_zone(self, tmp_path):
        kb = tmp_path / "kb"
        ref = kb / "reference"
        ref.mkdir(parents=True)
        _write_yaml(ref / "privacy_zones.yaml", """
privacy_zones:
  - name: Home
    mode: strip
    center: [51.5074, -0.1278]
    radius_m: 500
""")
        zones = load_privacy_zones(kb)
        assert len(zones) == 1
        assert zones[0].name == "Home"
        assert zones[0].mode == "strip"

    def test_coarsen_zone(self, tmp_path):
        kb = tmp_path / "kb"
        ref = kb / "reference"
        ref.mkdir(parents=True)
        _write_yaml(ref / "privacy_zones.yaml", """
privacy_zones:
  - name: Office
    mode: coarsen
    decimal_places: 2
    center: [51.5, -0.09]
    radius_m: 200
""")
        zones = load_privacy_zones(kb)
        assert zones[0].mode == "coarsen"
        assert zones[0].decimal_places == 2

    def test_file_based_zone_missing_file_skipped(self, tmp_path):
        kb = tmp_path / "kb"
        ref = kb / "reference"
        ref.mkdir(parents=True)
        _write_yaml(ref / "privacy_zones.yaml", """
privacy_zones:
  - name: Polygon
    mode: strip
    file: geo/custom/missing.geojson
""")
        zones = load_privacy_zones(kb)
        assert zones == []

    def test_file_based_zone_geojson(self, tmp_path):
        kb = tmp_path / "kb"
        ref = kb / "reference"
        custom = ref / "geo" / "custom"
        custom.mkdir(parents=True)
        geojson = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-0.2, 51.4], [0.1, 51.4], [0.1, 51.6], [-0.2, 51.6], [-0.2, 51.4]
                ]],
            },
            "properties": {"name": "BigZone"},
        }
        (custom / "zone.geojson").write_text(json.dumps(geojson), encoding="utf-8")
        _write_yaml(ref / "privacy_zones.yaml", """
privacy_zones:
  - name: BigZone
    mode: coarsen
    decimal_places: 1
    file: geo/custom/zone.geojson
""")
        zones = load_privacy_zones(kb)
        assert len(zones) == 1
        assert zones[0].name == "BigZone"

    def test_empty_yaml_returns_empty(self, tmp_path):
        kb = tmp_path / "kb"
        ref = kb / "reference"
        ref.mkdir(parents=True)
        _write_yaml(ref / "privacy_zones.yaml", "")
        assert load_privacy_zones(kb) == []

    def test_unknown_mode_skipped(self, tmp_path):
        kb = tmp_path / "kb"
        ref = kb / "reference"
        ref.mkdir(parents=True)
        _write_yaml(ref / "privacy_zones.yaml", """
privacy_zones:
  - name: Bad
    mode: redact
    center: [51.5, -0.1]
    radius_m: 100
""")
        assert load_privacy_zones(kb) == []

    def test_entry_without_center_or_file_skipped(self, tmp_path):
        kb = tmp_path / "kb"
        ref = kb / "reference"
        ref.mkdir(parents=True)
        _write_yaml(ref / "privacy_zones.yaml", """
privacy_zones:
  - name: Incomplete
    mode: strip
""")
        assert load_privacy_zones(kb) == []


# ---------------------------------------------------------------------------
# find_matching_zone
# ---------------------------------------------------------------------------

class TestFindMatchingZone:
    def test_point_inside_returns_zone(self):
        zone = _make_zone(lat=51.5, lon=-0.1, radius_m=10_000)
        result = find_matching_zone(51.5, -0.1, [zone])
        assert result is zone

    def test_point_outside_returns_none(self):
        zone = _make_zone(lat=51.5, lon=-0.1, radius_m=100)
        result = find_matching_zone(40.0, 2.0, [zone])
        assert result is None

    def test_empty_zones_returns_none(self):
        assert find_matching_zone(51.5, -0.1, []) is None

    def test_strip_beats_coarsen(self):
        strip_zone = _make_zone(mode="strip", lat=51.5, lon=-0.1, radius_m=10_000)
        coarsen_zone = _make_zone(mode="coarsen", decimal_places=2, lat=51.5, lon=-0.1, radius_m=10_000)
        result = find_matching_zone(51.5, -0.1, [coarsen_zone, strip_zone])
        assert result.mode == "strip"

    def test_min_decimal_places_wins_among_coarsen(self):
        z1 = _make_zone(mode="coarsen", decimal_places=3, lat=51.5, lon=-0.1, radius_m=10_000)
        z1 = PrivacyZone(name="z1", mode="coarsen", decimal_places=3, polygon=z1.polygon)
        z2 = _make_zone(mode="coarsen", decimal_places=1, lat=51.5, lon=-0.1, radius_m=10_000)
        z2 = PrivacyZone(name="z2", mode="coarsen", decimal_places=1, polygon=z2.polygon)
        result = find_matching_zone(51.5, -0.1, [z1, z2])
        assert result.decimal_places == 1

    def test_only_strip_zones_returns_strip(self):
        z = _make_zone(mode="strip", lat=51.5, lon=-0.1, radius_m=10_000)
        assert find_matching_zone(51.5, -0.1, [z]).mode == "strip"


# ---------------------------------------------------------------------------
# apply_gps_mask
# ---------------------------------------------------------------------------

class TestApplyGpsMask:
    def test_strip_returns_none(self):
        zone = _make_zone(mode="strip")
        assert apply_gps_mask(51.5074, -0.1278, zone) is None

    def test_coarsen_rounds_coordinates(self):
        zone = _make_zone(mode="coarsen", decimal_places=2)
        result = apply_gps_mask(51.5074, -0.1278, zone)
        assert result == (51.51, -0.13)

    def test_coarsen_one_decimal(self):
        zone = _make_zone(mode="coarsen", decimal_places=1)
        result = apply_gps_mask(51.5678, -0.1234, zone)
        assert result == (51.6, -0.1)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

class TestGpsMaskDbHelpers:
    def _setup_db(self, tmp_path: Path):
        db = tmp_path / "corpus.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
        conn.execute("""
            CREATE TABLE file_gps_masks (
                file_id    INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                zone_name  TEXT NOT NULL,
                mode       TEXT NOT NULL,
                masked_lat REAL,
                masked_lon REAL,
                masked_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO files VALUES (1, '/a/b.jpg')")
        conn.execute("INSERT INTO files VALUES (2, '/a/c.jpg')")
        conn.commit()
        return conn

    def test_upsert_and_get_strip(self, tmp_path):
        from src.db.corpus import get_gps_masked_files, upsert_gps_mask
        conn = self._setup_db(tmp_path)
        upsert_gps_mask(conn, 1, "Home", "strip", None, None)
        conn.commit()
        masked = get_gps_masked_files(conn)
        assert 1 in masked
        assert 2 not in masked

    def test_upsert_coarsen_stores_coords(self, tmp_path):
        from src.db.corpus import upsert_gps_mask
        conn = self._setup_db(tmp_path)
        upsert_gps_mask(conn, 1, "Office", "coarsen", 51.51, -0.13)
        conn.commit()
        row = conn.execute("SELECT * FROM file_gps_masks WHERE file_id=1").fetchone()
        assert row["zone_name"] == "Office"
        assert row["masked_lat"] == pytest.approx(51.51)
        assert row["masked_lon"] == pytest.approx(-0.13)

    def test_upsert_replaces_on_rerun(self, tmp_path):
        from src.db.corpus import upsert_gps_mask
        conn = self._setup_db(tmp_path)
        upsert_gps_mask(conn, 1, "Home", "strip", None, None)
        upsert_gps_mask(conn, 1, "Office", "coarsen", 51.51, -0.13)
        conn.commit()
        rows = conn.execute("SELECT * FROM file_gps_masks WHERE file_id=1").fetchall()
        assert len(rows) == 1
        assert rows[0]["zone_name"] == "Office"
