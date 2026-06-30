from src.pipeline.filter_spec import FilterSpec


def test_filter_spec_defaults():
    spec = FilterSpec()
    assert spec.glob is None
    assert spec.count_limit is None
    assert spec.modified_after is None
    assert spec.exclude_patterns == []


def test_filter_spec_from_dict():
    d = {"glob": "2024-*", "count_limit": 50, "modified_after": "2024-01-01", "exclude_patterns": ["@eaDir"]}
    spec = FilterSpec.from_dict(d)
    assert spec.glob == "2024-*"
    assert spec.count_limit == 50
    assert spec.modified_after == "2024-01-01"
    assert spec.exclude_patterns == ["@eaDir"]


def test_filter_spec_to_dict_omits_defaults():
    spec = FilterSpec(glob="*.jpg")
    d = spec.to_dict()
    assert d == {"glob": "*.jpg"}
    assert "file_type" not in d
    assert "count_limit" not in d


def test_filter_spec_round_trip():
    original = {"glob": "*.jpg", "modified_after": "2023-06-01", "exclude_patterns": ["#recycle", "@eaDir"]}
    spec = FilterSpec.from_dict(original)
    result = spec.to_dict()
    assert result["glob"] == "*.jpg"
    assert result["modified_after"] == "2023-06-01"
    assert result["exclude_patterns"] == ["#recycle", "@eaDir"]
