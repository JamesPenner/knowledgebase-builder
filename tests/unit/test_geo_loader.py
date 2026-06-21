"""Unit tests for src.geo.loader."""
import json
import zipfile
from pathlib import Path



def _make_square_geojson(path: Path, name: str = "Test Region") -> None:
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": name},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]
                    ],
                },
            }
        ],
    }
    path.write_text(json.dumps(geojson), encoding="utf-8")


def _make_kml(path: Path, name: str = "KML Region") -> None:
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark>
  <name>{name}</name>
  <Polygon>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>-10,-10,0 10,-10,0 10,10,0 -10,10,0 -10,-10,0</coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
</kml>"""
    path.write_text(kml, encoding="utf-8")


def _make_kmz(path: Path, kml_content: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("doc.kml", kml_content)


class TestLoadCustomRegionsGeoJSON:
    def test_loads_polygon_feature(self, tmp_path):
        from src.geo.loader import load_custom_regions

        p = tmp_path / "region.geojson"
        _make_square_geojson(p)
        records = load_custom_regions(tmp_path)
        assert len(records) == 1
        assert records[0].custom_name == "Test Region"
        assert records[0].level == "custom"

    def test_name_falls_back_to_stem(self, tmp_path):
        from src.geo.loader import load_custom_regions

        data = {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-5, -5], [5, -5], [5, 5], [-5, 5], [-5, -5]]],
            },
        }
        p = tmp_path / "my_region.geojson"
        p.write_text(json.dumps(data), encoding="utf-8")
        records = load_custom_regions(tmp_path)
        assert records[0].custom_name == "my_region"

    def test_skips_non_polygon_geometries(self, tmp_path):
        from src.geo.loader import load_custom_regions

        data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "point"},
                    "geometry": {"type": "Point", "coordinates": [0, 0]},
                }
            ],
        }
        p = tmp_path / "pts.geojson"
        p.write_text(json.dumps(data), encoding="utf-8")
        records = load_custom_regions(tmp_path)
        assert records == []


class TestLoadCustomRegionsKML:
    def test_loads_kml_polygon(self, tmp_path):
        from src.geo.loader import load_custom_regions

        p = tmp_path / "region.kml"
        _make_kml(p, "My KML")
        records = load_custom_regions(tmp_path)
        assert len(records) == 1
        assert records[0].custom_name == "My KML"
        assert records[0].level == "custom"

    def test_loads_kmz(self, tmp_path):
        from src.geo.loader import load_custom_regions

        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark>
  <name>KMZ Region</name>
  <Polygon>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>-5,-5,0 5,-5,0 5,5,0 -5,5,0 -5,-5,0</coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
</kml>"""
        p = tmp_path / "zone.kmz"
        _make_kmz(p, kml)
        records = load_custom_regions(tmp_path)
        assert len(records) == 1
        assert records[0].custom_name == "KMZ Region"

    def test_kml_multiple_placemarks(self, tmp_path):
        from src.geo.loader import load_custom_regions

        kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark>
  <name>Zone A</name>
  <Polygon>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>-10,-10,0 0,-10,0 0,0,0 -10,0,0 -10,-10,0</coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
<Placemark>
  <name>Zone B</name>
  <Polygon>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>0,0,0 10,0,0 10,10,0 0,10,0 0,0,0</coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
</kml>"""
        p = tmp_path / "zones.kml"
        p.write_text(kml, encoding="utf-8")
        records = load_custom_regions(tmp_path)
        assert len(records) == 2
        assert {r.custom_name for r in records} == {"Zone A", "Zone B"}


class TestLoadNaturalEarth:
    def test_missing_directory_returns_empty(self, tmp_path):
        from src.geo.loader import load_natural_earth

        records = load_natural_earth(tmp_path / "nonexistent")
        assert records == []

    def test_empty_directory_returns_empty(self, tmp_path):
        from src.geo.loader import load_natural_earth

        ne_dir = tmp_path / "natural_earth"
        ne_dir.mkdir()
        records = load_natural_earth(ne_dir)
        assert records == []


class TestLoadAllRegions:
    def test_missing_geo_dir_returns_empty(self, tmp_path):
        from src.geo.loader import load_all_regions

        kb_folder = tmp_path / "my_kb"
        kb_folder.mkdir()
        records = load_all_regions(kb_folder)
        assert records == []

    def test_combines_custom_regions(self, tmp_path):
        from src.geo.loader import load_all_regions

        kb_folder = tmp_path / "my_kb"
        custom_dir = kb_folder / "reference" / "geo" / "custom"
        custom_dir.mkdir(parents=True)
        _make_square_geojson(custom_dir / "zone.geojson", "My Zone")

        records = load_all_regions(kb_folder)
        assert len(records) == 1
        assert records[0].custom_name == "My Zone"
