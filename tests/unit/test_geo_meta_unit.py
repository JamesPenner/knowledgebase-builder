"""Unit tests for geo_meta stage logic."""
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.db.corpus import parse_gps_value
from src.stages.classify_rules import _haversine_m


# ---------------------------------------------------------------------------
# parse_gps_value
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("48.385327", pytest.approx(48.385327)),
    ("-123.514908", pytest.approx(-123.514908)),
    ("0.0", pytest.approx(0.0)),
    # DMS with direction
    ("48 deg 23' 7.18\" N", pytest.approx(48.385327, abs=1e-4)),
    ("123 deg 30' 53.67\" W", pytest.approx(-123.514908, abs=1e-4)),
    ("22 deg 4' 48.00\" N", pytest.approx(22.08, abs=1e-4)),
    ("33 deg 52' 12.00\" S", pytest.approx(-33.87, abs=1e-4)),
    # DMS without direction (raw EXIF, no ref suffix)
    ("48 deg 23' 7.18\"", pytest.approx(48.385327, abs=1e-4)),
    # float already
    (48.5, pytest.approx(48.5)),
])
def test_parse_gps_value(raw, expected):
    assert parse_gps_value(raw) == expected


def test_parse_gps_value_none():
    assert parse_gps_value(None) is None


def test_parse_gps_value_unparseable():
    assert parse_gps_value("not a coordinate") is None


# ---------------------------------------------------------------------------
# Haversine helper
# ---------------------------------------------------------------------------

def test_haversine_same_point():
    assert _haversine_m(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0, abs=0.01)


def test_haversine_known_distance():
    # London to Paris is approximately 340 km
    dist = _haversine_m(51.5074, -0.1278, 48.8566, 2.3522)
    assert 330_000 < dist < 350_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(threshold_m=500.0):
    cfg = MagicMock()
    cfg.geo_meta_default_threshold_m = threshold_m
    return cfg


def _make_cancel():
    return threading.Event()


def _make_progress():
    return MagicMock()


def _entity_row(location="Home", lat=51.5, lon=-0.1, threshold_m=None, city=None,
                state=None, country=None, country_code=None):
    row = {
        "location": location,
        "latitude": lat,
        "longitude": lon,
        "city": city,
        "state": state,
        "country": country,
        "country_code": country_code,
    }
    if threshold_m is not None:
        row["threshold_m"] = threshold_m
    return row


def _file_row(file_id=1, path="/img.jpg", lat=51.5, lon=-0.1):
    return {"id": file_id, "path": path, "lat": lat, "lon": lon}


def _file_row_dms(file_id=1, path="/img.jpg"):
    """File row with ExifTool DMS-format lat/lon strings (e.g. '51 deg 30' 0.00\" N')."""
    return {
        "id": file_id, "path": path,
        "lat": "51 deg 30' 0.18\" N",    # ≈ 51.5000°
        "lon": "0 deg 6' 0.00\" W",       # ≈ -0.1000°
    }


def _make_tbl(table_name="locations"):
    return {"table_name": table_name}


# Patch paths — lazy imports resolved at src.db.* and src.stages.*
_PATCHES = {
    "open_corpus": "src.db.corpus.open_corpus",
    "open_kb": "src.db.kb.open_kb",
    "get_gps_entity_tables": "src.db.kb.get_gps_entity_tables",
    "get_entity_table_rows": "src.db.kb.get_entity_table_rows",
    "get_gps_files": "src.db.corpus.get_gps_files_without_location_label",
    "upsert_label": "src.db.corpus.upsert_location_label",
    "update_checkpoint": "src.db.corpus.update_pipeline_checkpoint",
    "get_enabled_categories": "src.pipeline.knowledge_gates.get_enabled_categories",
}

_ALL_CATEGORIES_ENABLED = frozenset({"people", "places", "dates"})


# ---------------------------------------------------------------------------
# Within threshold → match
# ---------------------------------------------------------------------------

def test_within_threshold_writes_label(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl()]),
        patch(_PATCHES["get_entity_table_rows"], return_value=[_entity_row(lat=51.5, lon=-0.1, threshold_m=500.0)]),
        patch(_PATCHES["get_gps_files"], return_value=[_file_row(lat=51.5001, lon=-0.1001)]),
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(), _make_progress(), _make_cancel(),
        )

    assert result["files_matched"] == 1
    assert result["files_unmatched"] == 0
    mock_upsert.assert_called_once()


# ---------------------------------------------------------------------------
# Outside threshold → no match
# ---------------------------------------------------------------------------

def test_outside_threshold_no_label(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl()]),
        # London entity, Paris file (~340 km away, threshold 500m)
        patch(_PATCHES["get_entity_table_rows"], return_value=[_entity_row(lat=51.5074, lon=-0.1278, threshold_m=500.0)]),
        patch(_PATCHES["get_gps_files"], return_value=[_file_row(lat=48.8566, lon=2.3522)]),
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(), _make_progress(), _make_cancel(),
        )

    assert result["files_matched"] == 0
    assert result["files_unmatched"] == 1
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# No GPS entity tables → early return
# ---------------------------------------------------------------------------

