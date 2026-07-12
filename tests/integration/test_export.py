"""Integration tests for Stage 7 (Export)."""
import csv
import json
import threading

import pytest
import yaml

from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import (
    add_pattern_rule,
    add_to_stoplist,
    add_token_rejection,
    add_vocabulary_term,
    bump_kb_version,
    create_entity_table,
    open_kb,
    register_entity_table,
    upsert_entity_row,
)
from src.pipeline.progress import NullProgressReporter
from src.stages.export import run_export


def _run(corpus_path, kb_path, section=None):
    run_export(
        corpus_path,
        kb_path,
        Config(),
        NullProgressReporter(),
        threading.Event(),
        section=section,
    )


def _seed_kb(kb_conn, corpus_conn):
    """Populate knowledge.db and corpus.db with representative test data."""
    # Vocabulary
    add_vocabulary_term(kb_conn, "bridge", '["bridges"]', source="accepted")
    add_vocabulary_term(kb_conn, "highway", "[]", source="new_terms")

    # Stopwords — domain (should appear) and builtin (should not appear)
    add_to_stoplist(kb_conn, "temp_noise", source="domain")
    # builtin stopwords like "the" already seeded by open_kb

    # Replace rule (correction)
    add_pattern_rule(kb_conn, pattern="Brdg", action="replace", is_regex=False,
                     replace_with="Bridge", replace_type="correction")

    # Capture rule
    add_pattern_rule(kb_conn, pattern=r"^(\d{8})$", action="capture", is_regex=True,
                     label="date_8", extract_as="file_date", format_str="20{1}", value_type="date")

    # Reject token
    add_token_rejection(kb_conn, "untitled")

    # Entity table
    register_entity_table(kb_conn, "bridge", "Bridges", "bridge", "[]", "name", "text")
    create_entity_table(kb_conn, "bridge", ["name", "type"], "name")
    upsert_entity_row(kb_conn, "bridge", {"name": "Lions Gate", "type": "suspension"})

    bump_kb_version(kb_conn, "test_seed")
    kb_conn.commit()

    # Files + descriptions + retag_output in corpus
    corpus_conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img/a.jpg', 'a.jpg', '.jpg', 'image', 1, 0.0)"
    )
    corpus_conn.execute(
        "INSERT INTO descriptions (file_id, description_raw, description_normalized, model, processed_at)"
        " VALUES (1, 'A bridge.', 'A bridge.', 'test-model', datetime('now'))"
    )
    corpus_conn.execute(
        "INSERT INTO retag_output"
        " (file_id, tags_json, refined_description, new_terms_proposed_json, model, processed_at, retag_status)"
        " VALUES (1, ?, 'A steel bridge.', '[]', 'test-model', datetime('now'), 'done')",
        (json.dumps(["bridge"]),),
    )
    corpus_conn.commit()


# ---------------------------------------------------------------------------
# Full export tests
# ---------------------------------------------------------------------------

