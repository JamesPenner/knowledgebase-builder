"""Unit tests for vocabulary proposal generation — pure functions only."""


from src.stages.vocab import _detect_alias_columns, _suggest_canonical
from src.stages.vocab_llm import _paths_to_tree


class TestDetectAliasColumns:
    def test_aliases_detected(self):
        assert _detect_alias_columns(["name", "aliases", "type"]) == ["aliases"]

    def test_alt_name_detected(self):
        assert _detect_alias_columns(["location", "alt_name", "region"]) == ["alt_name"]

    def test_no_alias_columns(self):
        assert _detect_alias_columns(["name", "type", "region"]) == []

    def test_multiple_alias_columns(self):
        result = _detect_alias_columns(["aliases", "aka", "id", "name"])
        assert "aliases" in result
        assert "aka" in result
        assert len(result) == 2

    def test_case_insensitive(self):
        assert _detect_alias_columns(["Aliases", "AKA"]) == ["Aliases", "AKA"]

    def test_all_known_alias_names(self):
        cols = ["aliases", "alias", "alt_name", "alt_names",
                "nickname", "nicknames", "aka", "also_known_as"]
        result = _detect_alias_columns(cols)
        assert result == cols

    def test_preserves_order(self):
        cols = ["id", "nickname", "name", "aliases"]
        result = _detect_alias_columns(cols)
        assert result == ["nickname", "aliases"]


class TestSuggestCanonical:
    def test_nlp_lemma_match_in_terms(self):
        terms = ["photographs", "photograph", "photography"]
        result = _suggest_canonical(terms, "nlp_lemma", "lemma: photograph")
        assert result == "photograph"

    def test_nlp_lemma_fallback_to_shortest(self):
        # lemma "go" not in terms — falls back to shortest (min by len, first wins on tie)
        terms = ["going", "gone", "goes"]
        result = _suggest_canonical(terms, "nlp_lemma", "lemma: go")
        assert result == "gone"  # 4 chars; "gone" appears before "goes" in iteration order

    def test_encoded_canonical_parsed(self):
        terms = ["SF", "Frisco", "San Francisco"]
        result = _suggest_canonical(
            terms, "entity", "canonical:San Francisco | Locations → aliases column"
        )
        assert result == "San Francisco"

    def test_encoded_canonical_not_in_terms_falls_back(self):
        terms = ["photo", "pic"]
        result = _suggest_canonical(terms, "llm_semantic", "canonical:photograph | semantic grouping")
        # "photograph" not in terms → shortest fallback
        assert result == "pic"

    def test_default_shortest_when_no_hint(self):
        terms = ["photography", "photo", "photograph"]
        result = _suggest_canonical(terms, "entity", None)
        assert result == "photo"

    def test_empty_terms_returns_empty_string(self):
        assert _suggest_canonical([], "nlp_lemma", "lemma: foo") == ""


class TestPathsToTree:
    def test_two_level(self):
        result = _paths_to_tree(["Wildlife::Bear", "Wildlife::Eagle"])
        assert result == {"Wildlife": ["Bear", "Eagle"]}

    def test_three_level(self):
        result = _paths_to_tree(["Nature::Wildlife::Bear"])
        assert result == {"Nature": {"Wildlife": ["Bear"]}}

    def test_mixed_depth(self):
        result = _paths_to_tree([
            "Nature::Wildlife::Bear",
            "Nature::Wildlife::Eagle",
            "Urban::Church",
        ])
        assert result == {
            "Nature": {"Wildlife": ["Bear", "Eagle"]},
            "Urban": ["Church"],
        }

    def test_multiple_top_level(self):
        result = _paths_to_tree([
            "Nature::Wildlife::Bear",
            "Urban::Architecture::Church",
        ])
        assert "Nature" in result
        assert "Urban" in result
        assert result["Nature"]["Wildlife"] == ["Bear"]
        assert result["Urban"]["Architecture"] == ["Church"]

    def test_empty_returns_empty_dict(self):
        assert _paths_to_tree([]) == {}
