"""Integration tests for Vocabulary Manager — KB.AD1."""
import json
import sys
import threading
import types


from src.db.corpus import open_corpus
from src.db.kb import (
    add_vocab_proposal,
    add_vocabulary_term,
    confirm_vocab_proposal,
    create_entity_table,
    dismiss_vocab_proposal,
    get_synonym_list,
    get_synonym_map,
    get_vocab_proposals,
    merge_vocabulary_terms,
    open_kb,
    register_entity_table,
    update_vocabulary_term,
    upsert_entity_row,
)
from src.pipeline.progress import NullProgressReporter
from src.stages.vocab import _entity_proposals, generate_proposals


# ---------------------------------------------------------------------------
# DB CRUD helpers
# ---------------------------------------------------------------------------

class TestVocabCrud:
    def test_add_update_delete(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "photograph", source="accepted")
        row = kb_conn.execute("SELECT * FROM vocabulary WHERE term='photograph'").fetchone()
        assert row is not None
        assert json.loads(row["synonyms_json"]) == []

        update_vocabulary_term(kb_conn, "photograph", json.dumps(["photo", "pic"]))
        row = kb_conn.execute("SELECT * FROM vocabulary WHERE term='photograph'").fetchone()
        assert "photo" in json.loads(row["synonyms_json"])
        assert "pic" in json.loads(row["synonyms_json"])

        from src.db.kb import delete_vocabulary_term
        delete_vocabulary_term(kb_conn, "photograph")
        assert kb_conn.execute(
            "SELECT id FROM vocabulary WHERE term='photograph'"
        ).fetchone() is None

    def test_update_write_synonyms_flag(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "beach", source="accepted")
        update_vocabulary_term(kb_conn, "beach", "[]", write_synonyms=1)
        row = kb_conn.execute("SELECT write_synonyms FROM vocabulary WHERE term='beach'").fetchone()
        assert row["write_synonyms"] == 1