def test_export_creates_export_folder(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    assert (tmp_path / "export").is_dir()


def test_export_vocabulary_csv_content(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    vocab_file = tmp_path / "export" / "vocabulary.csv"
    assert vocab_file.exists()
    with open(vocab_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    terms = {r["term"] for r in rows}
    assert "bridge" in terms
    assert "highway" in terms
    assert all(r["source"] for r in rows)


def test_export_stopwords_excludes_builtin(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    sw_file = tmp_path / "export" / "stopwords.txt"
    assert sw_file.exists()
    terms = [ln.strip() for ln in sw_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert "temp_noise" in terms
    assert "the" not in terms   # builtin — must not appear
    assert "a" not in terms     # builtin


def test_export_corrections_csv_exact_only(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    # Add a regex replace rule — must NOT appear in corrections.csv (only exact rules do)
    add_pattern_rule(kb_conn, pattern=r"^hwy\d+$", action="replace", is_regex=True,
                     replace_with="Highway", replace_type="correction")
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    corr_file = tmp_path / "export" / "corrections.csv"
    assert corr_file.exists()
    with open(corr_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    raws = {r["raw"] for r in rows}
    assert "Brdg" in raws
    assert not any(r["raw"] == r"^hwy\d+$" for r in rows)  # regex rules excluded


def test_export_patterns_yaml_flat_list(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    patterns_file = tmp_path / "export" / "patterns.yaml"
    assert patterns_file.exists()
    data = yaml.safe_load(patterns_file.read_text(encoding="utf-8"))
    assert "rules" in data
    assert "substitute_rules" in data
    assert isinstance(data["rules"], list)
    # The regex capture rule seeded in _seed_kb should appear
    actions = {r["action"] for r in data["rules"]}
    assert "capture" in actions


def test_export_reject_tokens_csv(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    rt_file = tmp_path / "export" / "reject_tokens.csv"
    assert rt_file.exists()
    with open(rt_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    tokens = {r["token"] for r in rows}
    assert "untitled" in tokens


def test_export_field_map_csv_copied(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    # Create a reference/field_map.csv
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir()
    (ref_dir / "field_map.csv").write_text(
        "field_name,canonical_name,priority,enrichment_text,write_back,value_type,notes\n"
        "XMP-dc:Subject,keywords,1,true,true,keyword_list,\n",
        encoding="utf-8",
    )

    _run(corpus_path, kb_path)

    fm_file = tmp_path / "export" / "field_map.csv"
    assert fm_file.exists()
    assert "keywords" in fm_file.read_text(encoding="utf-8")


def test_export_entities_folder(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    entities_dir = tmp_path / "export" / "entities"
    assert entities_dir.is_dir()
    assert (entities_dir / "_registry.csv").exists()
    assert (entities_dir / "_links.csv").exists()
    assert (entities_dir / "bridge.csv").exists()

    with open(entities_dir / "bridge.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert any(r.get("name") == "Lions Gate" for r in rows)


def test_export_descriptions_csv(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    desc_file = tmp_path / "export" / "descriptions.csv"
    assert desc_file.exists()
    with open(desc_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["file_path"] == "/img/a.jpg"
    assert "bridge" in rows[0]["description"].lower()


def test_export_tags_csv(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    tags_file = tmp_path / "export" / "tags.csv"
    assert tags_file.exists()
    with open(tags_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["file_path"] == "/img/a.jpg"
    assert "bridge" in rows[0]["tags"]


def test_export_idempotent(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)
    first = (tmp_path / "export" / "vocabulary.csv").read_text(encoding="utf-8")

    _run(corpus_path, kb_path)
    second = (tmp_path / "export" / "vocabulary.csv").read_text(encoding="utf-8")

    assert first == second


def test_export_records_checkpoint(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    corpus_conn = open_corpus(corpus_path)
    row = corpus_conn.execute(
        "SELECT stage FROM pipeline_checkpoints WHERE stage='export'"
    ).fetchone()
    corpus_conn.close()
    assert row is not None


# ---------------------------------------------------------------------------
# Section-specific export tests
# ---------------------------------------------------------------------------

def test_export_section_vocabulary_only(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path, section="vocabulary")

    assert (tmp_path / "export" / "vocabulary.csv").exists()
    assert (tmp_path / "export" / "stopwords.txt").exists()
    assert not (tmp_path / "export" / "corrections.csv").exists()
    assert not (tmp_path / "export" / "patterns.yaml").exists()
    assert not (tmp_path / "export" / "descriptions.csv").exists()
    assert not (tmp_path / "export" / "tags.csv").exists()


def test_export_empty_kb_succeeds(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    open_corpus(corpus_path).close()
    open_kb(kb_path).close()

    _run(corpus_path, kb_path)

    assert (tmp_path / "export" / "vocabulary.csv").exists()
    # vocabulary.csv should have just a header row
    with open(tmp_path / "export" / "vocabulary.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows == []


# ---------------------------------------------------------------------------
# New export files: hashes, aesthetic_scores, search_text
# ---------------------------------------------------------------------------

def _seed_with_hashes(corpus_conn, kb_conn):
    """Extend _seed_kb data with hash rows and derived tags."""
    _seed_kb(kb_conn, corpus_conn)
    # image hash (file_id=1 set up by _seed_kb)
    corpus_conn.execute(
        """
        INSERT INTO file_hashes
            (file_id, sha256_content, phash, dhash, hashed_at)
        VALUES (1, 'sha_content_hex', 'phash_hex', 'dhash_hex', datetime('now'))
        """
    )
    # derived tag for search_text
    corpus_conn.execute(
        "INSERT INTO file_derived_tags (file_id, tag, category, source, rule_id)"
        " VALUES (1, 'Summer', 'calendar', 'classify', 1)"
    )
    corpus_conn.commit()


def test_export_hashes_csv_present_with_all_columns(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_with_hashes(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    hashes_file = tmp_path / "export" / "hashes.csv"
    assert hashes_file.exists()
    with open(hashes_file, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        expected_cols = {
            "path", "sha256", "sha256_content", "phash", "dhash",
            "video_collage_phash", "video_frame_phashes",
        }
        assert expected_cols.issubset(set(reader.fieldnames))
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["phash"] == "phash_hex"


def test_export_aesthetic_scores_csv_absent_when_no_scores(tmp_path):
    """aesthetic_scores.csv should not be written when there are no scores."""
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    assert not (tmp_path / "export" / "aesthetic_scores.csv").exists()


def test_export_aesthetic_scores_csv_present_when_scores_exist(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_kb(kb_conn, corpus_conn)
    corpus_conn.execute(
        "INSERT INTO file_aesthetic (file_id, model_name, score, band, scored_at)"
        " VALUES (1, 'nima_mobilenet', 6.5, 'good', datetime('now'))"
    )
    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    scores_file = tmp_path / "export" / "aesthetic_scores.csv"
    assert scores_file.exists()
    with open(scores_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert float(rows[0]["nima_score"]) == pytest.approx(6.5)
    assert rows[0]["nima_band"] == "good"


def test_export_search_text_csv_present(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_with_hashes(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    st_file = tmp_path / "export" / "search_text.csv"
    assert st_file.exists()
    with open(st_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    search_text = rows[0]["search_text"]
    assert "a.jpg" in search_text        # filename
    assert "Summer" in search_text       # derived tag
    assert "bridge" in search_text.lower()  # description


def test_export_search_text_csv_excludes_calendar_tag_when_dates_disabled(tmp_path):
    from src.db.kb import set_knowledge_category_enabled

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_with_hashes(corpus_conn, kb_conn)
    set_knowledge_category_enabled(kb_conn, "dates", False)
    kb_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    st_file = tmp_path / "export" / "search_text.csv"
    with open(st_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    search_text = rows[0]["search_text"]
    assert "Summer" not in search_text
    assert "bridge" in search_text.lower()


def test_export_search_text_csv_excludes_entity_tables_by_domain(tmp_path):
    from src.db.kb import set_knowledge_category_enabled

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_with_hashes(corpus_conn, kb_conn)
    corpus_conn.execute(
        "INSERT INTO file_entity_matches"
        " (file_id, table_name, matched_value, match_source, payload_json, stale)"
        " VALUES (1, 'people', 'Alice Smith', 'text', '{}', 0)"
    )
    corpus_conn.execute(
        "INSERT INTO file_entity_matches"
        " (file_id, table_name, matched_value, match_source, payload_json, stale)"
        " VALUES (1, 'locations', 'Vancouver', 'text', '{}', 0)"
    )
    corpus_conn.commit()
    set_knowledge_category_enabled(kb_conn, "people", False)
    kb_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    _run(corpus_path, kb_path)

    st_file = tmp_path / "export" / "search_text.csv"
    with open(st_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    search_text = rows[0]["search_text"]
    assert "Alice Smith" not in search_text
    assert "Vancouver" in search_text


# ---------------------------------------------------------------------------
# Export scope via CorpusFilterSpec
# ---------------------------------------------------------------------------

from src.db.corpus import add_source, upsert_file  # noqa: E402
from src.pipeline.filter_spec import CorpusFilterSpec  # noqa: E402


def _run_scoped(corpus_path, kb_path, scope):
    run_export(
        corpus_path,
        kb_path,
        Config(),
        NullProgressReporter(),
        threading.Event(),
        scope=scope,
    )


def _seed_two_sources(corpus_conn, kb_conn):
    src1 = add_source(corpus_conn, "/photos/2023", "images", True)
    src2 = add_source(corpus_conn, "/photos/2024", "images", True)
    fid1 = upsert_file(corpus_conn, src1, "/photos/2023/a.jpg", "a.jpg", ".jpg", "images", 1000, 0.0)
    fid2 = upsert_file(corpus_conn, src2, "/photos/2024/b.jpg", "b.jpg", ".jpg", "images", 1001, 0.0)
    for fid, desc in [(fid1, "A castle."), (fid2, "A beach.")]:
        corpus_conn.execute(
            "INSERT INTO descriptions (file_id, description_raw, model, pass1_status)"
            " VALUES (?, ?, 'dummy', 'done')",
            (fid, desc),
        )
    corpus_conn.commit()
    return src1, src2, fid1, fid2


def test_export_scope_by_source_filters_descriptions(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    src1, _src2, _fid1, _fid2 = _seed_two_sources(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()

    _run_scoped(corpus_path, kb_path, CorpusFilterSpec(source_id=src1))

    desc_file = tmp_path / "export" / "descriptions.csv"
    assert desc_file.exists()
    with open(desc_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert "castle" in rows[0]["description"]


def test_export_scope_by_file_type_filters_descriptions(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    src = add_source(corpus_conn, "/media", "all", True)
    fid_img = upsert_file(corpus_conn, src, "/media/img.jpg", "img.jpg", ".jpg", "images", 1000, 0.0)
    fid_vid = upsert_file(corpus_conn, src, "/media/vid.mp4", "vid.mp4", ".mp4", "video", 1001, 0.0)
    for fid, desc in [(fid_img, "A photo."), (fid_vid, "A video.")]:
        corpus_conn.execute(
            "INSERT INTO descriptions (file_id, description_raw, model, pass1_status)"
            " VALUES (?, ?, 'dummy', 'done')",
            (fid, desc),
        )
    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    _run_scoped(corpus_path, kb_path, CorpusFilterSpec(file_type="images"))

    desc_file = tmp_path / "export" / "descriptions.csv"
    assert desc_file.exists()
    with open(desc_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert "photo" in rows[0]["description"]


def test_export_scope_empty_spec_exports_all(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_two_sources(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()

    _run_scoped(corpus_path, kb_path, CorpusFilterSpec())

    desc_file = tmp_path / "export" / "descriptions.csv"
    assert desc_file.exists()
    with open(desc_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2


def test_export_scope_by_folder_prefix(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    src = add_source(corpus_conn, "/photos", "images", True)
    fid1 = upsert_file(corpus_conn, src, "/photos/Italy/a.jpg", "a.jpg", ".jpg", "images", 1000, 0.0)
    fid2 = upsert_file(corpus_conn, src, "/photos/France/b.jpg", "b.jpg", ".jpg", "images", 1001, 0.0)
    for fid, desc in [(fid1, "Rome."), (fid2, "Paris.")]:
        corpus_conn.execute(
            "INSERT INTO descriptions (file_id, description_raw, model, pass1_status)"
            " VALUES (?, ?, 'dummy', 'done')",
            (fid, desc),
        )
    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()

    _run_scoped(corpus_path, kb_path, CorpusFilterSpec(folder_prefix="/photos/Italy"))

    desc_file = tmp_path / "export" / "descriptions.csv"
    assert desc_file.exists()
    with open(desc_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert "Rome" in rows[0]["description"]


def test_export_scope_none_identical_to_empty_spec(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    _seed_two_sources(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()

    # scope=None should behave the same as an empty CorpusFilterSpec
    run_export(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    desc_file = tmp_path / "export" / "descriptions.csv"
    assert desc_file.exists()
    with open(desc_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
