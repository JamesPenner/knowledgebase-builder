"""Unit tests for normalize_filename — pure function, no DB."""
from src.stages.normalize import normalize_filename


def _capture(pattern, extract_as="field", keep_token=False, value_type="", format_str="", date_precision=None, is_regex=True):
    return {
        "pattern": pattern,
        "is_regex": is_regex,
        "action": "capture",
        "extract_as": extract_as,
        "keep_token": keep_token,
        "value_type": value_type,
        "format_str": format_str,
        "date_precision": date_precision,
        "replace_with": None,
        "replace_type": None,
    }


def _ignore(pattern, is_regex=True):
    return {"pattern": pattern, "is_regex": is_regex, "action": "ignore"}


def _reject(pattern, is_regex=True):
    return {"pattern": pattern, "is_regex": is_regex, "action": "reject"}


def _replace(pattern, replace_with, is_regex=False):
    return {"pattern": pattern, "is_regex": is_regex, "action": "replace", "replace_with": replace_with}


def test_ignore_rule_drops_token():
    rules = [_ignore(r"^\d{10}$")]
    name, captured = normalize_filename("2045631987_vacation.jpg", rules, [], set())
    assert "2045631987" not in name
    assert "vacation" in name


def test_ignore_rule_does_not_populate_captured():
    rules = [_ignore(r"^\d{10}$")]
    _, captured = normalize_filename("2045631987_vacation.jpg", rules, [], set())
    assert captured == {}


def test_ignore_rule_first_match_wins():
    rules = [
        _ignore(r"^\d+$"),
        _capture(r"^\d+$", extract_as="some_field"),
    ]
    name, captured = normalize_filename("12345_photo.jpg", rules, [], set())
    assert "12345" not in name
    assert "some_field" not in captured


def test_capture_rule_extracts_field():
    rules = [_capture(r"^(\d{8})$", extract_as="file_date")]
    name, captured = normalize_filename("20230415_beach.jpg", rules, [], set())
    assert captured.get("file_date") == "20230415"
    assert "20230415" not in name  # keep_token=False by default


def test_capture_rule_with_keep_token_true():
    rules = [_capture(r"^(\d{8})$", extract_as="file_date", keep_token=True)]
    name, captured = normalize_filename("20230415_beach.jpg", rules, [], set())
    assert captured.get("file_date") == "20230415"
    assert "20230415" in name


def test_reject_rule_drops_token():
    rules = [_reject(r"^\d{10}$")]
    name, _ = normalize_filename("2045631987_vacation.jpg", rules, [], set())
    assert "2045631987" not in name
    assert "vacation" in name


def test_replace_rule_substitutes_token():
    rules = [_replace("colour", "color")]
    name, _ = normalize_filename("colour_photo.jpg", rules, [], set())
    assert "color" in name
    assert "colour" not in name


def test_no_rules_keeps_all_tokens():
    name, captured = normalize_filename("beach_holiday_2023.jpg", [], [], set())
    assert "beach" in name
    assert "holiday" in name
    assert "2023" in name
    assert captured == {}


def test_stoplist_drops_token():
    name, _ = normalize_filename("a_beach_the.jpg", [], [], {"a", "the"})
    assert "beach" in name
    assert "a" not in name.split()
    assert "the" not in name.split()
