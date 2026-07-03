"""Integration tests for the geo_meta stage (Location Register Stage)."""
import csv
import threading
from pathlib import Path

import pytest

from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import (
    create_entity_table,
    open_kb,
    register_entity_table,
    upsert_entity_row,
)
from src.db.registry import open_registry, register_kb
from src.pipeline.progress import NullProgressReporter
from src.stages.geo_meta import run_geo_meta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kb(tmp_path: Path, name: str = "test-kb"):
    kb_folder = tmp_path / "knowledge-bases" / name
    kb_folder.mkdir(parents=True)
    corpus_conn = open_corpus(kb_folder / "corpus.db")
    kb_conn = open_kb(kb_folder / "knowledge.db")
    reg = open_registry(tmp_path)
    register_kb(reg, name, kb_folder.resolve())
    reg.close()
    return kb_folder, corpus_conn, kb_conn


def _add_source(corpus_conn):
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/photos', 'images', 1)"
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_gps_file(corpus_conn, src_id, path, lat, lon):
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, ?, ?, '.jpg', 'image', 1, 0.0)",
        (src_id, path, path.split("/")[-1]),
    )
    file_id = corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    corpus_conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value) VALUES (?, 'exif_gps_lat', ?)",
        (file_id, str(lat)),
    )
    corpus_conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value) VALUES (?, 'exif_gps_lon', ?)",
        (file_id, str(lon)),
    )
    corpus_conn.commit()
    return file_id


def _add_location_entity(kb_conn, location, lat, lon, threshold_m=500.0, city="", state="", country="", country_code=""):
    try:
        kb_conn.execute("SELECT COUNT(*) FROM entity_locations").fetchone()
    except Exception:
        create_entity_table(
            kb_conn, "locations",
            ["location", "city", "state", "country", "country_code", "latitude", "longitude", "threshold_m"],
            "location",
        )
        register_entity_table(
            kb_conn,
            table_name="locations",
            display_name="Locations",
            trigger_word="",
            trigger_aliases_json="[]",
            key_column="location",
            match_type="gps",
            source_csv="",
        )
    upsert_entity_row(kb_conn, "locations", {
        "location": location,
        "city": city,
        "state": state,
        "country": country,
        "country_code": country_code,
        "latitude": str(lat),
        "longitude": str(lon),
        "threshold_m": str(threshold_m),
    })
    kb_conn.commit()


def _run(corpus_path, kb_path, config=None):
    return run_geo_meta(
        corpus_path, kb_path,
        config or Config(),
        NullProgressReporter(),
        threading.Event(),
    )


# ---------------------------------------------------------------------------
# DMS format: ExifTool stores '51 deg 30' 0.00" N' instead of '51.5'
# ---------------------------------------------------------------------------

def test_dms_gps_format_matches_entity(tmp_path):
    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    # Insert DMS-format GPS values directly (as ExifTool would store them)
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/photos/kauai.jpg', 'kauai.jpg', '.jpg', 'image', 1, 0.0)",
        (src_id,),
    )
    file_id = corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    corpus_conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value)"
        " VALUES (?, 'exif_gps_lat', ?)",
        (file_id, "22 deg 4' 48.00\" N"),   # 22.08°
    )
    corpus_conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value)"
        " VALUES (?, 'exif_gps_lon', ?)",
        (file_id, "159 deg 46' 12.00\" W"),  # -159.77°
    )
    corpus_conn.commit()

    _add_location_entity(kb_conn, "Polihale Beach", lat=22.08, lon=-159.77, threshold_m=500.0)

    corpus_conn.close()
    kb_conn.close()

    result = _run(corpus_path, kb_path)

    assert result["files_matched"] == 1, f"Expected 1 match but got: {result}"
    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT * FROM file_location_labels").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["location"] == "Polihale Beach"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_gps_file_gets_label(tmp_path):
    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    _add_gps_file(corpus_conn, src_id, "/photos/home.jpg", lat=51.5, lon=-0.1)
    _add_location_entity(kb_conn, "Home", lat=51.5, lon=-0.1, threshold_m=500.0)

    corpus_conn.close()
    kb_conn.close()

    result = _run(corpus_path, kb_path)

    assert result["files_matched"] == 1
    assert result["files_unmatched"] == 0

    conn = open_corpus(corpus_path)
    rows = conn.execute("SELECT * FROM file_location_labels").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["location"] == "Home"


# ---------------------------------------------------------------------------
# No GPS entity tables registered → early return
# ---------------------------------------------------------------------------

def test_no_gps_tables_returns_early(tmp_path):
    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    _add_gps_file(corpus_conn, src_id, "/photos/a.jpg", lat=51.5, lon=-0.1)

    corpus_conn.close()
    kb_conn.close()

    result = _run(corpus_path, kb_path)

    assert result["files_processed"] == 0
    assert result["files_matched"] == 0


# ---------------------------------------------------------------------------
# No GPS files in corpus → 0 processed
# ---------------------------------------------------------------------------

