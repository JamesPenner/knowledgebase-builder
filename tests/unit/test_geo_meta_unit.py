"""Unit tests for geo_meta stage logic."""
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.stages.classify_rules import _haversine_m


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
}


# ---------------------------------------------------------------------------
# Within threshold → match
# ---------------------------------------------------------------------------

def test_within_threshold_writes_label(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
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
# Stats dict keys present
# ---------------------------------------------------------------------------

def test_stats_dict_keys(tmp_path):
    from src.stages.geo_meta import run_geo_meta

    with (
        patch(_PATCHES["open_corpus"], return_value=MagicMock()),
        patch(_PATCHES["open_kb"], return_value=MagicMock()),
        patch(_PATCHES["get_gps_entity_tables"], return_value=[]),
    ):
        result = run_geo_meta(
            tmp_path / "corpus.db", tmp_path / "knowledge.db",
            _make_config(), _make_progress(), _make_cancel(),
        )

    assert set(result.keys()) == {"files_processed", "files_matched", "files_unmatched", "errors"}
