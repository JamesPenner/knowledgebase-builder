"""Built-in field registry and field_map.csv generator for Stage 1.5."""
import csv
import json
import sqlite3
from pathlib import Path

# Column order matches the catalogue application's field_map.csv format.
_FIELDNAMES = [
    "CanonicalName", "ExifTool_Tag", "Priority", "DataType",
    "Category", "enrichment_text", "write_back", "extract_to_column", "rename_token",
]

DEFAULT_FIELDS: list[dict] = [
    # --- Description ---
    {"ExifTool_Tag": "XMP-dc:Description",              "CanonicalName": "description",      "Priority": 1, "DataType": "str",          "Category": "description", "enrichment_text": 1, "write_back": 1, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "XMP-dc:Title",                    "CanonicalName": "title",            "Priority": 1, "DataType": "str",          "Category": "description", "enrichment_text": 1, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "XMP-photoshop:Headline",          "CanonicalName": "headline",         "Priority": 1, "DataType": "str",          "Category": "description", "enrichment_text": 1, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:Headline",                   "CanonicalName": "headline",         "Priority": 2, "DataType": "str",          "Category": "description", "enrichment_text": 1, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:Caption-Abstract",           "CanonicalName": "caption",          "Priority": 1, "DataType": "str",          "Category": "description", "enrichment_text": 1, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- Keywords ---
    {"ExifTool_Tag": "XMP-dc:Subject",                  "CanonicalName": "keywords",         "Priority": 1, "DataType": "keyword_list", "Category": "description", "enrichment_text": 1, "write_back": 1, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:Keywords",                   "CanonicalName": "keywords_iptc",    "Priority": 1, "DataType": "keyword_list", "Category": "description", "enrichment_text": 1, "write_back": 1, "extract_to_column": "", "rename_token": 0},
    # --- Date ---
    {"ExifTool_Tag": "EXIF:DateTimeOriginal",           "CanonicalName": "exif_date_taken",  "Priority": 1, "DataType": "datetime",     "Category": "date",        "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:CreateDate",                 "CanonicalName": "exif_date_taken",  "Priority": 2, "DataType": "datetime",     "Category": "date",        "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "XMP:DateTimeOriginal",            "CanonicalName": "exif_date_taken",  "Priority": 3, "DataType": "datetime",     "Category": "date",        "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "XMP:CreateDate",                  "CanonicalName": "exif_date_taken",  "Priority": 4, "DataType": "datetime",     "Category": "date",        "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:DateCreated",                "CanonicalName": "exif_date_taken",  "Priority": 5, "DataType": "datetime",     "Category": "date",        "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "H264:DateTimeOriginal",           "CanonicalName": "exif_date_taken",  "Priority": 6, "DataType": "datetime",     "Category": "date",        "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "QuickTime:CreateDate",            "CanonicalName": "video_create_date","Priority": 1, "DataType": "datetime",     "Category": "date",        "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- Camera ---
    {"ExifTool_Tag": "EXIF:Make",                       "CanonicalName": "exif_camera_make", "Priority": 1, "DataType": "str",          "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "QuickTime:Make",                  "CanonicalName": "exif_camera_make", "Priority": 2, "DataType": "str",          "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:Model",                      "CanonicalName": "exif_camera_model","Priority": 1, "DataType": "str",          "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "QuickTime:Model",                 "CanonicalName": "exif_camera_model","Priority": 2, "DataType": "str",          "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:FocalLengthIn35mmFormat",    "CanonicalName": "focal_length_35mm","Priority": 1, "DataType": "float",        "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:FocalLength",                "CanonicalName": "focal_length",     "Priority": 1, "DataType": "float",        "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:Aperture",                   "CanonicalName": "aperture",         "Priority": 1, "DataType": "float",        "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:ExposureTime",               "CanonicalName": "shutter_speed",    "Priority": 1, "DataType": "float",        "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:Orientation",                "CanonicalName": "exif_orientation", "Priority": 1, "DataType": "int",          "Category": "camera",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- GPS ---
    {"ExifTool_Tag": "Composite:GPSLatitude",           "CanonicalName": "exif_gps_lat",     "Priority": 1, "DataType": "float",        "Category": "gps",         "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:GPSLatitude",                "CanonicalName": "exif_gps_lat",     "Priority": 2, "DataType": "float",        "Category": "gps",         "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "Composite:GPSLongitude",          "CanonicalName": "exif_gps_lon",     "Priority": 1, "DataType": "float",        "Category": "gps",         "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:GPSLongitude",               "CanonicalName": "exif_gps_lon",     "Priority": 2, "DataType": "float",        "Category": "gps",         "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- Dimensions ---
    {"ExifTool_Tag": "File:ImageWidth",                 "CanonicalName": "exif_width",       "Priority": 1, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:ExifImageWidth",             "CanonicalName": "exif_width",       "Priority": 2, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:ImageWidth",                 "CanonicalName": "exif_width",       "Priority": 3, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "QuickTime:ImageWidth",            "CanonicalName": "exif_width",       "Priority": 4, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "File:ImageHeight",                "CanonicalName": "exif_height",      "Priority": 1, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:ExifImageHeight",            "CanonicalName": "exif_height",      "Priority": 2, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:ImageHeight",                "CanonicalName": "exif_height",      "Priority": 3, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "QuickTime:ImageHeight",           "CanonicalName": "exif_height",      "Priority": 4, "DataType": "int",          "Category": "dimensions",  "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- Video ---
    {"ExifTool_Tag": "QuickTime:Duration",              "CanonicalName": "video_duration",   "Priority": 1, "DataType": "float",        "Category": "video",       "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "Matroska:Duration",               "CanonicalName": "video_duration",   "Priority": 2, "DataType": "float",        "Category": "video",       "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "RIFF:Duration",                   "CanonicalName": "video_duration",   "Priority": 3, "DataType": "float",        "Category": "video",       "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- People ---
    {"ExifTool_Tag": "XMP-iptcExt:PersonInImage",       "CanonicalName": "person_in_image",  "Priority": 1, "DataType": "str",          "Category": "people",      "enrichment_text": 1, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:By-line",                    "CanonicalName": "person_in_image",  "Priority": 2, "DataType": "str",          "Category": "people",      "enrichment_text": 1, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- Location ---
    {"ExifTool_Tag": "XMP-photoshop:City",              "CanonicalName": "location_city",    "Priority": 1, "DataType": "str",          "Category": "location",    "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:City",                       "CanonicalName": "location_city",    "Priority": 2, "DataType": "str",          "Category": "location",    "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "XMP-photoshop:State",             "CanonicalName": "location_state",   "Priority": 1, "DataType": "str",          "Category": "location",    "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:Province-State",             "CanonicalName": "location_state",   "Priority": 2, "DataType": "str",          "Category": "location",    "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "XMP-photoshop:Country",           "CanonicalName": "location_country", "Priority": 1, "DataType": "str",          "Category": "location",    "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "IPTC:Country-PrimaryLocationName","CanonicalName": "location_country", "Priority": 2, "DataType": "str",          "Category": "location",    "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- Events ---
    {"ExifTool_Tag": "XMP-iptcExt:Event",               "CanonicalName": "event",            "Priority": 1, "DataType": "str",          "Category": "events",      "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    # --- Technical ---
    {"ExifTool_Tag": "XMP-xmp:Rating",                  "CanonicalName": "rating",           "Priority": 1, "DataType": "int",          "Category": "technical",   "enrichment_text": 0, "write_back": 1, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:ISO",                        "CanonicalName": "iso",              "Priority": 1, "DataType": "int",          "Category": "technical",   "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
    {"ExifTool_Tag": "EXIF:Flash",                      "CanonicalName": "flash",            "Priority": 1, "DataType": "int",          "Category": "technical",   "enrichment_text": 0, "write_back": 0, "extract_to_column": "", "rename_token": 0},
]


def generate_field_map(conn: sqlite3.Connection, kb_folder: Path) -> int:
    """Scan corpus file_exif JSON, match against DEFAULT_FIELDS, write/merge reference/field_map.csv.

    Returns number of rows written (0 if CSV already up to date).
    """
    rows = conn.execute(
        "SELECT metadata_json FROM file_exif WHERE metadata_json IS NOT NULL"
    ).fetchall()

    seen_keys: set[str] = set()
    for row in rows:
        try:
            data = json.loads(row["metadata_json"])
            if isinstance(data, dict):
                seen_keys.update(data.keys())
        except (json.JSONDecodeError, TypeError):
            pass

    matched = [f for f in DEFAULT_FIELDS if f["ExifTool_Tag"] in seen_keys]
    ref_dir = kb_folder / "reference"
    ref_dir.mkdir(exist_ok=True)
    csv_path = ref_dir / "field_map.csv"

    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
        existing_tags = {r["ExifTool_Tag"] for r in existing}
        to_add = [m for m in matched if m["ExifTool_Tag"] not in existing_tags]
        if not to_add:
            return 0
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_FIELDNAMES)
            for entry in to_add:
                writer.writerow({k: entry.get(k, "") for k in _FIELDNAMES})
        return len(to_add)

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for entry in matched:
            writer.writerow({k: entry.get(k, "") for k in _FIELDNAMES})
    return len(matched)
