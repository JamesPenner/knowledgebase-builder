"""Unit tests for temporal field derivation (pure functions, no DB)."""
import pytest

from src.stages.temporal import derive_temporal_fields, _parse_dt


# ---------------------------------------------------------------------------
# _parse_dt
# ---------------------------------------------------------------------------

def test_parse_dt_exif_format():
    date, hour = _parse_dt("2023:12:25 14:30:00")
    assert date == "2023-12-25"
    assert hour == 14


def test_parse_dt_iso_format():
    date, hour = _parse_dt("2023-12-25T08:05:00")
    assert date == "2023-12-25"
    assert hour == 8


def test_parse_dt_date_only():
    date, hour = _parse_dt("2023-07-04")
    assert date == "2023-07-04"
    assert hour is None


def test_parse_dt_empty():
    assert _parse_dt("") == (None, None)
    assert _parse_dt("   ") == (None, None)


def test_parse_dt_too_short():
    assert _parse_dt("2023") == (None, None)


# ---------------------------------------------------------------------------
# derive_temporal_fields — basic year / decade
# ---------------------------------------------------------------------------

def test_derive_year_decade():
    out = derive_temporal_fields("2023:07:04 10:00:00")
    assert out["year"] == 2023
    assert out["decade"] == "2020s"


def test_derive_decade_boundary():
    out = derive_temporal_fields("1990:01:01 00:00:00")
    assert out["decade"] == "1990s"


def test_derive_empty_returns_empty():
    assert derive_temporal_fields(None) == {}
    assert derive_temporal_fields("") == {}
    assert derive_temporal_fields(None, None) == {}


# ---------------------------------------------------------------------------
# month_name
# ---------------------------------------------------------------------------

def test_derive_month_name_july():
    out = derive_temporal_fields("2023:07:04 10:00:00")
    assert out["month_name"] == "July"


def test_derive_month_name_december():
    out = derive_temporal_fields("2023:12:25 00:00:00")
    assert out["month_name"] == "December"


# ---------------------------------------------------------------------------
# day_name
# ---------------------------------------------------------------------------

def test_derive_day_name_monday():
    # 2023-01-02 is a Monday
    out = derive_temporal_fields("2023:01:02 09:00:00")
    assert out["day_name"] == "Monday"


def test_derive_day_name_saturday():
    # 2023-12-23 is a Saturday
    out = derive_temporal_fields("2023:12:23 12:00:00")
    assert out["day_name"] == "Saturday"


# ---------------------------------------------------------------------------
# season
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("month,expected", [
    (3, "Spring"), (4, "Spring"), (5, "Spring"),
    (6, "Summer"), (7, "Summer"), (8, "Summer"),
    (9, "Autumn"), (10, "Autumn"), (11, "Autumn"),
    (12, "Winter"), (1, "Winter"), (2, "Winter"),
])
def test_derive_season(month, expected):
    date_str = f"2023:{month:02d}:15 12:00:00"
    out = derive_temporal_fields(date_str)
    assert out["season"] == expected


# ---------------------------------------------------------------------------
# time_of_day
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (0,  "overnight"),
    (3,  "overnight"),
    (4,  "overnight"),
    (5,  "blue_hour_morning"),
    (6,  "golden_hour_morning"),
    (7,  "golden_hour_morning"),
    (8,  "morning"),
    (10, "morning"),
    (11, "midday"),
    (13, "midday"),
    (14, "afternoon"),
    (16, "afternoon"),
    (17, "golden_hour_evening"),
    (18, "golden_hour_evening"),
    (19, "blue_hour_evening"),
    (20, "night"),
    (23, "night"),
])
def test_derive_time_of_day(hour, expected):
    date_str = f"2023:07:01 {hour:02d}:00:00"
    out = derive_temporal_fields(date_str)
    assert out["time_of_day"] == expected


def test_derive_no_time_when_date_only():
    out = derive_temporal_fields(None, "2023-07-04")
    assert "time_of_day" not in out


# ---------------------------------------------------------------------------
# holiday
# ---------------------------------------------------------------------------

def test_derive_holiday_christmas():
    out = derive_temporal_fields("2023:12:25 10:00:00")
    assert out.get("holiday") == "Christmas Day"


def test_derive_holiday_new_years_day():
    out = derive_temporal_fields("2023:01:01 12:00:00")
    assert out.get("holiday") == "New Year's Day"


def test_derive_holiday_halloween():
    out = derive_temporal_fields("2023:10:31 20:00:00")
    assert out.get("holiday") == "Halloween"


def test_derive_holiday_easter_2023():
    # Easter 2023 is April 9
    out = derive_temporal_fields("2023:04:09 10:00:00")
    assert out.get("holiday") == "Easter"


def test_derive_no_holiday_ordinary_day():
    out = derive_temporal_fields("2023:03:15 12:00:00")
    assert out.get("holiday") is None


# ---------------------------------------------------------------------------
# file_date fallback
# ---------------------------------------------------------------------------

def test_derive_file_date_fallback():
    out = derive_temporal_fields(None, "2022-06-21")
    assert out["year"] == 2022
    assert out["month_name"] == "June"
    assert out["season"] == "Summer"
    assert "time_of_day" not in out


def test_exif_date_takes_priority_over_file_date():
    out = derive_temporal_fields("2020:07:04 10:00:00", "2022-06-21")
    assert out["year"] == 2020
    assert out["month_name"] == "July"


def test_file_date_with_time_component():
    out = derive_temporal_fields(None, "2022-06-21T15:30:00")
    assert out["time_of_day"] == "afternoon"
