"""Point-in-polygon resolution: convert (lat, lon) to a GeoLabel."""
from dataclasses import dataclass

from src.geo.loader import RegionRecord


@dataclass
class GeoLabel:
    country: str
    country_code: str
    state: str
    custom_region: str
    method: str      # 'shapefile' | 'custom'
    confidence: str  # 'high'


def resolve_point(lat: float, lon: float, regions: list[RegionRecord]) -> GeoLabel | None:
    """Test (lat, lon) against region list and return the best GeoLabel, or None."""
    from shapely.geometry import Point

    pt = Point(lon, lat)  # shapely uses (x=lon, y=lat)

    custom_match: RegionRecord | None = None
    state_match: RegionRecord | None = None
    country_match: RegionRecord | None = None

    for region in regions:
        try:
            if not region.polygon.contains(pt):
                continue
        except Exception:
            continue

        if region.level == "custom" and custom_match is None:
            custom_match = region
        elif region.level == "state" and state_match is None:
            state_match = region
        elif region.level == "country" and country_match is None:
            country_match = region

    if custom_match is None and state_match is None and country_match is None:
        return None

    # Custom region takes precedence; use shapefile for country/state context
    country = (state_match or country_match).country if (state_match or country_match) else ""
    country_code = (state_match or country_match).country_code if (state_match or country_match) else ""
    state = state_match.state if state_match else ""
    custom_name = custom_match.custom_name if custom_match else ""
    method = "custom" if custom_match else "shapefile"

    return GeoLabel(
        country=country,
        country_code=country_code,
        state=state,
        custom_region=custom_name,
        method=method,
        confidence="high",
    )