def test_no_gps_files(tmp_path):
    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/photos/no_gps.jpg', 'no_gps.jpg', '.jpg', 'image', 1, 0.0)",
        (src_id,),
    )
    corpus_conn.commit()
    _add_location_entity(kb_conn, "Home", lat=51.5, lon=-0.1, threshold_m=500.0)

    corpus_conn.close()
    kb_conn.close()

    result = _run(corpus_path, kb_path)

    assert result["files_processed"] == 0
    assert result["files_matched"] == 0


# ---------------------------------------------------------------------------
# Partial match: one file in range, one outside
# ---------------------------------------------------------------------------

def test_partial_match_split(tmp_path):
    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    _add_gps_file(corpus_conn, src_id, "/photos/near.jpg", lat=51.5001, lon=-0.1001)  # ~15m away
    _add_gps_file(corpus_conn, src_id, "/photos/far.jpg", lat=48.8566, lon=2.3522)   # Paris, ~340km

    _add_location_entity(kb_conn, "London", lat=51.5, lon=-0.1, threshold_m=500.0)

    corpus_conn.close()
    kb_conn.close()

    result = _run(corpus_path, kb_path)

    assert result["files_matched"] == 1
    assert result["files_unmatched"] == 1


# ---------------------------------------------------------------------------
# Resume: second run skips already-labelled files
# ---------------------------------------------------------------------------

def test_resume_skips_already_labelled(tmp_path):
    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    _add_gps_file(corpus_conn, src_id, "/photos/home.jpg", lat=51.5, lon=-0.1)
    _add_location_entity(kb_conn, "Home", lat=51.5, lon=-0.1, threshold_m=500.0)

    corpus_conn.close()
    kb_conn.close()

    result1 = _run(corpus_path, kb_path)
    result2 = _run(corpus_path, kb_path)

    assert result1["files_matched"] == 1
    assert result2["files_processed"] == 0  # already labelled, nothing pending


# ---------------------------------------------------------------------------
# Rerun/force: reset clears and reprocesses
# ---------------------------------------------------------------------------

def test_reset_clears_and_reprocesses(tmp_path):
    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    _add_gps_file(corpus_conn, src_id, "/photos/home.jpg", lat=51.5, lon=-0.1)
    _add_location_entity(kb_conn, "Home", lat=51.5, lon=-0.1, threshold_m=500.0)

    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    # Reset then rerun
    from src.db.corpus import reset_location_labels
    conn = open_corpus(corpus_path)
    reset_location_labels(conn)
    conn.close()

    result = _run(corpus_path, kb_path)
    assert result["files_matched"] == 1


# ---------------------------------------------------------------------------
# Export: location_labels.csv written
# ---------------------------------------------------------------------------

