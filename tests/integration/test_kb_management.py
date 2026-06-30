"""Integration tests for KB management CRUD: delete, health, --template, --import-kb."""
import csv
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from src.cli import app
from src.db.corpus import open_corpus
from src.db.kb import open_kb
from src.db.registry import delete_kb, list_kbs, open_registry, register_kb

runner = CliRunner()


def _make_kb(tmp_path: Path, name: str = "test-kb") -> Path:
    """Create a minimal KB folder registered in a local registry.db."""
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
# kb delete
# ---------------------------------------------------------------------------

def test_kb_delete_removes_from_registry(tmp_path):
    _make_kb(tmp_path)

    reg = open_registry(tmp_path)
    before = [kb["name"] for kb in list_kbs(reg)]
    assert "test-kb" in before

    delete_kb(reg, "test-kb")
    after = [kb["name"] for kb in list_kbs(reg)]
    reg.close()

    assert "test-kb" not in after


def test_kb_delete_leaves_disk_files(tmp_path):
    kb_folder = _make_kb(tmp_path)

    reg = open_registry(tmp_path)
    delete_kb(reg, "test-kb")
    reg.close()

    assert kb_folder.exists()
    assert (kb_folder / "corpus.db").exists()


def test_kb_delete_unknown_raises_value_error(tmp_path):
    reg = open_registry(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        delete_kb(reg, "no-such-kb")
    reg.close()


# ---------------------------------------------------------------------------
# kb health (via delete_kb helper — full CLI test uses the runner)
# ---------------------------------------------------------------------------

def test_kb_health_outputs_all_groups(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_kb(tmp_path)

    result = runner.invoke(app, ["kb", "health", "test-kb"])
    # No unhandled Python exception (SystemExit from raise typer.Exit is expected)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Environment (Required)" in result.output
    assert "Optional Tools" in result.output
    assert "KB State" in result.output
    assert "KB Scaffold Files" in result.output
    assert "ExifTool present" in result.output


def test_kb_health_reports_missing_corpus(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    (kb_folder / "corpus.db").unlink()

    result = runner.invoke(app, ["kb", "health", "test-kb"])
    assert result.exception is None or isinstance(result.exception, SystemExit)
    # Sources/files checks show unavailable when DB missing
    assert "database unavailable" in result.output


# ---------------------------------------------------------------------------
# kb create --template general-media
# ---------------------------------------------------------------------------

def test_kb_create_template_general_media(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # Provide a minimal seed/general-media/stopwords.txt relative to cwd
    seed_dir = tmp_path / "seed" / "general-media"
    seed_dir.mkdir(parents=True)
    (seed_dir / "stopwords.txt").write_text("photo\nvideo\nclip\n", encoding="utf-8")
    (seed_dir / "pattern_rules.yaml").write_text('{"rules": []}', encoding="utf-8")

    result = runner.invoke(app, ["kb", "create", "gm-kb", "--template", "general-media"])
    assert result.exit_code == 0, result.output

    kb_path = tmp_path / "knowledge-bases" / "gm-kb" / "knowledge.db"
    kb_conn = open_kb(kb_path)
    terms = {
        r["term"] for r in kb_conn.execute(
            "SELECT term FROM stoplist WHERE source='seeded'"
        ).fetchall()
    }
    kb_conn.close()
    assert "photo" in terms
    assert "video" in terms


# ---------------------------------------------------------------------------
# kb create --import-kb
# ---------------------------------------------------------------------------

def _make_export_bundle(bundle_dir: Path) -> None:
    """Create a minimal export bundle directory."""
    bundle_dir.mkdir(parents=True)

    # vocabulary.csv
    with open(bundle_dir / "vocabulary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["term", "synonyms_json", "write_synonyms", "source"])
        writer.writeheader()
        writer.writerow({"term": "bridge", "synonyms_json": '["bridges"]', "write_synonyms": None, "source": "accepted"})
        writer.writerow({"term": "highway", "synonyms_json": "[]", "write_synonyms": None, "source": "accepted"})

    # stopwords.txt
    (bundle_dir / "stopwords.txt").write_text("photo\nvideo\n", encoding="utf-8")

    # corrections.csv — exact replace rules
    with open(bundle_dir / "corrections.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["raw", "canonical", "type"])
        writer.writeheader()
        writer.writerow({"raw": "Brdg", "canonical": "Bridge", "type": "correction"})

    # patterns.yaml — flat list format
    patterns = {
        "rules": [
            {"pattern": r"^(\d{8})$", "is_regex": True, "action": "capture",
             "label": "date_8", "extract_as": "file_date",
             "value_type": "date", "format_str": None, "keep_token": False}
        ],
        "substitute_rules": [
            {"pattern": r"Hwy\s*(\d+)", "replacement": r"Highway \1",
             "label": "highway_num", "applies_to": "both"}
        ],
    }
    with open(bundle_dir / "patterns.yaml", "w", encoding="utf-8") as fh:
        yaml.dump(patterns, fh)

    # reject_tokens.csv
    with open(bundle_dir / "reject_tokens.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["pattern", "is_regex", "label", "scope"])
        writer.writeheader()
        writer.writerow({"pattern": "untitled", "is_regex": 0, "label": "noise", "scope": "both"})

    # entities/ (minimal)
    (bundle_dir / "entities").mkdir()
    with open(bundle_dir / "entities" / "_registry.csv", "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=["table_name", "display_name", "trigger_word",
                                        "trigger_aliases", "key_column", "match_type",
                                        "description", "source_csv"]).writeheader()
    with open(bundle_dir / "entities" / "_links.csv", "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=["parent_table", "parent_column", "linked_table",
                                        "linked_key_column", "label", "include_in_text_pool"]).writeheader()


def test_kb_create_import_kb_vocabulary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "bundle"
    _make_export_bundle(bundle)

    result = runner.invoke(app, ["kb", "create", "imported-kb", "--import-kb", str(bundle)])
    assert result.exit_code == 0, result.output

    kb_path = tmp_path / "knowledge-bases" / "imported-kb" / "knowledge.db"
    kb_conn = open_kb(kb_path)
    terms = {r["term"] for r in kb_conn.execute("SELECT term FROM vocabulary").fetchall()}
    kb_conn.close()
    assert "bridge" in terms
    assert "highway" in terms


def test_kb_create_import_kb_stopwords(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "bundle"
    _make_export_bundle(bundle)

    runner.invoke(app, ["kb", "create", "sw-kb", "--import-kb", str(bundle)])

    kb_path = tmp_path / "knowledge-bases" / "sw-kb" / "knowledge.db"
    kb_conn = open_kb(kb_path)
    terms = {
        r["term"] for r in kb_conn.execute(
            "SELECT term FROM stoplist WHERE source='seeded'"
        ).fetchall()
    }
    kb_conn.close()
    assert "photo" in terms
    assert "video" in terms


def test_kb_create_import_kb_corrections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "bundle"
    _make_export_bundle(bundle)

    runner.invoke(app, ["kb", "create", "corr-kb", "--import-kb", str(bundle)])

    kb_path = tmp_path / "knowledge-bases" / "corr-kb" / "knowledge.db"
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute(
        "SELECT replace_with FROM pattern_rules WHERE pattern='Brdg' AND action='replace'"
    ).fetchone()
    kb_conn.close()
    assert row is not None
    assert row["replace_with"] == "Bridge"


def test_kb_create_import_kb_patterns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "bundle"
    _make_export_bundle(bundle)

    runner.invoke(app, ["kb", "create", "pat-kb", "--import-kb", str(bundle)])

    kb_path = tmp_path / "knowledge-bases" / "pat-kb" / "knowledge.db"
    kb_conn = open_kb(kb_path)
    capture_count = kb_conn.execute(
        "SELECT COUNT(*) FROM pattern_rules WHERE action='capture'"
    ).fetchone()[0]
    substitute_count = kb_conn.execute("SELECT COUNT(*) FROM substitute_rules").fetchone()[0]
    kb_conn.close()
    assert capture_count >= 1
    assert substitute_count >= 1


def test_kb_create_import_kb_entities(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "bundle"
    _make_export_bundle(bundle)

    # Add entity data to the bundle
    entities_dir = bundle / "entities"
    with open(entities_dir / "_registry.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "table_name", "display_name", "trigger_word", "trigger_aliases",
            "key_column", "match_type", "description", "source_csv",
        ])
        w.writeheader()
        w.writerow({
            "table_name": "highways", "display_name": "Highways",
            "trigger_word": "highway", "trigger_aliases": "[]",
            "key_column": "id", "match_type": "text",
            "description": "", "source_csv": "",
        })
    with open(entities_dir / "highways.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "name"])
        w.writeheader()
        w.writerow({"id": "H5", "name": "Coquihalla Highway"})
        w.writerow({"id": "H1", "name": "Trans-Canada Highway"})

    result = runner.invoke(app, ["kb", "create", "ent-kb", "--import-kb", str(bundle)])
    assert result.exit_code == 0, result.output
    assert "Entities: 1 table(s)" in result.output

    kb_path = tmp_path / "knowledge-bases" / "ent-kb" / "knowledge.db"
    kb_conn = open_kb(kb_path)
    reg_count = kb_conn.execute("SELECT COUNT(*) FROM entity_table_registry").fetchone()[0]
    row_count = kb_conn.execute('SELECT COUNT(*) FROM "entity_highways"').fetchone()[0]
    kb_conn.close()
    assert reg_count == 1
    assert row_count == 2
