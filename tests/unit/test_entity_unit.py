"""Unit tests for entity CLI helpers: _normalise and _find_near_duplicates."""
from src.cli.entity import _find_near_duplicates


def test_near_dup_high_similarity():
    # "Burnaby Lake" vs "Burnaby Lakes" — singular/plural, scores ~0.96
    results = _find_near_duplicates("Burnaby Lake", ["Burnaby Lakes"], threshold=0.85)
    assert len(results) == 1
    name, score = results[0]
    assert name == "Burnaby Lakes"
    assert score >= 0.85


def test_near_dup_distinct():
    results = _find_near_duplicates("Kits Beach", ["Buntzen Lake"], threshold=0.85)
    assert results == []


def test_near_dup_exact_excluded():
    # Exact matches are skipped — upsert ON CONFLICT handles them
    results = _find_near_duplicates("Kits Beach", ["Kits Beach"], threshold=0.85)
    assert results == []


def test_near_dup_normalisation():
    # Extra whitespace and case differences collapse to the same normalised form
    results = _find_near_duplicates("Kits  Beach", ["kits beach"], threshold=0.85)
    assert results == []


def test_near_dup_empty_existing():
    results = _find_near_duplicates("Anywhere", [], threshold=0.85)
    assert results == []


def test_near_dup_multiple_matches():
    # "Burnaby Lake" matches both "Burnaby Lakes" (~0.96) and "Burnaby Lake Park" (~0.83)
    existing = ["Burnaby Lakes", "Burnaby Lake Park"]
    results = _find_near_duplicates("Burnaby Lake", existing, threshold=0.80)
    matched_names = {r[0] for r in results}
    assert "Burnaby Lakes" in matched_names
    assert "Burnaby Lake Park" in matched_names
