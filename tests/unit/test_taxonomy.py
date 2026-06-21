"""Unit tests for taxonomy generation helpers."""
from src.db.kb import build_taxonomy_data, merge_taxonomy, open_kb


# ---------------------------------------------------------------------------
# build_taxonomy_data
# ---------------------------------------------------------------------------

def test_build_taxonomy_classify_rules(tmp_path):
    """Classify rules are grouped under Tags by category."""
    kb = open_kb(tmp_path / "knowledge.db")
    # builtin rules are seeded on open — Tags section should have Calendar etc.
    data = build_taxonomy_data(kb)
    kb.close()

    assert "Tags" in data
    assert "Calendar" in data["Tags"]
    assert "Technical" in data["Tags"]
    assert "Temporal" in data["Tags"]
    # Christmas Day is a builtin calendar rule
    assert "Christmas Day" in data["Tags"]["Calendar"]
    # morning is a builtin temporal rule
    assert "morning" in data["Tags"]["Temporal"]


def test_build_taxonomy_no_vocabulary_section_when_empty(tmp_path):
    """Keywords section is absent when no accepted/user vocab terms exist."""
    kb = open_kb(tmp_path / "knowledge.db")
    data = build_taxonomy_data(kb)
    kb.close()

    assert "Keywords" not in data


def test_build_taxonomy_vocabulary_accepted(tmp_path):
    """Accepted vocabulary terms appear under Keywords."""
    from src.db.kb import add_vocabulary_term

    kb = open_kb(tmp_path / "knowledge.db")
    add_vocabulary_term(kb, "bridge", "[]", source="accepted")
    add_vocabulary_term(kb, "highway", "[]", source="user")
    add_vocabulary_term(kb, "road", "[]", source="seeded")  # excluded
    data = build_taxonomy_data(kb)
    kb.close()

    assert "Keywords" in data
    assert "bridge" in data["Keywords"]
    assert "highway" in data["Keywords"]
    assert "road" not in data["Keywords"]


def test_build_taxonomy_people(tmp_path):
    """People register appears under People section."""
    from src.db.kb import upsert_person

    kb = open_kb(tmp_path / "knowledge.db")
    upsert_person(kb, "James Penner")
    upsert_person(kb, "Alice Smith")
    data = build_taxonomy_data(kb)
    kb.close()

    assert "People" in data
    assert "James Penner" in data["People"]
    assert "Alice Smith" in data["People"]


def test_build_taxonomy_no_people_section_when_empty(tmp_path):
    kb = open_kb(tmp_path / "knowledge.db")
    data = build_taxonomy_data(kb)
    kb.close()

    assert "People" not in data


def test_build_taxonomy_entity_table(tmp_path):
    """Entity table rows appear under display_name section."""
    from src.db.kb import create_entity_table, register_entity_table, upsert_entity_row

    kb = open_kb(tmp_path / "knowledge.db")
    register_entity_table(kb, "bridge", "Bridges", "bridge", "[]", "bridge_name", "text")
    create_entity_table(kb, "bridge", ["bridge_name"], "bridge_name")
    upsert_entity_row(kb, "bridge", {"bridge_name": "Alpha Bridge"})
    upsert_entity_row(kb, "bridge", {"bridge_name": "Beta Bridge"})
    kb.commit()
    data = build_taxonomy_data(kb)
    kb.close()

    assert "Bridges" in data
    assert "Alpha Bridge" in data["Bridges"]
    assert "Beta Bridge" in data["Bridges"]


# ---------------------------------------------------------------------------
# merge_taxonomy
# ---------------------------------------------------------------------------

def test_merge_adds_new_top_level_key():
    existing = {"Keywords": ["foo"]}
    generated = {"Keywords": ["bar"], "People": ["Alice"]}
    merged = merge_taxonomy(existing, generated)

    assert "People" in merged
    assert "Alice" in merged["People"]


def test_merge_extends_list_without_duplicates():
    existing = {"Keywords": ["foo", "bar"]}
    generated = {"Keywords": ["bar", "baz"]}
    merged = merge_taxonomy(existing, generated)

    assert merged["Keywords"].count("bar") == 1
    assert "baz" in merged["Keywords"]
    assert "foo" in merged["Keywords"]


def test_merge_preserves_user_edits_in_list():
    existing = {"Keywords": ["user_term"]}
    generated = {"Keywords": ["generated_term"]}
    merged = merge_taxonomy(existing, generated)

    assert "user_term" in merged["Keywords"]
    assert "generated_term" in merged["Keywords"]
    # existing comes first
    assert merged["Keywords"].index("user_term") < merged["Keywords"].index("generated_term")


def test_merge_extends_nested_dict_subcategories():
    existing = {"Tags": {"Calendar": ["Christmas Day"]}}
    generated = {"Tags": {"Calendar": ["Christmas Day", "Easter"], "Technical": ["wide_angle"]}}
    merged = merge_taxonomy(existing, generated)

    assert "Easter" in merged["Tags"]["Calendar"]
    assert merged["Tags"]["Calendar"].count("Christmas Day") == 1
    assert "Technical" in merged["Tags"]
    assert "wide_angle" in merged["Tags"]["Technical"]


def test_merge_preserves_keys_only_in_existing():
    existing = {"UserCustomSection": ["custom_term"], "Tags": {"Calendar": ["Christmas Day"]}}
    generated = {"Tags": {"Calendar": ["Easter"]}}
    merged = merge_taxonomy(existing, generated)

    assert "UserCustomSection" in merged
    assert "custom_term" in merged["UserCustomSection"]


def test_merge_empty_existing():
    generated = {"Tags": {"Calendar": ["Christmas Day"]}, "Keywords": ["bridge"]}
    merged = merge_taxonomy({}, generated)

    assert merged == generated


def test_merge_empty_generated():
    existing = {"Tags": {"Calendar": ["Christmas Day"]}}
    merged = merge_taxonomy(existing, {})

    assert merged == existing
