"""Unit tests for KB.X1 face_meta readers and helpers."""
from src.stages.face_meta import (
    MetaFaceRegion,
    bbox_norm_to_pixels,
    deduplicate_regions,
    read_acdsee,
    read_mwg_rs,
)


# ---------------------------------------------------------------------------
# bbox_norm_to_pixels
# ---------------------------------------------------------------------------

def test_bbox_norm_to_pixels_center_point():
    x1, y1, x2, y2 = bbox_norm_to_pixels(0.5, 0.5, 0.4, 0.2, 1000, 500)
    assert abs(x1 - 300) < 1e-6
    assert abs(y1 - 200) < 1e-6
    assert abs(x2 - 700) < 1e-6
    assert abs(y2 - 300) < 1e-6


def test_bbox_norm_to_pixels_corner():
    x1, y1, x2, y2 = bbox_norm_to_pixels(0.0, 0.0, 0.2, 0.2, 100, 100)
    assert abs(x1 - (-10)) < 1e-6
    assert abs(y1 - (-10)) < 1e-6


# ---------------------------------------------------------------------------
# read_mwg_rs
# ---------------------------------------------------------------------------

def test_read_mwg_rs_three_regions():
    exif = {
        "RegionName": ["Alice", "Bob", "Carol"],
        "RegionType": ["Face", "Face", "Face"],
        "RegionAreaUnit": "normalized",
        "RegionAreaX": [0.1, 0.5, 0.9],
        "RegionAreaY": [0.2, 0.5, 0.2],
        "RegionAreaW": [0.1, 0.1, 0.1],
        "RegionAreaH": [0.1, 0.1, 0.1],
    }
    regions = read_mwg_rs(exif, 1000, 800)
    assert len(regions) == 3
    assert {r["name"] for r in regions} == {"Alice", "Bob", "Carol"}
    assert all(r["source"] == "mwg-rs" for r in regions)


def test_read_mwg_rs_skips_non_face_type():
    exif = {
        "RegionName": ["Alice", "Signature"],
        "RegionType": ["Face", "Barcode"],
        "RegionAreaUnit": "normalized",
        "RegionAreaX": [0.1, 0.5],
        "RegionAreaY": [0.2, 0.5],
        "RegionAreaW": [0.1, 0.1],
        "RegionAreaH": [0.1, 0.1],
    }
    regions = read_mwg_rs(exif, 1000, 800)
    assert len(regions) == 1
    assert regions[0]["name"] == "Alice"


def test_read_mwg_rs_empty_when_no_region_keys():
    regions = read_mwg_rs({}, 1000, 800)
    assert regions == []


def test_read_mwg_rs_skips_non_normalized_unit():
    exif = {
        "RegionName": ["Alice"],
        "RegionType": ["Face"],
        "RegionAreaUnit": "pixel",
        "RegionAreaX": [100],
        "RegionAreaY": [200],
        "RegionAreaW": [50],
        "RegionAreaH": [50],
    }
    regions = read_mwg_rs(exif, 1000, 800)
    assert regions == []


# ---------------------------------------------------------------------------
# read_acdsee
# ---------------------------------------------------------------------------

def test_read_acdsee_uses_dly_when_present():
    exif = {
        "ACDSeeRegionName": ["Alice"],
        "ACDSeeRegionType": ["Face"],
        "ACDSeeRegionDLYAreaX": [0.3],
        "ACDSeeRegionDLYAreaY": [0.4],
        "ACDSeeRegionDLYAreaW": [0.1],
        "ACDSeeRegionDLYAreaH": [0.1],
        "ACDSeeRegionALGAreaX": [0.9],
        "ACDSeeRegionALGAreaY": [0.9],
        "ACDSeeRegionALGAreaW": [0.05],
        "ACDSeeRegionALGAreaH": [0.05],
    }
    regions = read_acdsee(exif, 1000, 800)
    assert len(regions) == 1
    cx, cy, w, h = regions[0]["bbox_norm"]
    assert abs(cx - 0.3) < 1e-6


def test_read_acdsee_falls_back_to_alg():
    exif = {
        "ACDSeeRegionName": ["Bob"],
        "ACDSeeRegionType": ["Face"],
        "ACDSeeRegionALGAreaX": [0.7],
        "ACDSeeRegionALGAreaY": [0.3],
        "ACDSeeRegionALGAreaW": [0.08],
        "ACDSeeRegionALGAreaH": [0.08],
    }
    regions = read_acdsee(exif, 1000, 800)
    assert len(regions) == 1
    cx, cy, w, h = regions[0]["bbox_norm"]
    assert abs(cx - 0.7) < 1e-6


def test_read_acdsee_skips_non_face_type():
    exif = {
        "ACDSeeRegionName": ["Dog"],
        "ACDSeeRegionType": ["Animal"],
        "ACDSeeRegionDLYAreaX": [0.5],
        "ACDSeeRegionDLYAreaY": [0.5],
        "ACDSeeRegionDLYAreaW": [0.1],
        "ACDSeeRegionDLYAreaH": [0.1],
    }
    regions = read_acdsee(exif, 1000, 800)
    assert regions == []


# ---------------------------------------------------------------------------
# deduplicate_regions
# ---------------------------------------------------------------------------

def test_deduplicate_prefers_mwg_rs():
    regions = [
        MetaFaceRegion(name="Alice", bbox_norm=(0.1, 0.1, 0.1, 0.1), source="acdsee"),
        MetaFaceRegion(name="Alice", bbox_norm=(0.2, 0.2, 0.1, 0.1), source="mwg-rs"),
    ]
    result = deduplicate_regions(regions)
    assert len(result) == 1
    assert result[0]["source"] == "mwg-rs"
    assert abs(result[0]["bbox_norm"][0] - 0.2) < 1e-6


def test_deduplicate_preserves_distinct_names():
    regions = [
        MetaFaceRegion(name="Alice", bbox_norm=(0.1, 0.1, 0.1, 0.1), source="mwg-rs"),
        MetaFaceRegion(name="Bob", bbox_norm=(0.5, 0.5, 0.1, 0.1), source="mwg-rs"),
    ]
    result = deduplicate_regions(regions)
    assert len(result) == 2
    assert {r["name"] for r in result} == {"Alice", "Bob"}
