"""Load geographic region data from shapefiles and user-contributed custom region files."""
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_KML_NS = "http://www.opengis.net/kml/2.2"

_NE_COUNTRY_STEM = "ne_10m_admin_0_countries"
_NE_STATE_STEM = "ne_10m_admin_1_states_provinces"


@dataclass
class RegionRecord:
    polygon: object          # shapely.geometry.Polygon or MultiPolygon
    country: str
    country_code: str        # ISO-2
    state: str               # empty string for country-level records
    custom_name: str         # set for custom region files, empty otherwise
    level: str               # 'country' | 'state' | 'custom'


def load_natural_earth(geo_dir: Path) -> list[RegionRecord]:
    """Read Natural Earth admin_0 (country) and admin_1 (state/province) shapefiles."""
    ne_dir = geo_dir / "natural_earth"
    if not ne_dir.is_dir():
        return []

    records: list[RegionRecord] = []
    records.extend(_load_ne_countries(ne_dir))
    records.extend(_load_ne_states(ne_dir))
    return records


def _load_ne_countries(ne_dir: Path) -> list[RegionRecord]:
    shp_path = ne_dir / f"{_NE_COUNTRY_STEM}.shp"
    if not shp_path.exists():
        return []
    try:
        import shapefile  # pyshp
        from shapely.geometry import shape as shapely_shape
    except ImportError:
        logger.warning("pyshp or shapely not installed; skipping Natural Earth countries")
        return []

    records: list[RegionRecord] = []
    try:
        reader = shapefile.Reader(str(shp_path))
        fields = [f[0] for f in reader.fields[1:]]  # skip DeletionFlag
        for sr in reader.shapeRecords():
            rec = dict(zip(fields, sr.record))
            geom = shapely_shape(sr.shape.__geo_interface__)
            country = str(rec.get("NAME_LONG") or rec.get("ADMIN") or rec.get("NAME") or "")
            code = str(rec.get("ISO_A2") or rec.get("ADM0_A3") or "")
            records.append(RegionRecord(
                polygon=geom,
                country=country,
                country_code=code,
                state="",
                custom_name="",
                level="country",
            ))
    except Exception as exc:
        logger.warning("Error reading NE countries shapefile: %s", exc)
    return records


def _load_ne_states(ne_dir: Path) -> list[RegionRecord]:
    shp_path = ne_dir / f"{_NE_STATE_STEM}.shp"
    if not shp_path.exists():
        return []
    try:
        import shapefile
        from shapely.geometry import shape as shapely_shape
    except ImportError:
        return []

    records: list[RegionRecord] = []
    try:
        reader = shapefile.Reader(str(shp_path))
        fields = [f[0] for f in reader.fields[1:]]
        for sr in reader.shapeRecords():
            rec = dict(zip(fields, sr.record))
            geom = shapely_shape(sr.shape.__geo_interface__)
            country = str(rec.get("admin") or rec.get("ADMIN") or "")
            code = str(rec.get("adm0_a3") or rec.get("iso_a2") or "")
            state = str(rec.get("name") or rec.get("NAME") or "")
            records.append(RegionRecord(
                polygon=geom,
                country=country,
                country_code=code,
                state=state,
                custom_name="",
                level="state",
            ))
    except Exception as exc:
        logger.warning("Error reading NE states shapefile: %s", exc)
    return records


def load_custom_regions(custom_dir: Path) -> list[RegionRecord]:
    """Scan custom_dir for .geojson, .kml, .kmz, .shp and return RegionRecords."""
    if not custom_dir.is_dir():
        return []

    records: list[RegionRecord] = []
    for path in sorted(custom_dir.iterdir()):
        suffix = path.suffix.lower()
        try:
            if suffix == ".geojson":
                records.extend(_load_geojson(path))
            elif suffix == ".kml":
                records.extend(_load_kml(path, path.stem))
            elif suffix == ".kmz":
                records.extend(_load_kmz(path))
            elif suffix == ".shp":
                records.extend(_load_shp(path))
        except Exception as exc:
            logger.warning("Skipping %s — could not parse: %s", path.name, exc)
    return records


def _load_geojson(path: Path) -> list[RegionRecord]:
    from shapely.geometry import shape as shapely_shape

    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    features = data.get("features") or ([data] if data.get("type") == "Feature" else [])
    records: list[RegionRecord] = []
    for feat in features:
        geom_data = feat.get("geometry") or feat
        if geom_data.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            geom = shapely_shape(geom_data)
        except Exception:
            continue
        props = feat.get("properties") or {}
        name = str(props.get("name") or props.get("NAME") or path.stem)
        records.append(RegionRecord(
            polygon=geom,
            country="",
            country_code="",
            state="",
            custom_name=name,
            level="custom",
        ))
    return records


def _load_kml(path: Path, stem: str) -> list[RegionRecord]:
    text = path.read_text(encoding="utf-8")
    return _parse_kml_text(text, stem)


def _load_kmz(path: Path) -> list[RegionRecord]:
    with zipfile.ZipFile(path) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            return []
        text = zf.read(kml_names[0]).decode("utf-8")
    return _parse_kml_text(text, path.stem)


def _parse_kml_text(text: str, stem: str) -> list[RegionRecord]:
    from shapely.geometry import Polygon

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        logger.warning("KML parse error in %s: %s", stem, exc)
        return []

    ns = _KML_NS
    records: list[RegionRecord] = []

    for pm in root.iter(f"{{{ns}}}Placemark"):
        name_el = pm.find(f"{{{ns}}}name")
        name = name_el.text.strip() if name_el is not None and name_el.text else stem

        for coords_el in pm.iter(f"{{{ns}}}coordinates"):
            raw = (coords_el.text or "").strip()
            if not raw:
                continue
            pairs: list[tuple[float, float]] = []
            for token in raw.split():
                parts = token.split(",")
                if len(parts) >= 2:
                    try:
                        lon = float(parts[0])
                        lat = float(parts[1])
                        pairs.append((lon, lat))
                    except ValueError:
                        continue
            if len(pairs) >= 3:
                try:
                    geom = Polygon(pairs)
                    records.append(RegionRecord(
                        polygon=geom,
                        country="",
                        country_code="",
                        state="",
                        custom_name=name,
                        level="custom",
                    ))
                except Exception:
                    continue

    return records


def _load_shp(path: Path) -> list[RegionRecord]:
    try:
        import shapefile
        from shapely.geometry import shape as shapely_shape
    except ImportError:
        logger.warning("pyshp not installed; skipping %s", path.name)
        return []

    records: list[RegionRecord] = []
    reader = shapefile.Reader(str(path))
    fields = [f[0] for f in reader.fields[1:]]
    for sr in reader.shapeRecords():
        rec = dict(zip(fields, sr.record))
        try:
            geom = shapely_shape(sr.shape.__geo_interface__)
        except Exception:
            continue
        name = str(rec.get("name") or rec.get("NAME") or path.stem)
        records.append(RegionRecord(
            polygon=geom,
            country="",
            country_code="",
            state="",
            custom_name=name,
            level="custom",
        ))
    return records


def load_all_regions(kb_folder: Path) -> list[RegionRecord]:
    """Load Natural Earth + custom regions from kb_folder/reference/geo/."""
    geo_dir = kb_folder / "reference" / "geo"
    if not geo_dir.is_dir():
        return []
    records = load_natural_earth(geo_dir)
    records.extend(load_custom_regions(geo_dir / "custom"))
    return records
