"""Unit tests for the field registry and generate_field_map function."""
import csv
import json

from src.db.corpus import open_corpus
from src.stages.field_registry import DEFAULT_FIELDS, generate_field_map


def test_registry_includes_standard_fields():
    tags = {f["ExifTool_Tag"] for f in DEFAULT_FIELDS}
    assert "XMP-dc:Description" in tags
    assert "XMP-dc:Subject" in tags
    assert "EXIF:DateTimeOriginal" in tags
    assert "XMP-xmp:Rating" in tags


def test_generate_field_map_from_known_json(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'image', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    meta = {"XMP-dc:Description": "Test", "EXIF:Make": "Canon", "Unknown:Tag": "value"}
    conn.execute("INSERT INTO file_exif (file_id, metadata_json) VALUES (1, ?)", (json.dumps(meta),))
    conn.commit()

    count = generate_field_map(conn, tmp_path)
    conn.close()

    assert count >= 2
    csv_path = tmp_path / "reference" / "field_map.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    tags = {r["ExifTool_Tag"] for r in rows}
    assert "XMP-dc:Description" in tags
    assert "EXIF:Make" in tags
    assert "Unknown:Tag" not in tags  # not in DEFAULT_FIELDS


def test_field_map_merge_does_not_duplicate(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'image', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    meta = {"XMP-dc:Description": "Test", "EXIF:Make": "Canon"}
    conn.execute("INSERT INTO file_exif (file_id, metadata_json) VALUES (1, ?)", (json.dumps(meta),))
    conn.commit()

    generate_field_map(conn, tmp_path)
    csv_path = tmp_path / "reference" / "field_map.csv"
    with open(csv_path, newline="", encoding="utf-8") as fh:
        count_before = sum(1 for _ in csv.DictReader(fh))

    added = generate_field_map(conn, tmp_path)
    conn.close()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        count_after = sum(1 for _ in csv.DictReader(fh))

    assert added == 0
    assert count_after == count_before
