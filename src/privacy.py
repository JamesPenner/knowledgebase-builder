"""Privacy zone loading and GPS masking helpers for the write-back stage."""
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PrivacyZone:
    name: str
    mode: str            # 'strip' | 'coarsen'
    decimal_places: int  # only used for coarsen
    polygon: object      # shapely Polygon or MultiPolygon


def load_privacy_zones(kb_folder: Path) -> list[PrivacyZone]:
    """Read reference/privacy_zones.yaml and return resolved PrivacyZone objects."""
    yaml_path = kb_folder / "reference" / "privacy_zones.yaml"
    if not yaml_path.exists():
        return []

    try:
        with yaml_path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("Could not read privacy_zones.yaml: %s", exc)
        return []

    entries = raw.get("privacy_zones") or []
    zones: list[PrivacyZone] = []
    ref_dir = kb_folder / "reference"

    for entry in entries:
        name = str(entry.get("name") or "unnamed")
        mode = str(entry.get("mode") or "strip").lower()
        if mode not in ("strip", "coarsen"):
            logger.warning("Privacy zone %r has unknown mode %r; skipping", name, mode)
            continue
        decimal_places = int(entry.get("decimal_places") or 2)

        polygon = _build_polygon(entry, name, ref_dir)
        if polygon is None:
            continue
        zones.append(PrivacyZone(name=name, mode=mode, decimal_places=decimal_places, polygon=polygon))

    return zones


def _build_polygon(entry: dict, name: str, ref_dir: Path):
    """Build a shapely polygon from a zone spec dict. Returns None on failure."""
    if "file" in entry:
        return _polygon_from_file(ref_dir / entry["file"], name)

    if "center" in entry and "radius_m" in entry:
        return _circle_polygon(entry["center"], entry["radius_m"], name)

    logger.warning("Privacy zone %r has neither 'file' nor 'center'+'radius_m'; skipping", name)
    return None


def _circle_polygon(center, radius_m: float, name: str):
    try:
        from shapely.geometry import Point
    except ImportError:
        logger.warning("shapely not installed; cannot build circle zone %r", name)
        return None
    try:
        lat, lon = float(center[0]), float(center[1])
        # degree approximation: 1° ≈ 111 320 m
        return Point(lon, lat).buffer(radius_m / 111_320)
    except Exception as exc:
        logger.warning("Could not build circle for zone %r: %s", name, exc)
        return None


def _polygon_from_file(path: Path, name: str):
    if not path.exists():
        logger.warning("Privacy zone %r: file not found: %s", name, path)
        return None
    try:
        from src.geo.loader import (
            _load_geojson,
            _load_kml,
            _load_kmz,
            _load_shp,
        )
    except ImportError:
        logger.warning("src.geo.loader unavailable; cannot load zone file for %r", name)
        return None

    suffix = path.suffix.lower()
    try:
        if suffix == ".geojson":
            records = _load_geojson(path)
        elif suffix == ".kml":
            records = _load_kml(path, path.stem)
        elif suffix == ".kmz":
            records = _load_kmz(path)
        elif suffix == ".shp":
            records = _load_shp(path)
        else:
            logger.warning("Privacy zone %r: unsupported file type %r", name, suffix)
            return None
    except Exception as exc:
        logger.warning("Privacy zone %r: failed to load %s: %s", name, path.name, exc)
        return None

    if not records:
        logger.warning("Privacy zone %r: no polygons found in %s", name, path.name)
        return None
    return records[0].polygon


def find_matching_zone(lat: float, lon: float, zones: list[PrivacyZone]) -> "PrivacyZone | None":
    """Return the most restrictive zone containing (lat, lon), or None.

    Conflict resolution: strip beats coarsen; among coarsen zones, minimum
    decimal_places (least precision) wins.
    """
    try:
        from shapely.geometry import Point
    except ImportError:
        return None

    pt = Point(lon, lat)
    matches: list[PrivacyZone] = []
    for zone in zones:
        try:
            if zone.polygon.contains(pt):
                matches.append(zone)
        except Exception:
            continue

    if not matches:
        return None

    # strip wins
    strip = [z for z in matches if z.mode == "strip"]
    if strip:
        return strip[0]

    # among coarsen: pick minimum decimal_places
    coarsen = [z for z in matches if z.mode == "coarsen"]
    return min(coarsen, key=lambda z: z.decimal_places)


def apply_gps_mask(lat: float, lon: float, zone: PrivacyZone) -> "tuple[float, float] | None":
    """Return masked (lat, lon) for coarsen, or None for strip."""
    if zone.mode == "strip":
        return None
    dp = zone.decimal_places
    return round(lat, dp), round(lon, dp)
