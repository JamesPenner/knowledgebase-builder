"""Integration tests for KB.AM1 — Knowledge Settings schema, DB helpers, and CLI."""
import pytest
from typer.testing import CliRunner

from src.cli import app
from src.db.kb import (
    get_knowledge_settings,
    open_kb,
    set_knowledge_category_enabled,
)
from src.db.registry import open_registry, register_kb
from src.pipeline.knowledge_gates import get_enabled_categories

runner = CliRunner()


def _make_kb(tmp_path, name: str = "test-kb"):
    from src.db.corpus import open_corpus

    kb_folder = tmp_path / "knowledge-bases" / name
    kb_folder.mkdir(parents=True)
    (kb_folder / "reference").mkdir()
    open_corpus(kb_folder / "corpus.db").close()
    open_kb(kb_folder / "knowledge.db").close()

    reg = open_registry(tmp_path)
    register_kb(reg, name, kb_folder.resolve())
    reg.close()
    return kb_folder


# ---------------------------------------------------------------------------
# Schema + seeding
# ---------------------------------------------------------------------------

def test_knowledge_settings_seeded_enabled_by_default(tmp_path):
    conn = open_kb(tmp_path / "knowledge.db")
    settings = get_knowledge_settings(conn)
    conn.close()
    assert settings == {"people": True, "places": True, "dates": True}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def test_set_knowledge_category_enabled_round_trip(tmp_path):
    conn = open_kb(tmp_path / "knowledge.db")
    set_knowledge_category_enabled(conn, "people", False)
    settings = get_knowledge_settings(conn)
    conn.close()
    assert settings["people"] is False
    assert settings["places"] is True
    assert settings["dates"] is True


def test_set_knowledge_category_enabled_rejects_unknown_category(tmp_path):
    conn = open_kb(tmp_path / "knowledge.db")
    with pytest.raises(ValueError):
        set_knowledge_category_enabled(conn, "pets", False)
    conn.close()


def test_get_enabled_categories_reflects_toggles(tmp_path):
    conn = open_kb(tmp_path / "knowledge.db")
    assert get_enabled_categories(conn) == frozenset({"people", "places", "dates"})

    set_knowledge_category_enabled(conn, "dates", False)
    assert get_enabled_categories(conn) == frozenset({"people", "places"})
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_settings_shows_defaults(tmp_path, monkeypatch):
    _make_kb(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["kb", "settings", "test-kb"])
    assert result.exit_code == 0
    assert "people" in result.stdout
    assert "on" in result.stdout


def test_cli_set_setting_updates_and_persists(tmp_path, monkeypatch):
    kb_folder = _make_kb(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["kb", "set-setting", "test-kb", "places", "off"])
    assert result.exit_code == 0

    conn = open_kb(kb_folder / "knowledge.db")
    settings = get_knowledge_settings(conn)
    conn.close()
    assert settings["places"] is False
    assert settings["people"] is True


def test_cli_set_setting_rejects_bad_state(tmp_path, monkeypatch):
    _make_kb(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["kb", "set-setting", "test-kb", "places", "maybe"])
    assert result.exit_code != 0


def test_cli_set_setting_rejects_bad_category(tmp_path, monkeypatch):
    _make_kb(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["kb", "set-setting", "test-kb", "pets", "off"])
    assert result.exit_code != 0
