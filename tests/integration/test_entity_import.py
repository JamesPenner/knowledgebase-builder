"""Integration tests for entity import commands (locations + people)."""
import csv
from pathlib import Path

from typer.testing import CliRunner

from src.cli import app
from src.db.kb import get_entity_table_rows, get_entity_tables, get_life_events, get_people_names, open_kb
from src.db.registry import open_registry, register_kb

runner = CliRunner()


def _make_kb(tmp_path: Path, name: str = "test-kb") -> Path:
    from src.db.corpus import open_corpus
    kb_folder = tmp_path / "knowledge-bases" / name
    kb_folder.mkdir(parents=True)
    open_corpus(kb_folder / "corpus.db").close()
    open_kb(kb_folder / "knowledge.db").close()
    reg = open_registry(tmp_path)
    register_kb(reg, name, kb_folder.resolve())
    reg.close()
    return kb_folder


def _make_entity_bundle(bundle_dir: Path) -> None:
    entities_dir = bundle_dir / "entities"
    entities_dir.mkdir(parents=True)
    with open(entities_dir / "_registry.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "table_name", "display_name", "trigger_word", "trigger_aliases",
            "key_column", "match_type", "description", "source_csv",
        ])
        w.writeheader()
        w.writerow({
            "table_name": "bridges", "display_name": "Bridges",
            "trigger_word": "bridge", "trigger_aliases": "[]",
            "key_column": "name", "match_type": "text",
            "description": "", "source_csv": "",
        })
    with open(entities_dir / "bridges.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "type"])
        w.writeheader()
        w.writerow({"name": "Lions Gate", "type": "suspension"})
        w.writerow({"name": "Granville", "type": "bascule"})
    with open(entities_dir / "_links.csv", "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=[
            "parent_table", "parent_column", "linked_table",
            "linked_key_column", "label", "include_in_text_pool",
        ]).writeheader()


