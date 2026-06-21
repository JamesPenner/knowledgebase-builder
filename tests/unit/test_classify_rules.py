"""Unit tests for classify_rules pure functions — no DB or filesystem."""
import json


from src.stages.classify_rules import (
    BUILTIN_RULES,
    _easter,
    _nth_weekday,
    _decade_tag,
    evaluate_rule,
    infer_date_precision,
)


def _rule(match_type, result_tag, field_name=None, match_config=None, minimum=None, category="calendar"):
    return {
        "id": 1,
        "match_type": match_type,
        "result_tag": result_tag,
        "category": category,
        "field_name": field_name,
        "match_config": json.dumps(match_config or {}),
        "minimum_precision": minimum,
        "source": "captured",
        "enabled": 1,
    }


def test_month_day_christmas_fires():
    rule = _rule("month_day", "Christmas Day", "file_date", {"month": 12, "day": 25}, "full")
    assert evaluate_rule(rule, {"file_date": "2023-12-25"}) == "Christmas Day"


def test_month_day_wrong_date_skips():
    rule = _rule("month_day", "Christmas Day", "file_date", {"month": 12, "day": 25}, "full")
    assert evaluate_rule(rule, {"file_date": "2023-12-24"}) is None


def test_month_day_missing_date_skips():
    rule = _rule("month_day", "Christmas Day", "file_date", {"month": 12, "day": 25}, "full")
    assert evaluate_rule(rule, {}) is None


def test_month_day_low_precision_skips():
    rule = _rule("month_day", "Christmas Day", "file_date", {"month": 12, "day": 25}, "full")
    # Only year precision — not full
    fields = {"file_date": "2023", "file_date_precision": "year"}
    assert evaluate_rule(rule, fields) is None


def test_month_range_summer_fires():
    rule = _rule("month_range", "Summer", "file_date", {"months": [6, 7, 8]}, "month")
    assert evaluate_rule(rule, {"file_date": "2022-07-15"}) == "Summer"


def test_month_range_winter_fires_december():
    rule = _rule("month_range", "Winter", "file_date", {"months": [12, 1, 2]}, "month")
    assert evaluate_rule(rule, {"file_date": "2022-12-01"}) == "Winter"


def test_range_telephoto_fires():
    rule = _rule("range", "Telephoto", "focal_length_35mm", {"min": 71.0}, category="technical")
    assert evaluate_rule(rule, {"focal_length_35mm": "200"}) == "Telephoto"


def test_range_wide_fires():
    rule = _rule("range", "Wide-angle", "focal_length_35mm", {"min": 19.0, "max": 28.0}, category="technical")
    assert evaluate_rule(rule, {"focal_length_35mm": "24"}) == "Wide-angle"
    assert evaluate_rule(rule, {"focal_length_35mm": "35"}) is None


def test_comparison_landscape():
    rule = _rule("comparison", "Landscape", match_config={"field_a": "exif_width", "op": ">", "field_b": "exif_height"}, category="technical")
    assert evaluate_rule(rule, {"exif_width": "3000", "exif_height": "2000"}) == "Landscape"
    assert evaluate_rule(rule, {"exif_width": "2000", "exif_height": "3000"}) is None


def test_comparison_portrait():
    rule = _rule("comparison", "Portrait", match_config={"field_a": "exif_width", "op": "<", "field_b": "exif_height"}, category="technical")
    assert evaluate_rule(rule, {"exif_width": "2000", "exif_height": "3000"}) == "Portrait"


def test_easter_known_years():
    assert _easter(2024) == (3, 31)   # March 31, 2024
    assert _easter(2025) == (4, 20)   # April 20, 2025
    assert _easter(2023) == (4, 9)    # April 9, 2023


def test_nth_weekday_canadian_thanksgiving_2024():
    # 2nd Monday in October 2024 = Oct 14
    assert _nth_weekday(2024, 10, 0, 2) == 14


def test_nth_weekday_us_thanksgiving_2024():
    # 4th Thursday in November 2024 = Nov 28
    assert _nth_weekday(2024, 11, 3, 4) == 28


def test_nth_weekday_mothers_day_2024():
    # 2nd Sunday in May 2024 = May 12
    assert _nth_weekday(2024, 5, 6, 2) == 12


def test_decade_tag():
    assert _decade_tag("1978-10-13") == "1970s"
    assert _decade_tag("2000-01-01") == "2000s"
    assert _decade_tag("1990-06-15") == "1990s"


def test_computed_decade_fires():
    rule = _rule("computed", "Decade", match_config={"algorithm": "decade"}, minimum="year")
    tag = evaluate_rule(rule, {"file_date": "1985-06-10"})
    assert tag == "1980s"


def test_computed_easter_fires():
    rule = _rule("computed", "Easter", match_config={"algorithm": "easter"}, minimum="full")
    assert evaluate_rule(rule, {"file_date": "2024-03-31"}) == "Easter"
    assert evaluate_rule(rule, {"file_date": "2024-03-30"}) is None


def test_computed_panoramic_fires():
    rule = {
        "id": 99, "match_type": "computed", "result_tag": "Panoramic",
        "category": "technical", "field_name": None, "source": "computed",
        "match_config": json.dumps({"algorithm": "panoramic", "min_ratio": 2.0}),
        "minimum_precision": None, "enabled": 1,
    }
    assert evaluate_rule(rule, {"exif_width": "4000", "exif_height": "1000"}) == "Panoramic"
    assert evaluate_rule(rule, {"exif_width": "3000", "exif_height": "2000"}) is None


def test_infer_date_precision():
    assert infer_date_precision("2023-06-15") == "full"
    assert infer_date_precision("2023-06") == "month"
    assert infer_date_precision("2023") == "year"
    assert infer_date_precision("1970s") == "decade"
    assert infer_date_precision("") == "full"


