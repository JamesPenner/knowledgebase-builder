"""Integration tests for enrich kb generate-taxonomy command."""
import yaml
from typer.testing import CliRunner

from src.cli import app
from src.db.corpus import open_corpus
from src.db.kb import open_kb
from src.db.registry import open_registry, register_kb

runner = CliRunner()


def _make_kb(tmp_path, name: str = "test-kb"):
    """Create a minimal KB folder registered in tmp_path/registry.db."""
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
# generate-taxonomy
# ---------------------------------------------------------------------------

def test_generate_taxonomy_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)
    taxonomy_path = kb_folder / "reference" / "taxonomy.yaml"
    assert not taxonomy_path.exists()

    result = runner.invoke(app, ["kb", "generate-taxonomy", "test-kb"])
    assert result.exit_code == 0, result.output
    assert taxonomy_path.exists()


def test_generate_taxonomy_contains_tags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)

    result = runner.invoke(app, ["kb", "generate-taxonomy", "test-kb"])
    assert result.exit_code == 0, result.output

    data = yaml.safe_load((kb_folder / "reference" / "taxonomy.yaml").read_text())
    assert "Tags" in data
    assert "Calendar" in data["Tags"]
    assert "Christmas Day" in data["Tags"]["Calendar"]
    assert "Temporal" in data["Tags"]
    assert "morning" in data["Tags"]["Temporal"]


def test_generate_taxonomy_includes_vocabulary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)

    from src.db.kb import add_vocabulary_term
    kb = open_kb(kb_folder / "knowledge.db")
    add_vocabulary_term(kb, "bridge", "[]", source="accepted")
    add_vocabulary_term(kb, "highway", "[]", source="user")
    kb.commit()
    kb.close()

    result = runner.invoke(app, ["kb", "generate-taxonomy", "test-kb"])
    assert result.exit_code == 0, result.output

    data = yaml.safe_load((kb_folder / "reference" / "taxonomy.yaml").read_text())
    assert "Keywords" in data
    assert "bridge" in data["Keywords"]
    assert "highway" in data["Keywords"]


def test_generate_taxonomy_includes_people(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)

    from src.db.kb import upsert_person
    kb = open_kb(kb_folder / "knowledge.db")
    upsert_person(kb, "James Penner")
    kb.close()

    result = runner.invoke(app, ["kb", "generate-taxonomy", "test-kb"])
    assert result.exit_code == 0, result.output

    data = yaml.safe_load((kb_folder / "reference" / "taxonomy.yaml").read_text())
    assert "People" in data
    assert "James Penner" in data["People"]


def test_generate_taxonomy_merges_with_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)

    taxonomy_path = kb_folder / "reference" / "taxonomy.yaml"
    existing = {
        "UserSection": ["user_custom_term"],
        "Tags": {"Calendar": ["UserHoliday"]},
    }
    taxonomy_path.write_text(yaml.dump(existing, allow_unicode=True), encoding="utf-8")

    result = runner.invoke(app, ["kb", "generate-taxonomy", "test-kb"])
    assert result.exit_code == 0, result.output

    data = yaml.safe_load(taxonomy_path.read_text())
    assert "UserSection" in data
    assert "user_custom_term" in data["UserSection"]
    assert "UserHoliday" in data["Tags"]["Calendar"]
    assert "Christmas Day" in data["Tags"]["Calendar"]
    assert data["Tags"]["Calendar"].count("UserHoliday") == 1


def test_generate_taxonomy_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kb_folder = _make_kb(tmp_path)

    result1 = runner.invoke(app, ["kb", "generate-taxonomy", "test-kb"])
    assert result1.exit_code == 0, result1.output
    data_first = yaml.safe_load((kb_folder / "reference" / "taxonomy.yaml").read_text())

    result2 = runner.invoke(app, ["kb", "generate-taxonomy", "test-kb"])
    assert result2.exit_code == 0, result2.output
    data_second = yaml.safe_load((kb_folder / "reference" / "taxonomy.yaml").read_text())

    assert data_first == data_second


def test_generate_taxonomy_unknown_kb_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    open_registry(tmp_path).close()  # create empty registry

    result = runner.invoke(app, ["kb", "generate-taxonomy", "no-such-kb"])
    assert result.exit_code != 0
