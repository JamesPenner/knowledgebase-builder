"""Unit tests for seed_entity_links and seed_entity_bundle (src/db/kb.py)."""
import csv

from src.db.kb import seed_entity_bundle, seed_entity_links


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _make_entities_dir(tmp_path, tables, links=None):
    """Build a minimal entities/ bundle directory."""
    entities = tmp_path / "entities"
    entities.mkdir()
    registry_rows = []
    for t in tables:
        name = t["table_name"]
        key_col = t["key_column"]
        cols = t["columns"]
        data = t.get("data", [])
        registry_rows.append({
            "table_name": name,
            "display_name": t.get("display_name", name.title()),
            "trigger_word": t.get("trigger_word", name),
            "trigger_aliases": t.get("trigger_aliases", "[]"),
            "key_column": key_col,
            "match_type": t.get("match_type", "text"),
            "description": "",
            "source_csv": "",
        })
        _write_csv(entities / f"{name}.csv", cols, data)

    _write_csv(
        entities / "_registry.csv",
        ["table_name", "display_name", "trigger_word", "trigger_aliases",
         "key_column", "match_type", "description", "source_csv"],
        registry_rows,
    )

    if links is not None:
        _write_csv(
            entities / "_links.csv",
            ["parent_table", "parent_column", "linked_table", "linked_key_column",
             "label", "include_in_text_pool"],
            links,
        )

    return entities


# ---------------------------------------------------------------------------
# seed_entity_links tests
# ---------------------------------------------------------------------------

def test_seed_entity_links_empty_list(kb_db):
    result = seed_entity_links(kb_db, [])
    assert result == 0


def test_seed_entity_links_inserts_row(kb_db):
    link = {
        "parent_table": "bridge", "parent_column": "highway_id",
        "linked_table": "highway", "linked_key_column": "id",
        "label": "via highway", "include_in_text_pool": "1",
    }
    result = seed_entity_links(kb_db, [link])
    assert result == 1
    row = kb_db.execute("SELECT * FROM entity_table_links").fetchone()
    assert row["parent_table"] == "bridge"
    assert row["include_in_text_pool"] == 1


def test_seed_entity_links_idempotent(kb_db):
    link = {
        "parent_table": "bridge", "parent_column": "highway_id",
        "linked_table": "highway", "linked_key_column": "id",
        "label": "", "include_in_text_pool": "0",
    }
    seed_entity_links(kb_db, [link])
    seed_entity_links(kb_db, [link])
    count = kb_db.execute("SELECT COUNT(*) FROM entity_table_links").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# seed_entity_bundle tests
# ---------------------------------------------------------------------------

def test_missing_dir_is_noop(kb_db, tmp_path):
    result = seed_entity_bundle(kb_db, tmp_path / "nonexistent")
    assert result == (0, 0, 0)


def test_no_registry_is_noop(kb_db, tmp_path):
    entities = tmp_path / "entities"
    entities.mkdir()
    result = seed_entity_bundle(kb_db, entities)
    assert result == (0, 0, 0)


def test_single_table_imported(kb_db, tmp_path):
    entities = _make_entities_dir(tmp_path, [
        {
            "table_name": "bridges",
            "key_column": "name",
            "columns": ["name", "type"],
            "data": [
                {"name": "Lions Gate", "type": "suspension"},
                {"name": "Granville", "type": "bascule"},
            ],
        }
    ])
    tables, rows, links = seed_entity_bundle(kb_db, entities)
    assert tables == 1
    assert rows == 2
    assert links == 0

    reg = kb_db.execute(
        "SELECT * FROM entity_table_registry WHERE table_name='bridges'"
    ).fetchone()
    assert reg is not None
    assert reg["key_column"] == "name"

    data = kb_db.execute('SELECT * FROM "entity_bridges"').fetchall()
    assert len(data) == 2


def test_two_tables_with_links(kb_db, tmp_path):
    entities = _make_entities_dir(
        tmp_path,
        tables=[
            {
                "table_name": "bridges",
                "key_column": "bridge_id",
                "columns": ["bridge_id", "name", "highway_id"],
                "data": [{"bridge_id": "B1", "name": "Summit", "highway_id": "H5"}],
            },
            {
                "table_name": "highways",
                "key_column": "id",
                "columns": ["id", "name"],
                "data": [{"id": "H5", "name": "Coquihalla"}],
            },
        ],
        links=[
            {
                "parent_table": "bridges", "parent_column": "highway_id",
                "linked_table": "highways", "linked_key_column": "id",
                "label": "route", "include_in_text_pool": "1",
            }
        ],
    )
    tables, rows, links = seed_entity_bundle(kb_db, entities)
    assert tables == 2
    assert rows == 2
    assert links == 1

    lnk = kb_db.execute("SELECT * FROM entity_table_links").fetchone()
    assert lnk["parent_table"] == "bridges"
    assert lnk["linked_table"] == "highways"
    assert lnk["include_in_text_pool"] == 1


def test_idempotent_rerun(kb_db, tmp_path):
    entities = _make_entities_dir(tmp_path, [
        {
            "table_name": "bridges",
            "key_column": "name",
            "columns": ["name", "type"],
            "data": [{"name": "Lions Gate", "type": "suspension"}],
        }
    ])
    seed_entity_bundle(kb_db, entities)
    seed_entity_bundle(kb_db, entities)

    count = kb_db.execute('SELECT COUNT(*) FROM "entity_bridges"').fetchone()[0]
    assert count == 1
    reg_count = kb_db.execute("SELECT COUNT(*) FROM entity_table_registry").fetchone()[0]
    assert reg_count == 1
