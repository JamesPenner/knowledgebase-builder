"""Integration tests for Stage 1.7 (Entity Match)."""
import json
import threading


from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import (
    create_entity_table,
    open_kb,
    register_entity_table,
    upsert_entity_row,
)
from src.pipeline.progress import NullProgressReporter
from src.stages.entity_match import run_entity_match


def _seed_file_with_gps(corpus_conn, lat: float, lon: float) -> int:
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    corpus_conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'exif_gps_lat', ?, 'float')",
        (str(lat),),
    )
    corpus_conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'exif_gps_lon', ?, 'float')",
        (str(lon),),
    )
    corpus_conn.commit()
    return 1


def _setup_location_table(kb_conn, location_name: str, lat: float, lon: float, threshold: float) -> None:
    create_entity_table(kb_conn, "locations", ["location", "latitude", "longitude", "threshold_m"], "location")
    register_entity_table(kb_conn, "locations", "Locations", "", "[]", "location", "gps", "")
    upsert_entity_row(kb_conn, "locations", {
        "location": location_name,
        "latitude": str(lat),
        "longitude": str(lon),
        "threshold_m": str(threshold),
    })
    kb_conn.commit()


def test_gps_match_within_threshold(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file_with_gps(corpus_conn, 49.2827, -123.1207)
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    # Place entity at same coords, 500 m threshold
    _setup_location_table(kb_conn, "Test Location", 49.2827, -123.1207, 500)
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute("SELECT * FROM file_entity_matches WHERE file_id = 1").fetchall()
    corpus_conn.close()

    assert len(matches) == 1
    assert matches[0]["matched_value"] == "Test Location"
    assert matches[0]["match_source"] == "gps"


def test_gps_match_outside_threshold(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    # File in Vancouver
    _seed_file_with_gps(corpus_conn, 49.2827, -123.1207)
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    # Entity in Victoria, 500 m threshold — too far (~65 km away)
    _setup_location_table(kb_conn, "Victoria", 48.4284, -123.3656, 500)
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    count = corpus_conn.execute("SELECT COUNT(*) FROM file_entity_matches").fetchone()[0]
    corpus_conn.close()
    assert count == 0


def test_text_match_keyword_trigger(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    corpus_conn.execute(
        "INSERT INTO file_metadata_keywords (file_id, canonical_name, keyword)"
        " VALUES (1, 'keywords', 'Garibaldi')"
    )
    corpus_conn.commit()
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    create_entity_table(kb_conn, "parks", ["name", "province"], "name")
    # Empty trigger_word means always scan for key values
    register_entity_table(kb_conn, "parks", "Parks", "", "[]", "name", "text", "")
    upsert_entity_row(kb_conn, "parks", {"name": "Garibaldi", "province": "BC"})
    kb_conn.commit()
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute("SELECT * FROM file_entity_matches WHERE file_id = 1").fetchall()
    corpus_conn.close()
    assert any(m["matched_value"] == "Garibaldi" for m in matches)


def test_people_name_match(tmp_path):
    from src.db.kb import add_person_name, upsert_person

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    corpus_conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'xmp_description', 'Photo with Alice Johnson at the park', 'str')"
    )
    corpus_conn.commit()
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    pid = upsert_person(kb_conn, "Alice Johnson", first_name="Alice", last_name="Johnson")
    add_person_name(kb_conn, pid, "Alice Johnson", is_metadata_form=True)
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute(
        "SELECT * FROM file_entity_matches WHERE file_id = 1 AND table_name = 'people'"
    ).fetchall()
    corpus_conn.close()
    assert len(matches) >= 1
    payloads = [json.loads(m["payload_json"]) for m in matches]
    assert any(p.get("person_id") == pid for p in payloads)


# ---------------------------------------------------------------------------
# KB.P8 — linked table traversal tests
# ---------------------------------------------------------------------------

def _add_link(kb_conn, parent_table, parent_column, linked_table, linked_key_column,
              label="", include_in_text_pool=1):
    kb_conn.execute(
        "INSERT INTO entity_table_links"
        " (parent_table, parent_column, linked_table, linked_key_column, label, include_in_text_pool)"
        " VALUES (?,?,?,?,?,?)",
        (parent_table, parent_column, linked_table, linked_key_column, label, include_in_text_pool),
    )
    kb_conn.commit()


def _seed_file_with_keyword(corpus_conn, keyword: str) -> int:
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    corpus_conn.execute(
        "INSERT INTO file_metadata_keywords (file_id, canonical_name, keyword) VALUES (1, 'keywords', ?)",
        (keyword,),
    )
    corpus_conn.commit()
    return 1


def test_text_match_full_row_in_payload(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file_with_keyword(corpus_conn, "Kamloops")
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    create_entity_table(kb_conn, "cities", ["name", "region"], "name")
    register_entity_table(kb_conn, "cities", "Cities", "", "[]", "name", "text", "")
    upsert_entity_row(kb_conn, "cities", {"name": "Kamloops", "region": "BC"})
    kb_conn.commit()
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute(
        "SELECT * FROM file_entity_matches WHERE table_name = 'cities'"
    ).fetchall()
    corpus_conn.close()

    assert len(matches) == 1
    payload = json.loads(matches[0]["payload_json"])
    assert payload.get("name") == "Kamloops"
    assert payload.get("region") == "BC"
    assert "matched_key" not in payload


def test_linked_table_resolved_in_text_payload(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file_with_keyword(corpus_conn, "Annacis")
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    create_entity_table(kb_conn, "bridges", ["name", "highway_id"], "name")
    register_entity_table(kb_conn, "bridges", "Bridges", "", "[]", "name", "text", "")
    upsert_entity_row(kb_conn, "bridges", {"name": "Annacis", "highway_id": "5"})

    create_entity_table(kb_conn, "highways", ["id", "name", "route_number"], "id")
    register_entity_table(kb_conn, "highways", "Highways", "", "[]", "id", "text", "")
    upsert_entity_row(kb_conn, "highways", {"id": "5", "name": "Coquihalla Highway", "route_number": "5"})

    kb_conn.commit()
    _add_link(kb_conn, "bridges", "highway_id", "highways", "id", label="highway")
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute(
        "SELECT * FROM file_entity_matches WHERE table_name = 'bridges'"
    ).fetchall()
    corpus_conn.close()

    assert len(matches) == 1
    payload = json.loads(matches[0]["payload_json"])
    assert "_links" in payload
    assert payload["_links"]["highway"]["name"] == "Coquihalla Highway"


def test_gps_match_includes_links(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file_with_gps(corpus_conn, 49.0, -120.0)
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    create_entity_table(
        kb_conn, "locations",
        ["location", "latitude", "longitude", "threshold_m", "region_id"], "location",
    )
    register_entity_table(kb_conn, "locations", "Locations", "", "[]", "location", "gps", "")
    upsert_entity_row(kb_conn, "locations", {
        "location": "Test Peak",
        "latitude": "49.0",
        "longitude": "-120.0",
        "threshold_m": "500",
        "region_id": "7",
    })

    create_entity_table(kb_conn, "regions", ["id", "name"], "id")
    register_entity_table(kb_conn, "regions", "Regions", "", "[]", "id", "text", "")
    upsert_entity_row(kb_conn, "regions", {"id": "7", "name": "Interior BC"})

    kb_conn.commit()
    _add_link(kb_conn, "locations", "region_id", "regions", "id", label="region")
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute(
        "SELECT * FROM file_entity_matches WHERE table_name = 'locations'"
    ).fetchall()
    corpus_conn.close()

    assert len(matches) == 1
    payload = json.loads(matches[0]["payload_json"])
    assert "_links" in payload
    assert payload["_links"]["region"]["name"] == "Interior BC"


def test_orphaned_fk_omitted(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file_with_keyword(corpus_conn, "Annacis")
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    create_entity_table(kb_conn, "bridges", ["name", "highway_id"], "name")
    register_entity_table(kb_conn, "bridges", "Bridges", "", "[]", "name", "text", "")
    upsert_entity_row(kb_conn, "bridges", {"name": "Annacis", "highway_id": "99"})

    create_entity_table(kb_conn, "highways", ["id", "name"], "id")
    register_entity_table(kb_conn, "highways", "Highways", "", "[]", "id", "text", "")
    # No highway row with id="99" — orphaned FK

    kb_conn.commit()
    _add_link(kb_conn, "bridges", "highway_id", "highways", "id", label="highway")
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute(
        "SELECT * FROM file_entity_matches WHERE table_name = 'bridges'"
    ).fetchall()
    corpus_conn.close()

    assert len(matches) == 1
    payload = json.loads(matches[0]["payload_json"])
    assert not payload.get("_links")


def test_cycle_detection(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file_with_keyword(corpus_conn, "alpha")
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    create_entity_table(kb_conn, "aa", ["name", "bb_id"], "name")
    register_entity_table(kb_conn, "aa", "AA", "", "[]", "name", "text", "")
    upsert_entity_row(kb_conn, "aa", {"name": "alpha", "bb_id": "1"})

    create_entity_table(kb_conn, "bb", ["id", "name", "aa_id"], "id")
    register_entity_table(kb_conn, "bb", "BB", "", "[]", "id", "text", "")
    upsert_entity_row(kb_conn, "bb", {"id": "1", "name": "beta", "aa_id": "alpha"})

    kb_conn.commit()
    _add_link(kb_conn, "aa", "bb_id", "bb", "id", label="bb_link")
    _add_link(kb_conn, "bb", "aa_id", "aa", "name", label="aa_link")
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute(
        "SELECT * FROM file_entity_matches WHERE table_name = 'aa'"
    ).fetchall()
    corpus_conn.close()

    assert len(matches) == 1
    payload = json.loads(matches[0]["payload_json"])
    assert "bb_link" in payload["_links"]
    assert "_links" not in payload["_links"]["bb_link"]


def test_max_depth_enforced(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file_with_keyword(corpus_conn, "node1")
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    create_entity_table(kb_conn, "t1", ["name", "t2_id"], "name")
    register_entity_table(kb_conn, "t1", "T1", "", "[]", "name", "text", "")
    upsert_entity_row(kb_conn, "t1", {"name": "node1", "t2_id": "10"})

    for tname, cols, row in [
        ("t2", ["id", "name", "t3_id"], {"id": "10", "name": "n2", "t3_id": "20"}),
        ("t3", ["id", "name", "t4_id"], {"id": "20", "name": "n3", "t4_id": "30"}),
        ("t4", ["id", "name", "t5_id"], {"id": "30", "name": "n4", "t5_id": "40"}),
        ("t5", ["id", "name"],          {"id": "40", "name": "n5"}),
    ]:
        create_entity_table(kb_conn, tname, cols, "id")
        register_entity_table(kb_conn, tname, tname.upper(), "", "[]", "id", "text", "")
        upsert_entity_row(kb_conn, tname, row)

    kb_conn.commit()
    _add_link(kb_conn, "t1", "t2_id", "t2", "id", label="t2_link")
    _add_link(kb_conn, "t2", "t3_id", "t3", "id", label="t3_link")
    _add_link(kb_conn, "t3", "t4_id", "t4", "id", label="t4_link")
    _add_link(kb_conn, "t4", "t5_id", "t5", "id", label="t5_link")
    kb_conn.close()

    run_entity_match(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    matches = corpus_conn.execute(
        "SELECT * FROM file_entity_matches WHERE table_name = 't1'"
    ).fetchall()
    corpus_conn.close()

    assert len(matches) == 1
    payload = json.loads(matches[0]["payload_json"])
    l2 = payload["_links"]["t2_link"]
    l3 = l2["_links"]["t3_link"]
    l4 = l3["_links"]["t4_link"]
    assert "_links" not in l4  # depth exhausted before t5