def test_no_gps_tables_returns_early(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(), _make_progress(), _make_cancel(),
        )

    assert result["files_processed"] == 0
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Multiple entity tables → best match (lowest distance) wins
# ---------------------------------------------------------------------------

def test_multiple_tables_best_match_selected(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    captured = {}

    def fake_upsert(conn, file_id, location, city, state, country, country_code, distance_m, matched_table):
        captured["location"] = location

    def fake_rows(conn, table_name):
        if table_name == "far_tbl":
            return [_entity_row(location="Far Place", lat=51.505, lon=-0.1, threshold_m=5000.0)]
        return [_entity_row(location="Close Place", lat=51.5001, lon=-0.1001, threshold_m=5000.0)]

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl("far_tbl"), _make_tbl("close_tbl")]),
        patch(_PATCHES["get_entity_table_rows"], side_effect=fake_rows),
        patch(_PATCHES["get_gps_files"], return_value=[_file_row(lat=51.5, lon=-0.1)]),
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"], side_effect=fake_upsert),
    ):
        run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(threshold_m=5000.0), _make_progress(), _make_cancel(),
        )

    assert captured.get("location") == "Close Place"


# ---------------------------------------------------------------------------
# Missing threshold_m column → falls back to config default
# ---------------------------------------------------------------------------

def test_missing_threshold_m_uses_config_default(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl()]),
        # No threshold_m in row; file is ~111m away; config default 1000m → match
        patch(_PATCHES["get_entity_table_rows"], return_value=[_entity_row(lat=51.5, lon=-0.1)]),
        patch(_PATCHES["get_gps_files"], return_value=[_file_row(lat=51.5010, lon=-0.1)]),
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(threshold_m=1000.0), _make_progress(), _make_cancel(),
        )

    assert result["files_matched"] == 1
    mock_upsert.assert_called_once()


# ---------------------------------------------------------------------------
# Empty entity table → 0 matched
# ---------------------------------------------------------------------------

def test_empty_entity_table_no_match(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl()]),
        patch(_PATCHES["get_entity_table_rows"], return_value=[]),
        patch(_PATCHES["get_gps_files"], return_value=[_file_row()]),
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(), _make_progress(), _make_cancel(),
        )

    assert result["files_matched"] == 0
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Config threshold acts as minimum floor over per-entry threshold
# ---------------------------------------------------------------------------

def test_config_threshold_is_minimum_floor(tmp_path):
    """When geo_meta_default_threshold_m > entry threshold_m, config wins."""
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl()]),
        # Entry threshold 50m, file is 200m away → previously no match, now matches at 500m floor
        patch(_PATCHES["get_entity_table_rows"], return_value=[_entity_row(lat=51.5, lon=-0.1, threshold_m=50.0)]),
        patch(_PATCHES["get_gps_files"], return_value=[_file_row(lat=51.5018, lon=-0.1)]),  # ~200m
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(threshold_m=500.0), _make_progress(), _make_cancel(),
        )

    assert result["files_matched"] == 1, "config floor of 500m should override entry's 50m"
    mock_upsert.assert_called_once()


def test_per_entry_threshold_above_config_wins(tmp_path):
    """Per-entry threshold larger than config is respected."""
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl()]),
        # Entry 2000m, config 500m → effective 2000m; file 800m away → match
        patch(_PATCHES["get_entity_table_rows"], return_value=[_entity_row(lat=51.5, lon=-0.1, threshold_m=2000.0)]),
        patch(_PATCHES["get_gps_files"], return_value=[_file_row(lat=51.5072, lon=-0.1)]),  # ~800m
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(threshold_m=500.0), _make_progress(), _make_cancel(),
        )

    assert result["files_matched"] == 1
    mock_upsert.assert_called_once()


# ---------------------------------------------------------------------------
# DMS-format GPS values are parsed and matched correctly
# ---------------------------------------------------------------------------

def test_dms_format_file_matches_entity(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[_make_tbl()]),
        patch(_PATCHES["get_entity_table_rows"], return_value=[_entity_row(lat=51.5, lon=-0.1, threshold_m=500.0)]),
        # DMS strings — without the parser, CAST gives 51.0 and 0.0 (wrong)
        patch(_PATCHES["get_gps_files"], return_value=[_file_row_dms()]),
        patch(_PATCHES["update_checkpoint"]),
        patch(_PATCHES["upsert_label"]) as mock_upsert,
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(threshold_m=500.0), _make_progress(), _make_cancel(),
        )

    assert result["files_matched"] == 1
    mock_upsert.assert_called_once()


# ---------------------------------------------------------------------------
# Stats dict keys present
# ---------------------------------------------------------------------------

def test_stats_dict_keys(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_enabled_categories"], return_value=_ALL_CATEGORIES_ENABLED),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[]),
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(), _make_progress(), _make_cancel(),
        )

    assert set(result.keys()) == {"files_processed", "files_matched", "files_unmatched", "errors"}