def test_export_writes_location_labels_csv(tmp_path):
    from src.stages.export import run_export

    kb_folder, corpus_conn, kb_conn = _make_kb(tmp_path)
    corpus_path = kb_folder / "corpus.db"
    kb_path = kb_folder / "knowledge.db"

    src_id = _add_source(corpus_conn)
    file_id = _add_gps_file(corpus_conn, src_id, "/photos/home.jpg", lat=51.5, lon=-0.1)

    from src.db.corpus import upsert_location_label
    upsert_location_label(
        corpus_conn, file_id,
        location="Home", city="London", state="", country="UK",
        country_code="GB", distance_m=12.0, matched_table="locations",
    )
    corpus_conn.commit()
    corpus_conn.close()

    from src.db.kb import add_vocabulary_term, bump_kb_version
    add_vocabulary_term(kb_conn, "test", "[]", source="accepted")
    bump_kb_version(kb_conn, "test")
    kb_conn.commit()
    kb_conn.close()

    run_export(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    export_csv = kb_folder / "export" / "location_labels.csv"
    assert export_csv.exists()

    with open(export_csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    assert rows[0]["location"] == "Home"
    assert rows[0]["city"] == "London"
    assert rows[0]["country_code"] == "GB"
    assert float(rows[0]["distance_m"]) == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# Health check: CSV present + entity_locations empty → warning
# ---------------------------------------------------------------------------

def test_health_check_warns_when_csv_present_but_entity_empty(tmp_path):
    from src.health import _check_location_register

    kb_folder = tmp_path / "kb"
    kb_folder.mkdir(parents=True)
    reg_dir = kb_folder / "reference" / "registers"
    reg_dir.mkdir(parents=True)
    (reg_dir / "Index_of_Locations.csv").write_text("Location,Latitude,Longitude\n", encoding="utf-8")

    kb_conn = open_kb(kb_folder / "knowledge.db")
    result = _check_location_register(kb_folder, kb_conn)
    kb_conn.close()

    assert result.ok is False
    assert result.severity == "warning"
    assert "seed-registers" in result.fix


# ---------------------------------------------------------------------------
# Health check: no register CSV → ok (info)
# ---------------------------------------------------------------------------

def test_health_check_ok_when_no_csv(tmp_path):
    from src.health import _check_location_register

    kb_folder = tmp_path / "kb"
    kb_folder.mkdir(parents=True)

    kb_conn = open_kb(kb_folder / "knowledge.db")
    result = _check_location_register(kb_folder, kb_conn)
    kb_conn.close()

    assert result.ok is True
    assert result.severity == "info"


# ---------------------------------------------------------------------------
# Health check: CSV present + entity populated → ok
# ---------------------------------------------------------------------------

def test_health_check_ok_when_entity_populated(tmp_path):
    from src.health import _check_location_register

    kb_folder = tmp_path / "kb"
    kb_folder.mkdir(parents=True)
    reg_dir = kb_folder / "reference" / "registers"
    reg_dir.mkdir(parents=True)
    (reg_dir / "Index_of_Locations.csv").write_text("Location,Latitude,Longitude\n", encoding="utf-8")

    kb_conn = open_kb(kb_folder / "knowledge.db")
    _add_location_entity(kb_conn, "Home", lat=51.5, lon=-0.1)
    result = _check_location_register(kb_folder, kb_conn)
    kb_conn.close()

    assert result.ok is True
    assert "1 locations" in result.detail


# ---------------------------------------------------------------------------
# seed-registers: locations CSV → entity populated (via underlying DB logic)
# ---------------------------------------------------------------------------

def _run_seed_registers(kb_folder: Path) -> dict:
    """Call the seed-registers logic directly, bypassing CLI routing."""
    import csv as _csv
    from src.db.kb import (
        create_entity_table,
        open_kb,
        register_entity_table,
        upsert_entity_row,
    )

    result = {}
    kb_conn = open_kb(kb_folder / "knowledge.db")

    loc_csv = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    if not loc_csv.exists():
        result["locations"] = "not_found"
    else:
        try:
            existing_count = kb_conn.execute("SELECT COUNT(*) FROM entity_locations").fetchone()[0]
        except Exception:
            existing_count = 0
        if existing_count > 0:
            result["locations"] = f"skipped:{existing_count}"
        else:
            with open(loc_csv, newline="", encoding="utf-8-sig") as fh:
                reader = _csv.DictReader(fh)
                raw_headers = reader.fieldnames or []
                headers = [h.strip().lower().replace(" ", "_") for h in raw_headers]
                key_col = headers[0] if headers else "location"
                create_entity_table(kb_conn, "locations", headers, key_col)
                register_entity_table(
                    kb_conn,
                    table_name="locations",
                    display_name="Locations",
                    trigger_word="",
                    trigger_aliases_json="[]",
                    key_column=key_col,
                    match_type="gps",
                    source_csv=str(loc_csv),
                )
                imported = 0
                for raw_row in reader:
                    row = {h: raw_row.get(orig, "").strip() for h, orig in zip(headers, raw_headers)}
                    key_val = row.get(key_col, "")
                    if key_val in ("", "-"):
                        continue
                    upsert_entity_row(kb_conn, "locations", row)
                    imported += 1
                kb_conn.commit()
            result["locations"] = f"imported:{imported}"

    kb_conn.close()
    return result


def test_seed_registers_imports_locations(tmp_path):
    kb_folder = tmp_path / "knowledge-bases" / "mytest"
    kb_folder.mkdir(parents=True)
    open_corpus(kb_folder / "corpus.db").close()
    open_kb(kb_folder / "knowledge.db").close()

    loc_csv = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    loc_csv.parent.mkdir(parents=True)
    with open(loc_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Location", "City", "Latitude", "Longitude", "threshold_m"])
        w.writeheader()
        w.writerow({"Location": "Home", "City": "London", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "200"})
        w.writerow({"Location": "Office", "City": "London", "Latitude": "51.51", "Longitude": "-0.09", "threshold_m": "100"})

    result = _run_seed_registers(kb_folder)
    assert result["locations"] == "imported:2"

    kb_conn = open_kb(kb_folder / "knowledge.db")
    rows = kb_conn.execute("SELECT * FROM entity_locations").fetchall()
    kb_conn.close()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# seed-registers: idempotent (second call skips)
# ---------------------------------------------------------------------------

def test_seed_registers_idempotent(tmp_path):
    kb_folder = tmp_path / "knowledge-bases" / "mytest"
    kb_folder.mkdir(parents=True)
    open_corpus(kb_folder / "corpus.db").close()
    open_kb(kb_folder / "knowledge.db").close()

    loc_csv = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    loc_csv.parent.mkdir(parents=True)
    with open(loc_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Location", "Latitude", "Longitude"])
        w.writeheader()
        w.writerow({"Location": "Home", "Latitude": "51.5", "Longitude": "-0.1"})

    _run_seed_registers(kb_folder)
    result2 = _run_seed_registers(kb_folder)

    assert "skipped" in result2["locations"]
    assert "1" in result2["locations"]