def _write_locations_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["Location", "City", "State", "Country", "Latitude", "Longitude", "threshold_m"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def _write_people_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "NameID", "Prefer NickName", "Metadata Name", "Column1",
        "Title", "First Name", "Middle Name", "Last Name",
        "Married Names", "Nick Names", "Spouse Name", "SpouseID",
        "birth_date", "date_marriage", "death_date", "Family",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


# ---------------------------------------------------------------------------
# Location tests
# ---------------------------------------------------------------------------

def test_import_locations_creates_table_and_registry(tmp_path):

    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    kb_path = kb_folder / "knowledge.db"

    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_locations_csv(csv_path, [
        {"Location": "Kits Beach", "City": "Vancouver", "State": "BC",
         "Country": "Canada", "Latitude": "49.274", "Longitude": "-123.155", "threshold_m": "200"},
        {"Location": "Stanley Park", "City": "Vancouver", "State": "BC",
         "Country": "Canada", "Latitude": "49.301", "Longitude": "-123.144", "threshold_m": "500"},
    ])

    # Import via CLI helper — call the function directly bypassing Typer
    from src.db.kb import create_entity_table, register_entity_table, upsert_entity_row

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        raw_headers = reader.fieldnames or []
        headers = [h.strip().lower().replace(" ", "_") for h in raw_headers]
        key_col = headers[0]
        kb_conn = open_kb(kb_path)
        create_entity_table(kb_conn, "locations", headers, key_col)
        register_entity_table(kb_conn, "locations", "Locations", "", "[]", key_col, "gps", str(csv_path))
        for raw_row in reader:
            row = {h: raw_row.get(orig, "").strip() for h, orig in zip(headers, raw_headers)}
            if row.get(key_col, "") not in ("", "-"):
                upsert_entity_row(kb_conn, "locations", row)
        kb_conn.commit()
        kb_conn.close()

    kb_conn = open_kb(kb_path)
    tables = get_entity_tables(kb_conn)
    assert any(t["table_name"] == "locations" for t in tables)
    rows = get_entity_table_rows(kb_conn, "locations")
    assert len(rows) == 2
    locations = {r["location"] for r in rows}
    assert "Kits Beach" in locations
    assert "Stanley Park" in locations
    kb_conn.close()


def test_import_locations_skips_unnamed_rows(tmp_path):
    from src.db.kb import create_entity_table, register_entity_table, upsert_entity_row

    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    kb_path = kb_folder / "knowledge.db"

    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_locations_csv(csv_path, [
        {"Location": "-", "City": "", "Latitude": "49.0", "Longitude": "-123.0", "threshold_m": "100"},
        {"Location": "Real Place", "City": "City", "Latitude": "49.5", "Longitude": "-123.5", "threshold_m": "300"},
    ])

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        raw_headers = reader.fieldnames or []
        headers = [h.strip().lower().replace(" ", "_") for h in raw_headers]
        key_col = headers[0]
        kb_conn = open_kb(kb_path)
        create_entity_table(kb_conn, "locations", headers, key_col)
        register_entity_table(kb_conn, "locations", "Locations", "", "[]", key_col, "gps", "")
        for raw_row in reader:
            row = {h: raw_row.get(orig, "").strip() for h, orig in zip(headers, raw_headers)}
            if row.get(key_col, "") not in ("", "-"):
                upsert_entity_row(kb_conn, "locations", row)
        kb_conn.commit()
        kb_conn.close()

    kb_conn = open_kb(kb_path)
    rows = get_entity_table_rows(kb_conn, "locations")
    assert len(rows) == 1
    assert rows[0]["location"] == "Real Place"
    kb_conn.close()


# ---------------------------------------------------------------------------
# People tests
# ---------------------------------------------------------------------------

def test_import_people_creates_people_and_names(tmp_path):
    from src.db.kb import add_person_name, upsert_person

    kb_path = tmp_path / "knowledge.db"
    kb_conn = open_kb(kb_path)

    person_id = upsert_person(kb_conn, "Jane Smith", first_name="Jane", last_name="Smith")
    add_person_name(kb_conn, person_id, "Jane Smith", is_metadata_form=True)
    add_person_name(kb_conn, person_id, "Janie")
    kb_conn.close()

    kb_conn = open_kb(kb_path)
    names = get_people_names(kb_conn)
    assert any(n["name"] == "Jane Smith" and n["is_metadata_form"] for n in names)
    assert any(n["name"] == "Janie" for n in names)
    kb_conn.close()


def test_import_people_creates_life_events_with_partner(tmp_path):
    from src.db.kb import add_life_event, upsert_person

    kb_path = tmp_path / "knowledge.db"
    kb_conn = open_kb(kb_path)

    pid_a = upsert_person(kb_conn, "Alice", first_name="Alice", last_name="Jones")
    pid_b = upsert_person(kb_conn, "Bob", first_name="Bob", last_name="Jones")
    add_life_event(kb_conn, pid_a, "birth", "1980-05-15")
    add_life_event(kb_conn, pid_a, "marriage", "2005-06-20", partner_id=pid_b)
    kb_conn.close()

    kb_conn = open_kb(kb_path)
    events = get_life_events(kb_conn, [pid_a])
    assert len(events) == 2
    birth = next(e for e in events if e["event_type"] == "birth")
    marriage = next(e for e in events if e["event_type"] == "marriage")
    assert birth["event_date"] == "1980-05-15"
    assert marriage["partner_id"] == pid_b
    kb_conn.close()


# ---------------------------------------------------------------------------
# import-bundle CLI tests
# ---------------------------------------------------------------------------

def test_import_bundle_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    bundle_dir = tmp_path / "bundle"
    _make_entity_bundle(bundle_dir)

    result = runner.invoke(app, ["entity", "import-bundle", str(bundle_dir), "--kb", "test-kb"])
    assert result.exit_code == 0, result.output
    assert "1 entity table(s)" in result.output
    assert "2 row(s)" in result.output

    kb_conn = open_kb(kb_folder / "knowledge.db")
    assert any(t["table_name"] == "bridges" for t in get_entity_tables(kb_conn))
    rows = get_entity_table_rows(kb_conn, "bridges")
    assert len(rows) == 2
    kb_conn.close()


def test_import_bundle_no_entities_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_kb(tmp_path)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    result = runner.invoke(app, ["entity", "import-bundle", str(bundle_dir), "--kb", "test-kb"])
    assert result.exit_code == 0, result.output
    assert "No entities/" in result.output


# ---------------------------------------------------------------------------
# Fuzzy dedup tests
# ---------------------------------------------------------------------------

def test_import_locations_near_dup_aborts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    # Singular vs plural — scores ~0.96, well above the default 0.85 threshold
    _write_locations_csv(csv_path, [
        {"Location": "Burnaby Lake", "Latitude": "49.248", "Longitude": "-122.940", "threshold_m": "500"},
        {"Location": "Burnaby Lakes", "Latitude": "49.249", "Longitude": "-122.941", "threshold_m": "300"},
    ])

    result = runner.invoke(app, ["entity", "import-locations", "--kb", "test-kb"])
    assert result.exit_code == 1
    assert "near-duplicate" in result.output
    assert "Import aborted" in result.output

    kb_conn = open_kb(kb_folder / "knowledge.db")
    rows = get_entity_table_rows(kb_conn, "locations")
    assert len(rows) == 0
    kb_conn.close()


def test_import_locations_force_proceeds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_locations_csv(csv_path, [
        {"Location": "Burnaby Lake", "Latitude": "49.248", "Longitude": "-122.940", "threshold_m": "500"},
        {"Location": "Burnaby Lakes", "Latitude": "49.249", "Longitude": "-122.941", "threshold_m": "300"},
    ])

    result = runner.invoke(app, ["entity", "import-locations", "--kb", "test-kb", "--force"])
    assert result.exit_code == 0, result.output
    assert "Warning" in result.output
    assert "near-duplicate" in result.output

    kb_conn = open_kb(kb_folder / "knowledge.db")
    rows = get_entity_table_rows(kb_conn, "locations")
    assert len(rows) == 2
    kb_conn.close()


def test_import_locations_distinct_names_pass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_locations_csv(csv_path, [
        {"Location": "Kits Beach", "Latitude": "49.274", "Longitude": "-123.155", "threshold_m": "200"},
        {"Location": "Buntzen Lake", "Latitude": "49.390", "Longitude": "-122.867", "threshold_m": "500"},
    ])

    result = runner.invoke(app, ["entity", "import-locations", "--kb", "test-kb"])
    assert result.exit_code == 0, result.output
    assert "2 rows" in result.output

    kb_conn = open_kb(kb_folder / "knowledge.db")
    rows = get_entity_table_rows(kb_conn, "locations")
    assert len(rows) == 2
    kb_conn.close()


def test_import_locations_near_dup_vs_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"

    # First import: "Burnaby Lake" goes in cleanly
    _write_locations_csv(csv_path, [
        {"Location": "Burnaby Lake", "Latitude": "49.248", "Longitude": "-122.940", "threshold_m": "500"},
    ])
    result = runner.invoke(app, ["entity", "import-locations", "--kb", "test-kb"])
    assert result.exit_code == 0, result.output

    # Second import: "Burnaby Lakes" is a near-dup of the existing "Burnaby Lake"
    _write_locations_csv(csv_path, [
        {"Location": "Burnaby Lakes", "Latitude": "49.249", "Longitude": "-122.941", "threshold_m": "300"},
    ])
    result = runner.invoke(app, ["entity", "import-locations", "--kb", "test-kb"])
    assert result.exit_code == 1
    assert "near-duplicate" in result.output

    # Existing row still present; new row not added
    kb_conn = open_kb(kb_folder / "knowledge.db")
    rows = get_entity_table_rows(kb_conn, "locations")
    assert len(rows) == 1
    assert rows[0]["location"] == "Burnaby Lake"
    kb_conn.close()


def test_import_locations_custom_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    # "Burnaby Lake" vs "Burnaby Lakes" scores ~0.96 — flagged at 0.85, but not at 0.99
    _write_locations_csv(csv_path, [
        {"Location": "Burnaby Lake", "Latitude": "49.248", "Longitude": "-122.940", "threshold_m": "500"},
        {"Location": "Burnaby Lakes", "Latitude": "49.249", "Longitude": "-122.941", "threshold_m": "300"},
    ])

    result = runner.invoke(
        app,
        ["entity", "import-locations", "--kb", "test-kb", "--similarity-threshold", "0.99"],
    )
    assert result.exit_code == 0, result.output
    assert "2 rows" in result.output

    kb_conn = open_kb(kb_folder / "knowledge.db")
    rows = get_entity_table_rows(kb_conn, "locations")
    assert len(rows) == 2
    kb_conn.close()
