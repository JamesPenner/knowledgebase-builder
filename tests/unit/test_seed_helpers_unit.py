"""Unit tests for seed_location_register and seed_people_register helpers."""
import csv
from pathlib import Path

from src.db.kb import open_kb, seed_location_register, seed_people_register


def _make_kb(tmp_path: Path):
    kb_path = tmp_path / "knowledge.db"
    conn = open_kb(kb_path)
    return conn


def _write_location_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Location", "City", "Latitude", "Longitude", "threshold_m"])
        writer.writeheader()
        writer.writerows(rows)


def _write_people_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["NameID", "First Name", "Last Name", "Title", "Middle Name",
                  "Nick Names", "Prefer NickName", "Metadata Name", "Married Names",
                  "Family", "SpouseID", "birth_date", "date_marriage", "death_date"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# seed_location_register
# ---------------------------------------------------------------------------

def test_seed_locations_imports_rows(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Home", "City": "London", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "200"},
        {"Location": "Office", "City": "London", "Latitude": "51.51", "Longitude": "-0.09", "threshold_m": "100"},
    ])

    n = seed_location_register(conn, csv_path)
    assert n == 2

    rows = conn.execute("SELECT * FROM entity_locations").fetchall()
    assert len(rows) == 2
    conn.close()


def test_seed_locations_idempotent(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Home", "City": "", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "500"},
    ])

    n1 = seed_location_register(conn, csv_path)
    n2 = seed_location_register(conn, csv_path)
    assert n1 == 1
    assert n2 == 0  # skipped because already populated
    conn.close()


def test_seed_locations_registers_gps_match_type(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Park", "City": "", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "300"},
    ])
    seed_location_register(conn, csv_path)

    reg = conn.execute(
        "SELECT match_type FROM entity_table_registry WHERE table_name = 'locations'"
    ).fetchone()
    assert reg is not None
    assert reg["match_type"] == "gps"
    conn.close()


def test_seed_locations_skips_blank_key_rows(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Home", "City": "", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "200"},
        {"Location": "",     "City": "", "Latitude": "0",    "Longitude": "0",    "threshold_m": ""},
        {"Location": "-",    "City": "", "Latitude": "0",    "Longitude": "0",    "threshold_m": ""},
    ])
    n = seed_location_register(conn, csv_path)
    assert n == 1
    conn.close()


# ---------------------------------------------------------------------------
# seed_people_register
# ---------------------------------------------------------------------------

def test_seed_people_imports_rows(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_People.csv"
    _write_people_csv(csv_path, [
        {"NameID": "P001", "First Name": "Alice", "Last Name": "Smith", "Family": "FALSE"},
        {"NameID": "P002", "First Name": "Bob",   "Last Name": "Jones", "Family": "TRUE"},
    ])

    n = seed_people_register(conn, csv_path)
    assert n == 2

    count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert count == 2
    conn.close()


def test_seed_people_idempotent(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_People.csv"
    _write_people_csv(csv_path, [
        {"NameID": "P001", "First Name": "Alice", "Last Name": "Smith", "Family": "FALSE"},
    ])

    n1 = seed_people_register(conn, csv_path)
    n2 = seed_people_register(conn, csv_path)
    assert n1 == 1
    assert n2 == 0
    conn.close()


def test_seed_people_prefer_nickname(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_People.csv"
    _write_people_csv(csv_path, [
        {"NameID": "P001", "First Name": "Robert", "Last Name": "Brown",
         "Nick Names": "Bob", "Prefer NickName": "TRUE", "Family": "FALSE"},
    ])
    seed_people_register(conn, csv_path)

    person = conn.execute("SELECT preferred_name FROM people WHERE id=1").fetchone()
    assert person["preferred_name"] == "Bob"
    conn.close()


def test_seed_people_skips_rows_without_nameid(tmp_path):
    conn = _make_kb(tmp_path)
    csv_path = tmp_path / "Index_of_People.csv"
    _write_people_csv(csv_path, [
        {"NameID": "P001", "First Name": "Alice", "Last Name": "Smith", "Family": "FALSE"},
        {"NameID": "",     "First Name": "Ghost", "Last Name": "Row",   "Family": "FALSE"},
    ])
    n = seed_people_register(conn, csv_path)
    assert n == 1
    conn.close()