def test_builtin_rules_all_have_required_keys():
    required = {"label", "result_tag", "category", "match_type", "match_config", "is_builtin"}
    for rule in BUILTIN_RULES:
        missing = required - rule.keys()
        assert not missing, f"Rule {rule.get('label')} missing keys: {missing}"


def test_builtin_rules_match_config_is_valid_json():
    for rule in BUILTIN_RULES:
        cfg = rule["match_config"]
        parsed = json.loads(cfg)
        assert isinstance(parsed, dict), f"Rule {rule['label']} match_config not a dict"


# ---------------------------------------------------------------------------
# in_list match type
# ---------------------------------------------------------------------------

def test_in_list_fires_when_value_in_list():
    rule = _rule("in_list", "raw_file", "file_format", {"values": ["raw", "arw", "cr2"]})
    assert evaluate_rule(rule, {"file_format": "arw"}) == "raw_file"


def test_in_list_case_insensitive():
    rule = _rule("in_list", "raw_file", "file_format", {"values": ["raw", "arw"]})
    assert evaluate_rule(rule, {"file_format": "ARW"}) == "raw_file"


def test_in_list_returns_none_when_not_in_list():
    rule = _rule("in_list", "raw_file", "file_format", {"values": ["raw", "arw"]})
    assert evaluate_rule(rule, {"file_format": "jpg"}) is None


def test_in_list_returns_none_when_field_missing():
    rule = _rule("in_list", "raw_file", "file_format", {"values": ["raw"]})
    assert evaluate_rule(rule, {}) is None


def test_in_list_heic_fires():
    rule = _rule("in_list", "heic", "file_format", {"values": ["heic", "heif"]})
    assert evaluate_rule(rule, {"file_format": "heic"}) == "heic"
    assert evaluate_rule(rule, {"file_format": "jpg"}) is None


def test_in_list_video_fires():
    rule = _rule("in_list", "video", "file_format", {"values": ["mp4", "mov", "avi"]})
    assert evaluate_rule(rule, {"file_format": "mov"}) == "video"


# ---------------------------------------------------------------------------
# New range-based rules (hour_of_day, aspect_ratio, ISO, quality)
# ---------------------------------------------------------------------------

def test_hour_range_midday_fires():
    rule = _rule("range", "midday", "hour_of_day", {"min": 11, "max": 13})
    assert evaluate_rule(rule, {"hour_of_day": "12"}) == "midday"
    assert evaluate_rule(rule, {"hour_of_day": "14"}) is None


def test_hour_range_overnight_fires():
    rule = _rule("range", "overnight", "hour_of_day", {"min": 0, "max": 4})
    assert evaluate_rule(rule, {"hour_of_day": "2"}) == "overnight"
    assert evaluate_rule(rule, {"hour_of_day": "5"}) is None


def test_aspect_ratio_square_fires():
    rule = _rule("range", "Square", "aspect_ratio", {"min": 0.9, "max": 1.1}, category="technical")
    assert evaluate_rule(rule, {"aspect_ratio": "1.0"}) == "Square"
    assert evaluate_rule(rule, {"aspect_ratio": "1.5"}) is None


def test_iso_high_fires():
    rule = _rule("range", "high_iso", "iso", {"min": 800.0, "max": 3200.0}, category="technical")
    assert evaluate_rule(rule, {"iso": "1600"}) == "high_iso"
    assert evaluate_rule(rule, {"iso": "100"}) is None


def test_quality_range_low_key():
    rule = _rule("range", "low_key", "exposure", {"max": 0.20}, category="tonality")
    assert evaluate_rule(rule, {"exposure": "0.10"}) == "low_key"
    assert evaluate_rule(rule, {"exposure": "0.50"}) is None


def test_quality_range_blown_highlights():
    rule = _rule("range", "blown_highlights", "highlights", {"min": 0.15}, category="tonality")
    assert evaluate_rule(rule, {"highlights": "0.20"}) == "blown_highlights"
    assert evaluate_rule(rule, {"highlights": "0.05"}) is None


def test_quality_range_vibrant_saturation():
    rule = _rule("range", "vibrant", "saturation_mean", {"min": 0.50}, category="tonality")
    assert evaluate_rule(rule, {"saturation_mean": "0.65"}) == "vibrant"
    assert evaluate_rule(rule, {"saturation_mean": "0.30"}) is None


def test_quality_range_warm_tones():
    rule = _rule("range", "warm_tones", "dominant_hue", {"min": 20.0, "max": 70.0}, category="tonality")
    assert evaluate_rule(rule, {"dominant_hue": "45.0"}) == "warm_tones"
    assert evaluate_rule(rule, {"dominant_hue": "180.0"}) is None


def test_flash_fired_exact():
    rule = _rule("exact", "flash_fired", "flash_fired", {"value": "1"})
    assert evaluate_rule(rule, {"flash_fired": "1"}) == "flash_fired"
    assert evaluate_rule(rule, {"flash_fired": "0"}) is None


def test_gps_present_exact():
    rule = _rule("exact", "geotagged", "gps_present", {"value": "true"})
    assert evaluate_rule(rule, {"gps_present": "true"}) == "geotagged"
    assert evaluate_rule(rule, {}) is None


def test_builtin_rules_include_new_categories():
    tags = {r["result_tag"] for r in BUILTIN_RULES}
    assert "low_key" in tags
    assert "blown_highlights" in tags
    assert "flash_fired" in tags
    assert "geotagged" in tags
    assert "raw_file" in tags
    assert "midday" in tags
    assert "high_iso" in tags
    assert "Boxing Day" in tags
    assert "ANZAC Day" in tags


def test_builtin_rules_count_exceeds_previous():
    assert len(BUILTIN_RULES) >= 70
