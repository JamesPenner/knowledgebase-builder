"""Unit tests for _haversine_m pure function."""
from src.stages.classify_rules import _haversine_m


def test_haversine_zero_distance():
    assert _haversine_m(49.2827, -123.1207, 49.2827, -123.1207) == 0.0


def test_haversine_known_distance():
    # Vancouver (49.2827, -123.1207) to Victoria (48.4284, -123.3656)
    # Haversine straight-line distance ≈ 97 km
    dist = _haversine_m(49.2827, -123.1207, 48.4284, -123.3656)
    assert 85_000 < dist < 110_000, f"Expected ~97 km, got {dist:.0f} m"


def test_haversine_threshold_within():
    # Two points about 30 m apart (roughly 0.0003 degrees latitude)
    dist = _haversine_m(49.0000, -123.0000, 49.0003, -123.0000)
    assert dist < 50.0, f"Expected < 50 m, got {dist:.1f} m"


def test_haversine_threshold_outside():
    # Two points about 1 km apart
    dist = _haversine_m(49.0000, -123.0000, 49.0090, -123.0000)
    assert dist > 500, f"Expected > 500 m, got {dist:.1f} m"
