"""Integration tests for register seed and template-generation endpoints."""
import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.db.kb import open_kb


def _write_location_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["Location", "City", "Latitude", "Longitude", "threshold_m"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
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


@pytest.fixture()
def kb_folder(tmp_path, monkeypatch):
    folder = tmp_path / "kb"
    folder.mkdir()
    open_kb(folder / "knowledge.db").close()
    monkeypatch.setattr("src.api.pipeline._get_kb_folder", lambda name: folder)
    monkeypatch.setattr("src.api.pipeline._try_open_folder", lambda p: None)
    return folder


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# seed-locations
# ---------------------------------------------------------------------------

def test_seed_locations_success(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Home", "City": "London", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "200"},
        {"Location": "Work", "City": "London", "Latitude": "51.51", "Longitude": "-0.09", "threshold_m": "100"},
    ])

    resp = client.post("/api/stages/seed-locations?kb=testkb")
    assert resp.status_code == 200
    html = resp.text
    assert "wb-gate--done" in html
    assert "2 locations seeded" in html

    conn = open_kb(kb_folder / "knowledge.db")
    rows = conn.execute("SELECT * FROM entity_locations").fetchall()
    conn.close()
    assert len(rows) == 2


def test_seed_locations_already_seeded(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Home", "City": "", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "500"},
    ])
    client.post("/api/stages/seed-locations?kb=testkb")
    resp = client.post("/api/stages/seed-locations?kb=testkb")
    assert resp.status_code == 200
    assert "already seeded" in resp.text


def test_seed_locations_csv_missing_offers_generate(kb_folder, client):
    resp = client.post("/api/stages/seed-locations?kb=testkb")
    assert resp.status_code == 200
    assert "wb-gate--pending" in resp.text
    assert "generate-location-template" in resp.text


def test_seed_locations_response_uses_stable_row_id(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Home", "City": "", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "200"},
    ])
    resp = client.post("/api/stages/seed-locations?kb=testkb")
    assert 'id="gate-geo_meta"' in resp.text


# ---------------------------------------------------------------------------
# seed-people
# ---------------------------------------------------------------------------

def test_seed_people_success(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_People.csv"
    _write_people_csv(csv_path, [
        {"NameID": "P001", "First Name": "Alice", "Last Name": "Smith", "Family": "FALSE"},
        {"NameID": "P002", "First Name": "Bob",   "Last Name": "Jones", "Family": "TRUE"},
    ])

    resp = client.post("/api/stages/seed-people?kb=testkb")
    assert resp.status_code == 200
    assert "wb-gate--done" in resp.text
    assert "2 people seeded" in resp.text

    conn = open_kb(kb_folder / "knowledge.db")
    count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    conn.close()
    assert count == 2


def test_seed_people_already_seeded(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_People.csv"
    _write_people_csv(csv_path, [
        {"NameID": "P001", "First Name": "Alice", "Last Name": "Smith", "Family": "FALSE"},
    ])
    client.post("/api/stages/seed-people?kb=testkb")
    resp = client.post("/api/stages/seed-people?kb=testkb")
    assert resp.status_code == 200
    assert "already seeded" in resp.text


def test_seed_people_csv_missing_offers_generate(kb_folder, client):
    resp = client.post("/api/stages/seed-people?kb=testkb")
    assert resp.status_code == 200
    assert "wb-gate--pending" in resp.text
    assert "generate-people-template" in resp.text


# ---------------------------------------------------------------------------
# generate-location-template
# ---------------------------------------------------------------------------

def test_generate_location_template_creates_file(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    assert not csv_path.exists()

    resp = client.post("/api/stages/generate-location-template?kb=testkb")
    assert resp.status_code == 200
    assert csv_path.exists()


def test_generate_location_template_has_correct_headers(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    client.post("/api/stages/generate-location-template?kb=testkb")

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers = next(reader)

    assert "Location" in headers
    assert "Latitude" in headers
    assert "Longitude" in headers
    assert "threshold_m" in headers


def test_generate_location_template_response_has_seed_button(kb_folder, client):
    resp = client.post("/api/stages/generate-location-template?kb=testkb")
    assert "seed-locations" in resp.text
    assert "Seed Locations" in resp.text


def test_generate_location_template_does_not_overwrite_existing(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_Locations.csv"
    _write_location_csv(csv_path, [
        {"Location": "Home", "City": "", "Latitude": "51.5", "Longitude": "-0.1", "threshold_m": "200"},
    ])
    original_mtime = csv_path.stat().st_mtime

    client.post("/api/stages/generate-location-template?kb=testkb")
    assert csv_path.stat().st_mtime == original_mtime


# ---------------------------------------------------------------------------
# generate-people-template
# ---------------------------------------------------------------------------

def test_generate_people_template_creates_file(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_People.csv"
    assert not csv_path.exists()

    resp = client.post("/api/stages/generate-people-template?kb=testkb")
    assert resp.status_code == 200
    assert csv_path.exists()


def test_generate_people_template_has_correct_headers(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_People.csv"
    client.post("/api/stages/generate-people-template?kb=testkb")

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers = next(reader)

    assert "NameID" in headers
    assert "First Name" in headers
    assert "Last Name" in headers
    assert "birth_date" in headers


def test_generate_people_template_response_has_seed_button(kb_folder, client):
    resp = client.post("/api/stages/generate-people-template?kb=testkb")
    assert "seed-people" in resp.text
    assert "Seed People" in resp.text


def test_generate_people_template_does_not_overwrite_existing(kb_folder, client):
    csv_path = kb_folder / "reference" / "registers" / "Index_of_People.csv"
    _write_people_csv(csv_path, [
        {"NameID": "P001", "First Name": "Alice", "Last Name": "Smith", "Family": "FALSE"},
    ])
    original_mtime = csv_path.stat().st_mtime

    client.post("/api/stages/generate-people-template?kb=testkb")
    assert csv_path.stat().st_mtime == original_mtime