class TestGetSynonymList:
    def test_returns_deserialized_list(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        kb_conn.execute(
            "INSERT INTO vocabulary (term, synonyms_json, source) VALUES (?, ?, 'accepted')",
            ("photograph", json.dumps(["photo", "photography"])),
        )
        kb_conn.commit()
        result = get_synonym_list(kb_conn, "photograph")
        assert result == ["photo", "photography"]

    def test_returns_empty_for_missing_term(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        assert get_synonym_list(kb_conn, "nonexistent") == []

    def test_returns_empty_for_no_synonyms(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "beach", source="accepted")
        assert get_synonym_list(kb_conn, "beach") == []


class TestGetSynonymMap:
    def test_builds_reverse_map(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        kb_conn.execute(
            "INSERT INTO vocabulary (term, synonyms_json, source) VALUES (?, ?, 'accepted')",
            ("photograph", json.dumps(["photo", "photography"])),
        )
        kb_conn.execute(
            "INSERT INTO vocabulary (term, synonyms_json, source) VALUES (?, ?, 'accepted')",
            ("beach", json.dumps(["shore"])),
        )
        kb_conn.commit()
        result = get_synonym_map(kb_conn)
        assert result["photo"] == "photograph"
        assert result["photography"] == "photograph"
        assert result["shore"] == "beach"

    def test_empty_when_no_synonyms(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "beach", source="accepted")
        assert get_synonym_map(kb_conn) == {}


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

class TestMergeVocabularyTerms:
    def test_merge_adds_synonym_and_replace_rule(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "beach", source="accepted")
        add_vocabulary_term(kb_conn, "shore", source="accepted")

        merge_vocabulary_terms(kb_conn, "beach", "shore")

        assert get_synonym_list(kb_conn, "beach") == ["shore"]
        assert kb_conn.execute(
            "SELECT id FROM vocabulary WHERE term='shore'"
        ).fetchone() is None

    def test_merge_replace_rule_fields(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "beach", source="accepted")
        add_vocabulary_term(kb_conn, "shore", source="accepted")

        merge_vocabulary_terms(kb_conn, "beach", "shore")

        rule = kb_conn.execute(
            "SELECT * FROM pattern_rules WHERE pattern='shore' AND action='replace'"
        ).fetchone()
        assert rule is not None
        assert rule["replace_with"] == "beach"
        assert rule["replace_type"] == "synonym"
        assert rule["is_regex"] == 0

    def test_merge_idempotent_replace_rule(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "beach", source="accepted")
        add_vocabulary_term(kb_conn, "shore", source="accepted")
        merge_vocabulary_terms(kb_conn, "beach", "shore")
        add_vocabulary_term(kb_conn, "shore", source="accepted")
        merge_vocabulary_terms(kb_conn, "beach", "shore")
        count = kb_conn.execute(
            "SELECT COUNT(*) FROM pattern_rules WHERE pattern='shore' AND action='replace' AND replace_with='beach'"
        ).fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Proposals — CRUD
# ---------------------------------------------------------------------------

class TestVocabProposals:
    def test_add_and_retrieve_proposal(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["photograph", "photo"], "nlp_lemma", "lemma: photograph")
        assert pid > 0
        proposals = get_vocab_proposals(kb_conn)
        assert len(proposals) == 1
        assert json.loads(proposals[0]["terms_json"]) == ["photo", "photograph"]
        assert proposals[0]["source"] == "nlp_lemma"

    def test_idempotent_same_term_set(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocab_proposal(kb_conn, ["photo", "photograph"], "nlp_lemma")
        result = add_vocab_proposal(kb_conn, ["photograph", "photo"], "nlp_lemma")
        assert result == 0
        assert len(get_vocab_proposals(kb_conn)) == 1

    def test_dismissed_not_readded(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["photo", "photograph"], "nlp_lemma")
        dismiss_vocab_proposal(kb_conn, pid)
        result = add_vocab_proposal(kb_conn, ["photograph", "photo"], "nlp_lemma")
        assert result == 0

    def test_dismiss_excludes_from_pending(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["photo", "photograph"], "nlp_lemma")
        dismiss_vocab_proposal(kb_conn, pid)
        assert get_vocab_proposals(kb_conn, status="pending") == []
        assert len(get_vocab_proposals(kb_conn, status="dismissed")) == 1


class TestConfirmProposal:
    def test_confirm_writes_synonyms(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "photograph", source="accepted")
        pid = add_vocab_proposal(kb_conn, ["photograph", "photo", "photography"], "nlp_lemma")
        confirm_vocab_proposal(kb_conn, pid, "photograph")
        syns = get_synonym_list(kb_conn, "photograph")
        assert "photo" in syns
        assert "photography" in syns

    def test_confirm_creates_replace_rules(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "photograph", source="accepted")
        pid = add_vocab_proposal(kb_conn, ["photograph", "photo"], "nlp_lemma")
        confirm_vocab_proposal(kb_conn, pid, "photograph")
        rule = kb_conn.execute(
            "SELECT * FROM pattern_rules WHERE pattern='photo' AND action='replace'"
        ).fetchone()
        assert rule is not None
        assert rule["replace_with"] == "photograph"
        assert rule["replace_type"] == "synonym"

    def test_confirm_adds_missing_canonical(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["beach", "shore"], "nlp_lemma")
        confirm_vocab_proposal(kb_conn, pid, "beach")
        row = kb_conn.execute("SELECT * FROM vocabulary WHERE term='beach'").fetchone()
        assert row is not None
        assert row["source"] == "user"

    def test_confirm_marks_proposal_confirmed(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["beach", "shore"], "nlp_lemma")
        confirm_vocab_proposal(kb_conn, pid, "beach")
        assert get_vocab_proposals(kb_conn, status="pending") == []
        confirmed = get_vocab_proposals(kb_conn, status="confirmed")
        assert len(confirmed) == 1
        assert confirmed[0]["canonical"] == "beach"

    def test_confirm_no_duplicate_replace_rule(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "photograph", source="accepted")
        pid1 = add_vocab_proposal(kb_conn, ["photograph", "photo"], "nlp_lemma")
        confirm_vocab_proposal(kb_conn, pid1, "photograph")
        # Add same rule again manually should be skipped
        pid2 = add_vocab_proposal(kb_conn, ["photograph", "photo", "pic"], "nlp_lemma")
        confirm_vocab_proposal(kb_conn, pid2, "photograph")
        count = kb_conn.execute(
            "SELECT COUNT(*) FROM pattern_rules WHERE pattern='photo' AND action='replace'"
        ).fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Entity proposals
# ---------------------------------------------------------------------------

def _seed_entity_table_with_aliases(kb_conn, table_name="locations", key_col="name"):
    columns = [key_col, "aliases", "region"]
    create_entity_table(kb_conn, table_name, columns, key_col)
    register_entity_table(
        kb_conn,
        table_name=table_name,
        display_name="Locations",
        trigger_word="location",
        trigger_aliases_json="[]",
        key_column=key_col,
        match_type="text",
    )
    upsert_entity_row(kb_conn, table_name, {
        key_col: "San Francisco", "aliases": "SF|Frisco", "region": "California"
    })
    upsert_entity_row(kb_conn, table_name, {
        key_col: "New York", "aliases": "NY|NYC", "region": "New York"
    })
    upsert_entity_row(kb_conn, table_name, {
        key_col: "London", "aliases": "", "region": "England"
    })
    return kb_conn


class TestEntityProposals:
    def test_entity_proposals_from_aliases_column(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        _seed_entity_table_with_aliases(kb_conn)
        proposals = _entity_proposals(kb_conn)
        assert len(proposals) == 2
        terms_sets = [frozenset(p["terms"]) for p in proposals]
        assert frozenset({"San Francisco", "SF", "Frisco"}) in terms_sets
        assert frozenset({"New York", "NY", "NYC"}) in terms_sets

    def test_entity_row_without_aliases_skipped(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        _seed_entity_table_with_aliases(kb_conn)
        proposals = _entity_proposals(kb_conn)
        term_lists = [p["terms"] for p in proposals]
        assert not any("London" in t for t in term_lists)

    def test_entity_proposals_source_label(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        _seed_entity_table_with_aliases(kb_conn)
        proposals = _entity_proposals(kb_conn)
        for p in proposals:
            assert p["source"] == "entity"
            assert "Locations" in p["source_detail"]
            assert "aliases" in p["source_detail"]

    def test_entity_proposals_multiple_alias_cols(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        columns = ["name", "aliases", "aka", "region"]
        create_entity_table(kb_conn, "places", columns, "name")
        register_entity_table(
            kb_conn,
            table_name="places",
            display_name="Places",
            trigger_word="place",
            trigger_aliases_json="[]",
            key_column="name",
            match_type="text",
        )
        upsert_entity_row(kb_conn, "places", {
            "name": "San Francisco", "aliases": "SF", "aka": "The City", "region": "CA"
        })
        proposals = _entity_proposals(kb_conn)
        assert len(proposals) == 1
        terms = frozenset(proposals[0]["terms"])
        assert "San Francisco" in terms
        assert "SF" in terms
        assert "The City" in terms

    def test_generate_entity_proposals_added_to_db(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        _seed_entity_table_with_aliases(kb_conn)
        count = generate_proposals(kb_conn)
        assert count == 2
        pending = get_vocab_proposals(kb_conn)
        assert len(pending) == 2

    def test_generate_proposals_idempotent(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        _seed_entity_table_with_aliases(kb_conn)
        generate_proposals(kb_conn)
        count2 = generate_proposals(kb_conn)
        assert count2 == 0
        assert len(get_vocab_proposals(kb_conn)) == 2

    def test_generate_skips_dismissed(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        _seed_entity_table_with_aliases(kb_conn)
        generate_proposals(kb_conn)
        pending = get_vocab_proposals(kb_conn)
        dismiss_vocab_proposal(kb_conn, pending[0]["id"])
        generate_proposals(kb_conn)
        assert len(get_vocab_proposals(kb_conn, status="pending")) == 1


# ---------------------------------------------------------------------------
# NLP proposals (stub spaCy)
# ---------------------------------------------------------------------------

def _make_fake_spacy_for_vocab(lemma_map: dict[str, str]):
    """Return a fake spacy module; nlp(term) returns a one-token doc with the given lemma."""
    class FakeToken:
        def __init__(self, text, lemma):
            self.text = text
            self.lemma_ = lemma

    class FakeDoc:
        def __init__(self, token):
            self._token = token

        def __len__(self):
            return 1

        def __getitem__(self, idx):
            return self._token

    class FakeNLP:
        def __call__(self, text):
            lemma = lemma_map.get(text, text)
            return FakeDoc(FakeToken(text, lemma))

    fake_spacy = types.ModuleType("spacy")
    fake_spacy.load = lambda model, **kwargs: FakeNLP()
    return fake_spacy


class TestNlpProposals:
    def test_nlp_groups_morphological_variants(self, tmp_path, monkeypatch):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "photograph", source="accepted")
        add_vocabulary_term(kb_conn, "photographs", source="accepted")
        add_vocabulary_term(kb_conn, "beach", source="accepted")

        fake = _make_fake_spacy_for_vocab({
            "photograph": "photograph",
            "photographs": "photograph",
            "beach": "beach",
        })
        monkeypatch.setitem(sys.modules, "spacy", fake)

        from src.stages.vocab import _nlp_proposals
        proposals = _nlp_proposals(kb_conn)
        assert len(proposals) == 1
        terms = frozenset(proposals[0]["terms"])
        assert terms == {"photograph", "photographs"}
        assert proposals[0]["source"] == "nlp_lemma"
        assert "photograph" in proposals[0]["source_detail"]

    def test_nlp_no_proposal_for_single_form(self, tmp_path, monkeypatch):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "beach", source="accepted")

        fake = _make_fake_spacy_for_vocab({"beach": "beach"})
        monkeypatch.setitem(sys.modules, "spacy", fake)

        from src.stages.vocab import _nlp_proposals
        proposals = _nlp_proposals(kb_conn)
        assert proposals == []

    def test_generate_nlp_proposals_written_to_db(self, tmp_path, monkeypatch):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "photograph", source="accepted")
        add_vocabulary_term(kb_conn, "photographs", source="accepted")

        fake = _make_fake_spacy_for_vocab({
            "photograph": "photograph",
            "photographs": "photograph",
        })
        monkeypatch.setitem(sys.modules, "spacy", fake)
        count = generate_proposals(kb_conn)
        assert count == 1
        pending = get_vocab_proposals(kb_conn)
        assert len(pending) == 1


# ---------------------------------------------------------------------------
# Suggest Level A synonym substitution
# ---------------------------------------------------------------------------

def _seed_suggest_files(conn, count=3):
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    for i in range(count):
        conn.execute(
            "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (1, '/f{i}.jpg', 'f{i}.jpg', '.jpg', 'image', 1, 0.0)"
        )
    conn.commit()


def _seed_keywords(conn, file_id, *keywords):
    for kw in keywords:
        conn.execute(
            "INSERT INTO file_metadata_keywords (file_id, canonical_name, keyword)"
            " VALUES (?, 'keywords', ?)",
            (file_id, kw),
        )
    conn.commit()


def _make_fake_spacy(terms_per_doc):
    call_count = [0]

    class FakeToken:
        def __init__(self, lemma, idx=0):
            self.lemma_ = lemma
            self.pos_ = "NOUN"
            self.is_stop = False
            self.i = idx

    class FakeDoc:
        def __init__(self, terms):
            self.tokens = [FakeToken(t, idx) for idx, t in enumerate(terms)]
            self.noun_chunks = []

        def __iter__(self):
            return iter(self.tokens)

    class FakeNLP:
        def __call__(self, text):
            idx = call_count[0] % len(terms_per_doc)
            call_count[0] += 1
            return FakeDoc(terms_per_doc[idx])

    fake_spacy = types.ModuleType("spacy")
    fake_spacy.load = lambda model, **kwargs: FakeNLP()
    return fake_spacy


def test_level_a_substitutes_synonym_with_canonical(tmp_path, monkeypatch):
    from src.config import Config
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_suggest_files(corpus_conn, 3)
    _seed_keywords(corpus_conn, 1, "shore")
    _seed_keywords(corpus_conn, 2, "shore")
    _seed_keywords(corpus_conn, 3, "shore")
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    add_vocabulary_term(kb_conn, "beach", source="accepted")
    # "shore" is a synonym of "beach"
    kb_conn.execute(
        "UPDATE vocabulary SET synonyms_json=? WHERE term='beach'",
        (json.dumps(["shore"]),),
    )
    kb_conn.commit()
    kb_conn.close()

    # Fake spaCy returns "shore" as the lemma each time
    fake = _make_fake_spacy([["shore"], ["shore"], ["shore"]])
    monkeypatch.setitem(sys.modules, "spacy", fake)

    from src.stages.suggest import run_suggest
    config = Config(suggest_min_files=2)
    run_suggest(corpus_path, kb_path, config, NullProgressReporter(), threading.Event(), levels=["a"])

    corpus_conn = open_corpus(corpus_path)
    terms = {r["term"] for r in corpus_conn.execute(
        "SELECT term FROM candidates WHERE source='level_a'"
    ).fetchall()}
    corpus_conn.close()

    # "shore" should be substituted with "beach", but "beach" is already in vocabulary
    # so it should be excluded. Result: no candidates (shore→beach, beach excluded)
    assert "shore" not in terms


# ---------------------------------------------------------------------------
# Thematic confirm branch
# ---------------------------------------------------------------------------

class TestConfirmThematicProposal:
    def test_thematic_writes_replace_rule_with_thematic_type(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        add_vocabulary_term(kb_conn, "Bear", source="accepted")
        add_vocabulary_term(kb_conn, "Eagle", source="accepted")
        pid = add_vocab_proposal(kb_conn, ["Wildlife", "Bear", "Eagle"], "llm_thematic",
                                 "canonical:Wildlife | thematic grouping")
        confirm_vocab_proposal(kb_conn, pid, "Wildlife")
        rule = kb_conn.execute(
            "SELECT * FROM pattern_rules WHERE pattern='Bear' AND action='replace' AND replace_with='Wildlife'"
        ).fetchone()
        assert rule is not None
        assert rule["replace_type"] == "thematic"

    def test_thematic_does_not_add_to_synonyms_json(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["Wildlife", "Bear", "Eagle"], "llm_thematic",
                                 "canonical:Wildlife | thematic grouping")
        confirm_vocab_proposal(kb_conn, pid, "Wildlife")
        wildlife_row = kb_conn.execute(
            "SELECT synonyms_json FROM vocabulary WHERE term='Wildlife'"
        ).fetchone()
        assert wildlife_row is not None
        assert json.loads(wildlife_row["synonyms_json"]) == []

    def test_thematic_adds_umbrella_to_vocabulary(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["Wildlife", "Bear", "Wolf"], "llm_thematic",
                                 "canonical:Wildlife | thematic grouping")
        confirm_vocab_proposal(kb_conn, pid, "Wildlife")
        row = kb_conn.execute("SELECT * FROM vocabulary WHERE term='Wildlife'").fetchone()
        assert row is not None
        assert row["source"] == "user"

    def test_thematic_replace_rules_for_all_non_canonical_terms(self, tmp_path):
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = add_vocab_proposal(kb_conn, ["Wildlife", "Bear", "Eagle", "Wolf"], "llm_thematic",
                                 "canonical:Wildlife | thematic grouping")
        confirm_vocab_proposal(kb_conn, pid, "Wildlife")
        for term in ("Bear", "Eagle", "Wolf"):
            rule = kb_conn.execute(
                "SELECT id FROM pattern_rules WHERE pattern=? AND replace_with='Wildlife' AND replace_type='thematic'",
                (term,),
            ).fetchone()
            assert rule is not None, f"Missing replace rule for {term}"


class TestTaxonomyProposals:
    def test_save_and_retrieve_pending(self, tmp_path):
        import json
        from src.db.kb import get_pending_taxonomy_proposal, save_taxonomy_proposal
        kb_conn = open_kb(tmp_path / "knowledge.db")
        tree = [{"name": "Nature", "children": [{"name": "Bear"}]}]
        save_taxonomy_proposal(kb_conn, json.dumps(tree))
        row = get_pending_taxonomy_proposal(kb_conn)
        assert row is not None
        assert json.loads(row["tree_json"]) == tree
        assert row["status"] == "pending"

    def test_save_replaces_existing_pending(self, tmp_path):
        import json
        from src.db.kb import get_pending_taxonomy_proposal, save_taxonomy_proposal
        kb_conn = open_kb(tmp_path / "knowledge.db")
        save_taxonomy_proposal(kb_conn, json.dumps([{"name": "Old"}]))
        save_taxonomy_proposal(kb_conn, json.dumps([{"name": "New"}]))
        row = get_pending_taxonomy_proposal(kb_conn)
        assert row is not None
        assert json.loads(row["tree_json"]) == [{"name": "New"}]
        dismissed = kb_conn.execute(
            "SELECT COUNT(*) FROM taxonomy_proposals WHERE status='dismissed'"
        ).fetchone()[0]
        assert dismissed == 1

    def test_dismiss_taxonomy_proposal(self, tmp_path):
        import json
        from src.db.kb import dismiss_taxonomy_proposal, get_pending_taxonomy_proposal, save_taxonomy_proposal
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = save_taxonomy_proposal(kb_conn, json.dumps([{"name": "Nature"}]))
        dismiss_taxonomy_proposal(kb_conn, pid)
        assert get_pending_taxonomy_proposal(kb_conn) is None

    def test_apply_taxonomy_writes_yaml(self, tmp_path):
        import json
        import yaml
        from src.db.kb import apply_taxonomy_proposal, save_taxonomy_proposal
        kb_ref = tmp_path / "reference"
        kb_ref.mkdir()
        kb_folder = tmp_path
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = save_taxonomy_proposal(kb_conn, json.dumps([{"name": "Nature", "children": [{"name": "Bear"}]}]))
        accepted = {"Nature": {"Wildlife": ["Bear", "Eagle"]}}
        apply_taxonomy_proposal(kb_conn, pid, accepted, kb_folder)
        taxonomy_path = kb_folder / "reference" / "taxonomy.yaml"
        assert taxonomy_path.exists()
        data = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
        assert "Topics" in data
        assert "Nature" in data["Topics"]
        row = kb_conn.execute("SELECT status FROM taxonomy_proposals WHERE id=?", (pid,)).fetchone()
        assert row["status"] == "applied"

    def test_apply_taxonomy_merges_existing_yaml(self, tmp_path):
        import json
        import yaml
        from src.db.kb import apply_taxonomy_proposal, save_taxonomy_proposal
        kb_ref = tmp_path / "reference"
        kb_ref.mkdir()
        kb_folder = tmp_path
        taxonomy_path = kb_folder / "reference" / "taxonomy.yaml"
        taxonomy_path.write_text(
            yaml.dump({"Keywords": ["Bear", "Eagle"]}, allow_unicode=True),
            encoding="utf-8",
        )
        kb_conn = open_kb(tmp_path / "knowledge.db")
        pid = save_taxonomy_proposal(kb_conn, json.dumps([]))
        apply_taxonomy_proposal(kb_conn, pid, {"Nature": ["Bear"]}, kb_folder)
        data = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
        assert "Keywords" in data
        assert "Topics" in data
