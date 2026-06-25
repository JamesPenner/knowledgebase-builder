"""Unit tests for the prompt library: DB helpers and stage integration points."""
import sqlite3

import pytest

from src.db.kb import (
    delete_stage_prompt,
    load_stage_prompt,
    seed_stage_prompts,
    set_active_stage_prompt,
    upsert_stage_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kb_conn(tmp_path):
    """Minimal knowledge.db with stage_prompts table seeded."""
    from src.db.kb import open_kb
    conn = open_kb(tmp_path / "knowledge.db")
    yield conn
    conn.close()


@pytest.fixture
def bare_conn(tmp_path):
    """Raw SQLite connection with stage_prompts table but no rows."""
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE stage_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL,
            prompt_key TEXT NOT NULL,
            name TEXT NOT NULL,
            body TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            is_builtin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE (stage, prompt_key, name)
        );
    """)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# seed_stage_prompts
# ---------------------------------------------------------------------------

def test_seed_inserts_four_builtin_rows(kb_conn):
    rows = kb_conn.execute(
        "SELECT COUNT(*) FROM stage_prompts WHERE is_builtin=1"
    ).fetchone()[0]
    assert rows == 4


def test_seed_covers_all_four_keys(kb_conn):
    keys = {
        (r["stage"], r["prompt_key"])
        for r in kb_conn.execute("SELECT stage, prompt_key FROM stage_prompts WHERE is_builtin=1").fetchall()
    }
    assert keys == {
        ("describe", "system"),
        ("describe", "aggregate"),
        ("retag", "system"),
        ("summarize", "system"),
    }


def test_seed_marks_builtins_active(kb_conn):
    inactive = kb_conn.execute(
        "SELECT COUNT(*) FROM stage_prompts WHERE is_builtin=1 AND is_active=0"
    ).fetchone()[0]
    assert inactive == 0


def test_seed_is_idempotent(kb_conn):
    seed_stage_prompts(kb_conn)
    seed_stage_prompts(kb_conn)
    rows = kb_conn.execute("SELECT COUNT(*) FROM stage_prompts WHERE is_builtin=1").fetchone()[0]
    assert rows == 4


# ---------------------------------------------------------------------------
# load_stage_prompt
# ---------------------------------------------------------------------------

def test_load_returns_default_when_table_missing(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    conn.row_factory = sqlite3.Row
    result = load_stage_prompt(conn, "describe", "system", default="fallback")
    conn.close()
    assert result == "fallback"


def test_load_returns_default_when_no_active_row(bare_conn):
    result = load_stage_prompt(bare_conn, "describe", "system", default="fallback")
    assert result == "fallback"


def test_load_returns_active_body(bare_conn):
    bare_conn.execute(
        "INSERT INTO stage_prompts (stage, prompt_key, name, body, is_active, is_builtin) "
        "VALUES ('describe', 'system', 'Custom', 'my prompt', 1, 0)"
    )
    bare_conn.commit()
    result = load_stage_prompt(bare_conn, "describe", "system", default="fallback")
    assert result == "my prompt"


def test_load_returns_default_for_unknown_key(kb_conn):
    result = load_stage_prompt(kb_conn, "describe", "nonexistent", default="fallback")
    assert result == "fallback"


# ---------------------------------------------------------------------------
# upsert_stage_prompt
# ---------------------------------------------------------------------------

def test_upsert_creates_new_row(bare_conn):
    prompt_id = upsert_stage_prompt(bare_conn, "retag", "system", "Variant A", "body text")
    assert prompt_id > 0
    row = bare_conn.execute("SELECT * FROM stage_prompts WHERE id=?", (prompt_id,)).fetchone()
    assert row["body"] == "body text"
    assert row["is_builtin"] == 0
    assert row["is_active"] == 0


def test_upsert_updates_body_on_duplicate_name(bare_conn):
    id1 = upsert_stage_prompt(bare_conn, "retag", "system", "Variant A", "v1")
    id2 = upsert_stage_prompt(bare_conn, "retag", "system", "Variant A", "v2")
    assert id1 == id2
    row = bare_conn.execute("SELECT body FROM stage_prompts WHERE id=?", (id1,)).fetchone()
    assert row["body"] == "v2"


# ---------------------------------------------------------------------------
# set_active_stage_prompt
# ---------------------------------------------------------------------------

def test_set_active_deactivates_others(kb_conn):
    new_id = upsert_stage_prompt(kb_conn, "retag", "system", "Custom", "custom body")
    set_active_stage_prompt(kb_conn, "retag", "system", new_id)
    active = kb_conn.execute(
        "SELECT id FROM stage_prompts WHERE stage='retag' AND prompt_key='system' AND is_active=1"
    ).fetchall()
    assert len(active) == 1
    assert active[0]["id"] == new_id


def test_set_active_raises_for_unknown_id(kb_conn):
    with pytest.raises(ValueError):
        set_active_stage_prompt(kb_conn, "retag", "system", 99999)


# ---------------------------------------------------------------------------
# delete_stage_prompt
# ---------------------------------------------------------------------------

def test_delete_user_prompt_succeeds(kb_conn):
    new_id = upsert_stage_prompt(kb_conn, "retag", "system", "ToDelete", "body")
    delete_stage_prompt(kb_conn, new_id)
    row = kb_conn.execute("SELECT id FROM stage_prompts WHERE id=?", (new_id,)).fetchone()
    assert row is None


def test_delete_builtin_raises(kb_conn):
    builtin = kb_conn.execute(
        "SELECT id FROM stage_prompts WHERE is_builtin=1 LIMIT 1"
    ).fetchone()
    with pytest.raises(ValueError, match="Built-in"):
        delete_stage_prompt(kb_conn, builtin["id"])


def test_delete_active_user_prompt_reactivates_builtin(kb_conn):
    new_id = upsert_stage_prompt(kb_conn, "retag", "system", "Active Variant", "body")
    set_active_stage_prompt(kb_conn, "retag", "system", new_id)
    delete_stage_prompt(kb_conn, new_id)
    active = kb_conn.execute(
        "SELECT is_builtin FROM stage_prompts WHERE stage='retag' AND prompt_key='system' AND is_active=1"
    ).fetchone()
    assert active is not None
    assert active["is_builtin"] == 1


# ---------------------------------------------------------------------------
# Stage function integration points
# ---------------------------------------------------------------------------

def test_build_system_prompt_custom_base_no_focus():
    from src.stages.summarize import _build_system_prompt
    result = _build_system_prompt("", base="Custom base text.")
    assert result == "Custom base text."


def test_build_system_prompt_custom_base_with_focus():
    from src.stages.summarize import _build_system_prompt
    result = _build_system_prompt("wildlife", base="Custom base.")
    assert result == "Custom base.\nDOMAIN FOCUS: wildlife"


def test_aggregate_descriptions_custom_instruction():
    from src.stages.describe import _aggregate_descriptions

    class _FakeSession:
        def generate(self, system, user, **_):
            return user

    session = _FakeSession()
    result = _aggregate_descriptions(
        ["Frame one desc.", "Frame two desc."],
        focus="",
        session=session,
        instruction="MY CUSTOM INSTRUCTION",
    )
    assert "MY CUSTOM INSTRUCTION" in result


def test_build_describe_prompt_custom_base():
    from src.stages.describe import _build_describe_prompt
    result = _build_describe_prompt([], [], focus="", base_prompt="My custom base.")
    assert result == "My custom base."
