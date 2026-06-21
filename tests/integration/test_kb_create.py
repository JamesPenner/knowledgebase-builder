"""Integration tests for kb create reference file population."""
from pathlib import Path

import yaml


def _call_populate(kb_folder: Path) -> None:
    from src.cli.kb import _populate_reference_files
    _populate_reference_files(kb_folder)


_REFERENCE_FILES = [
    "reference/dates.yaml",
    "reference/derive_rules.yaml",
    "reference/taxonomy.yaml",
    "reference/stopwords.txt",
    "seed/vocabulary.csv",
    "adapters/acdsee/mapping.yaml",
    "adapters/acdsee/ACDSeeCategoriesTemplate.arg",
]


def test_populate_creates_all_reference_files(tmp_path):
    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    for rel in _REFERENCE_FILES:
        assert (kb_folder / rel).exists(), f"Missing: {rel}"


def test_dates_yaml_is_valid_yaml(tmp_path):
    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    data = yaml.safe_load((kb_folder / "reference/dates.yaml").read_text(encoding="utf-8"))
    assert "calendar" in data
    assert isinstance(data["calendar"], list)
    assert len(data["calendar"]) >= 1


def test_derive_rules_yaml_is_valid_yaml(tmp_path):
    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    data = yaml.safe_load((kb_folder / "reference/derive_rules.yaml").read_text(encoding="utf-8"))
    assert "field_rules" in data


def test_taxonomy_yaml_is_valid_yaml(tmp_path):
    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    data = yaml.safe_load((kb_folder / "reference/taxonomy.yaml").read_text(encoding="utf-8"))
    assert "categories" in data


def test_stopwords_txt_has_content(tmp_path):
    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    lines = (kb_folder / "reference/stopwords.txt").read_text(encoding="utf-8").splitlines()
    words = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
    assert len(words) >= 5


def test_vocabulary_csv_has_header(tmp_path):
    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    import csv
    with open(kb_folder / "seed/vocabulary.csv", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert "domain" in (reader.fieldnames or [])
        rows = list(reader)
    assert len(rows) >= 1


def test_acdsee_template_is_nonempty(tmp_path):
    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    content = (kb_folder / "adapters/acdsee/ACDSeeCategoriesTemplate.arg").read_text(encoding="utf-8")
    assert len(content) > 10


def test_populate_copies_from_catalogue_template(tmp_path, monkeypatch):
    """If a catalogue template exists, files should be copied from it."""
    # Create a fake catalogue KB with a known dates.yaml
    fake_template = tmp_path / "catalogue_kb"
    fake_template.mkdir()
    (fake_template / "reference").mkdir()
    (fake_template / "seed").mkdir()
    (fake_template / "adapters" / "acdsee").mkdir(parents=True)

    sentinel_content = "enabled: true\ncalendar:\n- name: TestSentinel\n  type: fixed\n  month: 1\n  day: 1\n  algorithm: null\n  enabled: true\n"
    (fake_template / "reference" / "dates.yaml").write_text(sentinel_content, encoding="utf-8")

    # Patch _find_catalogue_template to return the fake template
    import src.cli.kb as kb_module
    monkeypatch.setattr(kb_module, "_find_catalogue_template", lambda: fake_template)

    kb_folder = tmp_path / "kb"
    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "seed").mkdir()

    _call_populate(kb_folder)

    # dates.yaml should be the sentinel copy
    actual = (kb_folder / "reference/dates.yaml").read_text(encoding="utf-8")
    assert "TestSentinel" in actual
