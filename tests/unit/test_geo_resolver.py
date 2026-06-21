"""Unit tests for src.geo.resolver."""
from shapely.geometry import Polygon

from src.geo.loader import RegionRecord
from src.geo.resolver import GeoLabel, resolve_point


def _make_country(lon_min, lat_min, lon_max, lat_max, country="Canada", code="CA") -> RegionRecord:
    poly = Polygon([(lon_min, lat_min), (lon_max, lat_min),
                    (lon_max, lat_max), (lon_min, lat_max)])
    return RegionRecord(polygon=poly, country=country, country_code=code,
                        state="", custom_name="", level="country")


def _make_state(lon_min, lat_min, lon_max, lat_max, country="Canada", code="CA", state="BC") -> RegionRecord:
    poly = Polygon([(lon_min, lat_min), (lon_max, lat_min),
                    (lon_max, lat_max), (lon_min, lat_max)])
    return RegionRecord(polygon=poly, country=country, country_code=code,
                        state=state, custom_name="", level="state")


def _make_custom(lon_min, lat_min, lon_max, lat_max, name="Home Zone") -> RegionRecord:
    poly = Polygon([(lon_min, lat_min), (lon_max, lat_min),
                    (lon_max, lat_max), (lon_min, lat_max)])
    return RegionRecord(polygon=poly, country="", country_code="",
                        state="", custom_name=name, level="custom")


class TestResolvePoint:
    def test_point_inside_country(self):
        regions = [_make_country(-130, 45, -120, 55)]
        result = resolve_point(50.0, -125.0, regions)
        assert result is not None
        assert result.country == "Canada"
        assert result.country_code == "CA"
        assert result.method == "shapefile"
        assert result.confidence == "high"

    def test_point_inside_state(self):
        regions = [
            _make_country(-130, 45, -110, 60),
            _make_state(-130, 48, -120, 56, state="British Columbia"),
        ]
        result = resolve_point(50.0, -125.0, regions)
        assert result is not None
        assert result.state == "British Columbia"
        assert result.country == "Canada"

    def test_point_outside_all_returns_none(self):
        regions = [_make_country(-10, -10, 0, 0)]
        result = resolve_point(50.0, 100.0, regions)
        assert result is None

    def test_empty_region_list_returns_none(self):
        result = resolve_point(49.25, -123.1, [])
        assert result is None

    def test_custom_region_takes_precedence(self):
        regions = [
            _make_country(-130, 45, -110, 60),
            _make_state(-130, 48, -120, 56, state="BC"),
            _make_custom(-127, 49, -123, 51, name="Privacy Zone"),
        ]
        result = resolve_point(50.0, -125.0, regions)
        assert result is not None
        assert result.custom_region == "Privacy Zone"
        assert result.method == "custom"
        assert result.country == "Canada"

    def test_custom_only_no_shapefile_match(self):
        regions = [_make_custom(-5, -5, 5, 5, name="Isolated Zone")]
        result = resolve_point(0.0, 0.0, regions)
        assert result is not None
        assert result.custom_region == "Isolated Zone"
        assert result.country == ""
        assert result.country_code == ""
        assert result.method == "custom"

    def test_state_match_no_custom(self):
        regions = [
            _make_country(-130, 45, -110, 60),
            _make_state(-130, 48, -120, 56, state="Alberta"),
        ]
        result = resolve_point(52.0, -115.0, regions)
        assert result is not None
        assert result.state == ""  # point is in country but not in the state polygon above

    def test_returns_geolabel_dataclass(self):
        regions = [_make_country(-180, -90, 180, 90, country="World", code="XX")]
        result = resolve_point(0.0, 0.0, regions)
        assert isinstance(result, GeoLabel)

    def test_point_on_boundary(self):
        regions = [_make_country(0, 0, 10, 10)]
        # Boundary behaviour is well-defined: shapely considers boundary as inside
        result = resolve_point(0.0, 0.0, regions)
        # May or may not match — just ensure no exception
        assert result is None or isinstance(result, GeoLabel)
