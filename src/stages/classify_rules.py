"""Pure classify rule definitions and evaluation functions. No DB access."""
import json
from math import atan2, cos, radians, sin, sqrt

# ---------------------------------------------------------------------------
# Precision hierarchy
# ---------------------------------------------------------------------------

_PRECISION_ORDER = {"century": 1, "decade": 2, "year": 3, "month": 4, "full": 5}


def infer_date_precision(date_str: str) -> str:
    """Infer precision level from an ISO 8601 partial date string."""
    if not date_str:
        return "full"
    s = date_str.strip()
    if len(s) == 4 and s.isdigit():
        return "year"
    if len(s) == 5 and s[4] == "s" and s[:4].isdigit():
        return "decade"
    parts = s.split("-")
    if len(parts) >= 3:
        return "full"
    if len(parts) == 2:
        return "month"
    return "year"


# ---------------------------------------------------------------------------
# Computed algorithm helpers
# ---------------------------------------------------------------------------

def _easter(year: int) -> tuple[int, int]:
    """Return (month, day) of Easter Sunday via Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return month, day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> int:
    """Return the day-of-month for the nth occurrence of weekday (0=Mon, 6=Sun)."""
    import calendar
    first_weekday, _ = calendar.monthrange(year, month)
    diff = (weekday - first_weekday) % 7
    return 1 + diff + (n - 1) * 7


def _decade_tag(date_str: str) -> str:
    """Return decade label string like '1970s' from an ISO date."""
    year = int(date_str[:4])
    return f"{(year // 10) * 10}s"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres between two WGS-84 lat/lon points."""
    R = 6_371_000.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlam = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlam / 2) ** 2
    return 2.0 * R * atan2(sqrt(a), sqrt(1.0 - a))


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

def _meets_precision(date_str: str, fields: dict, minimum: str | None) -> bool:
    if minimum is None:
        return True
    precision = fields.get("file_date_precision") or infer_date_precision(date_str)
    return _PRECISION_ORDER.get(precision, 5) >= _PRECISION_ORDER.get(minimum, 1)


def evaluate_rule(rule: dict, fields: dict[str, str]) -> str | None:
    """Return result_tag if rule fires against fields, else None.

    fields is a flat {name: value_str} dict assembled from all sources for one file.
    Values are always strings (as stored in SQLite).
    """
    match_type = rule["match_type"]
    raw_config = rule.get("match_config") or "{}"
    cfg = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
    field_name = rule.get("field_name")
    minimum = rule.get("minimum_precision")

    if match_type == "month_day":
        date_str = fields.get(field_name) if field_name else None
        if not date_str:
            return None
        if not _meets_precision(date_str, fields, minimum):
            return None
        try:
            parts = date_str.split("-")
            if len(parts) < 3:
                return None
            if int(parts[1]) == cfg["month"] and int(parts[2][:2]) == cfg["day"]:
                return rule["result_tag"]
        except (IndexError, KeyError, ValueError):
            return None

    elif match_type == "month_range":
        date_str = fields.get(field_name) if field_name else None
        if not date_str:
            return None
        if not _meets_precision(date_str, fields, minimum):
            return None
        try:
            parts = date_str.split("-")
            if len(parts) < 2:
                return None
            if int(parts[1]) in cfg["months"]:
                return rule["result_tag"]
        except (IndexError, KeyError, ValueError):
            return None

    elif match_type == "range":
        raw = fields.get(field_name) if field_name else None
        if raw is None:
            return None
        try:
            val = float(raw)
        except (ValueError, TypeError):
            return None
        lo = cfg.get("min")
        hi = cfg.get("max")
        if lo is not None and val < float(lo):
            return None
        if hi is not None and val > float(hi):
            return None
        return rule["result_tag"]

    elif match_type == "comparison":
        a_raw = fields.get(cfg.get("field_a", ""))
        b_raw = fields.get(cfg.get("field_b", ""))
        if a_raw is None or b_raw is None:
            return None
        try:
            a_val, b_val = float(a_raw), float(b_raw)
        except (ValueError, TypeError):
            return None
        op = cfg.get("op", ">")
        passed = (
            (op == ">" and a_val > b_val) or
            (op == ">=" and a_val >= b_val) or
            (op == "<" and a_val < b_val) or
            (op == "<=" and a_val <= b_val) or
            (op == "==" and a_val == b_val)
        )
        return rule["result_tag"] if passed else None

    elif match_type == "exact":
        raw = fields.get(field_name) if field_name else None
        if raw is None:
            return None
        return rule["result_tag"] if str(raw) == str(cfg.get("value", "")) else None

    elif match_type == "in_list":
        raw = fields.get(field_name) if field_name else None
        if raw is None:
            return None
        values = [str(v).lower() for v in cfg.get("values", [])]
        return rule["result_tag"] if str(raw).lower() in values else None

    elif match_type == "computed":
        algo = cfg.get("algorithm", "")

        if algo == "panoramic":
            w_raw = fields.get("exif_width")
            h_raw = fields.get("exif_height")
            if not w_raw or not h_raw:
                return None
            try:
                if float(w_raw) / float(h_raw) >= float(cfg.get("min_ratio", 2.0)):
                    return rule["result_tag"]
            except (ValueError, ZeroDivisionError):
                return None
            return None

        date_str = fields.get("file_date")
        if not date_str:
            return None

        if algo == "decade":
            if not _meets_precision(date_str, fields, minimum):
                return None
            try:
                return _decade_tag(date_str)
            except (ValueError, IndexError):
                return None

        if not _meets_precision(date_str, fields, minimum):
            return None
        try:
            parts = date_str.split("-")
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2][:2])
        except (IndexError, ValueError):
            return None

        if algo == "easter":
            e_month, e_day = _easter(year)
            return rule["result_tag"] if month == e_month and day == e_day else None
        elif algo == "thanksgiving_ca":
            e_day = _nth_weekday(year, 10, 0, 2)
            return rule["result_tag"] if month == 10 and day == e_day else None
        elif algo == "thanksgiving_us":
            e_day = _nth_weekday(year, 11, 3, 4)
            return rule["result_tag"] if month == 11 and day == e_day else None
        elif algo == "mothers_day":
            e_day = _nth_weekday(year, 5, 6, 2)
            return rule["result_tag"] if month == 5 and day == e_day else None
        elif algo == "fathers_day":
            e_day = _nth_weekday(year, 6, 6, 3)
            return rule["result_tag"] if month == 6 and day == e_day else None

    return None


# ---------------------------------------------------------------------------
# Built-in rule definitions
# ---------------------------------------------------------------------------

def _md(label, tag, month, day):
    return {
        "label": label, "result_tag": tag, "category": "calendar",
        "source": "captured", "field_name": "file_date",
        "match_type": "month_day",
        "match_config": json.dumps({"month": month, "day": day}),
        "minimum_precision": "full", "is_builtin": 1,
    }


def _season(label, tag, months):
    return {
        "label": label, "result_tag": tag, "category": "calendar",
        "source": "captured", "field_name": "file_date",
        "match_type": "month_range",
        "match_config": json.dumps({"months": months}),
        "minimum_precision": "month", "is_builtin": 1,
    }


def _computed(label, tag, algo, category="calendar", minimum=None, extra=None):
    cfg = {"algorithm": algo}
    if extra:
        cfg.update(extra)
    return {
        "label": label, "result_tag": tag, "category": category,
        "source": "computed", "field_name": None,
        "match_type": "computed",
        "match_config": json.dumps(cfg),
        "minimum_precision": minimum, "is_builtin": 1,
    }


def _tech_range(label, tag, field, *, lo=None, hi=None, category="technical"):
    cfg: dict = {}
    if lo is not None:
        cfg["min"] = lo
    if hi is not None:
        cfg["max"] = hi
    return {
        "label": label, "result_tag": tag, "category": category,
        "source": "exif", "field_name": field,
        "match_type": "range",
        "match_config": json.dumps(cfg),
        "minimum_precision": None, "is_builtin": 1,
    }


def _tech_compare(label, tag, field_a, op, field_b):
    return {
        "label": label, "result_tag": tag, "category": "technical",
        "source": "exif", "field_name": None,
        "match_type": "comparison",
        "match_config": json.dumps({"field_a": field_a, "op": op, "field_b": field_b}),
        "minimum_precision": None, "is_builtin": 1,
    }


def _exact(label, tag, field, value, category="technical"):
    return {
        "label": label, "result_tag": tag, "category": category,
        "source": "computed", "field_name": field,
        "match_type": "exact",
        "match_config": json.dumps({"value": value}),
        "minimum_precision": None, "is_builtin": 1,
    }


def _in_list(label, tag, field, values, category="technical"):
    return {
        "label": label, "result_tag": tag, "category": category,
        "source": "computed", "field_name": field,
        "match_type": "in_list",
        "match_config": json.dumps({"values": values}),
        "minimum_precision": None, "is_builtin": 1,
    }


def _hour_range(label, tag, lo, hi):
    return {
        "label": label, "result_tag": tag, "category": "temporal",
        "source": "computed", "field_name": "hour_of_day",
        "match_type": "range",
        "match_config": json.dumps({"min": lo, "max": hi}),
        "minimum_precision": None, "is_builtin": 1,
    }


def _quality_range(label, tag, field, *, lo=None, hi=None):
    cfg: dict = {}
    if lo is not None:
        cfg["min"] = lo
    if hi is not None:
        cfg["max"] = hi
    return {
        "label": label, "result_tag": tag, "category": "tonality",
        "source": "quality", "field_name": field,
        "match_type": "range",
        "match_config": json.dumps(cfg),
        "minimum_precision": None, "is_builtin": 1,
    }


BUILTIN_RULES: list[dict] = [
    # ── Fixed calendar events ─────────────────────────────────────────────────
    _md("Christmas Day",     "Christmas Day",     12, 25),
    _md("Christmas Eve",     "Christmas Eve",     12, 24),
    _md("Boxing Day",        "Boxing Day",        12, 26),
    _md("New Year's Day",    "New Year's Day",     1,  1),
    _md("New Year's Eve",    "New Year's Eve",    12, 31),
    _md("Valentine's Day",   "Valentine's Day",    2, 14),
    _md("St. Patrick's Day", "St. Patrick's Day",  3, 17),
    _md("ANZAC Day",         "ANZAC Day",          4, 25),
    _md("Halloween",         "Halloween",         10, 31),
    _md("Canada Day",        "Canada Day",         7,  1),
    _md("Remembrance Day",   "Remembrance Day",   11, 11),

    # ── Seasons (Northern Hemisphere) ─────────────────────────────────────────
    _season("Spring", "Spring", [3, 4, 5]),
    _season("Summer", "Summer", [6, 7, 8]),
    _season("Autumn", "Autumn", [9, 10, 11]),
    _season("Winter", "Winter", [12, 1, 2]),

    # ── Computed calendar events ───────────────────────────────────────────────
    _computed("Easter Sunday",         "Easter",                "easter",          minimum="full"),
    _computed("Canadian Thanksgiving", "Canadian Thanksgiving", "thanksgiving_ca", minimum="full"),
    _computed("US Thanksgiving",       "US Thanksgiving",       "thanksgiving_us", minimum="full"),
    _computed("Mother's Day",          "Mother's Day",          "mothers_day",     minimum="full"),
    _computed("Father's Day",          "Father's Day",          "fathers_day",     minimum="full"),
    _computed("Decade tag",            "Decade",                "decade",          minimum="year"),

    # ── Time of day ───────────────────────────────────────────────────────────
    _hour_range("Overnight",            "overnight",            0,  4),
    _hour_range("Blue hour (morning)",  "blue_hour_morning",    5,  5),
    _hour_range("Golden hour (morning)","golden_hour_morning",  6,  7),
    _hour_range("Morning",              "morning",              8, 10),
    _hour_range("Midday",               "midday",              11, 13),
    _hour_range("Afternoon",            "afternoon",           14, 16),
    _hour_range("Golden hour (evening)","golden_hour_evening", 17, 18),
    _hour_range("Blue hour (evening)",  "blue_hour_evening",   19, 19),
    _hour_range("Night",                "night",               20, 23),

    # ── Orientation / framing ─────────────────────────────────────────────────
    _tech_compare("Landscape orientation", "Landscape", "exif_width", ">",  "exif_height"),
    _tech_compare("Portrait orientation",  "Portrait",  "exif_width", "<",  "exif_height"),
    _tech_range("Square",      "Square",     "aspect_ratio", lo=0.9,  hi=1.1),
    _tech_range("Widescreen",  "Widescreen", "aspect_ratio", lo=1.77),
    _computed("Panoramic", "Panoramic", "panoramic", category="technical", extra={"min_ratio": 2.0}),

    # ── Focal length (35 mm equivalent) ──────────────────────────────────────
    _tech_range("Ultra-wide lens",    "ultra_wide",       "focal_length_35mm", hi=24.0),
    _tech_range("Wide-angle lens",    "wide_angle",       "focal_length_35mm", lo=24.0, hi=50.0),
    _tech_range("Standard lens",      "standard",         "focal_length_35mm", lo=50.0, hi=70.0),
    _tech_range("Telephoto lens",     "telephoto",        "focal_length_35mm", lo=70.0, hi=200.0),
    _tech_range("Super-telephoto",    "super_telephoto",  "focal_length_35mm", lo=200.0),

    # ── Aperture ──────────────────────────────────────────────────────────────
    _tech_range("Very wide aperture", "very_wide_aperture", "aperture", hi=1.8),
    _tech_range("Wide aperture",      "wide_aperture",      "aperture", hi=2.8),
    _tech_range("Medium aperture",    "medium_aperture",    "aperture", lo=2.8, hi=5.6),
    _tech_range("Narrow aperture",    "narrow_aperture",    "aperture", lo=5.6, hi=11.0),
    _tech_range("Very narrow aperture","very_narrow_aperture","aperture",lo=11.0),

    # ── Shutter speed ─────────────────────────────────────────────────────────
    _tech_range("Long exposure",  "long_exposure",  "shutter_speed", lo=1.0),
    _tech_range("Slow shutter",   "slow_shutter",   "shutter_speed", lo=0.033, hi=1.0),
    _tech_range("High speed",     "high_speed",     "shutter_speed", hi=0.001),

    # ── ISO ───────────────────────────────────────────────────────────────────
    _tech_range("Low ISO",       "low_iso",       "iso", hi=100.0),
    _tech_range("Standard ISO",  "standard_iso",  "iso", lo=100.0, hi=800.0),
    _tech_range("High ISO",      "high_iso",      "iso", lo=800.0, hi=3200.0),
    _tech_range("Very high ISO", "very_high_iso", "iso", lo=3200.0),

    # ── Flash ─────────────────────────────────────────────────────────────────
    _exact("Flash fired",   "flash_fired", "flash_fired", "1"),
    _exact("No flash",      "no_flash",    "flash_fired", "0"),

    # ── File format ───────────────────────────────────────────────────────────
    _in_list("RAW file", "raw_file", "file_format",
             ["raw", "arw", "cr2", "cr3", "nef", "orf", "raf", "rw2", "dng", "pef", "srw"]),
    _in_list("HEIC/HEIF", "heic", "file_format", ["heic", "heif"]),
    _in_list("Video",     "video", "file_format",
             ["mp4", "mov", "avi", "mkv", "m4v", "mts", "m2ts", "wmv", "flv", "webm", "3gp"]),
    _in_list("TIFF",      "tiff", "file_format", ["tif", "tiff"]),

    # ── GPS ───────────────────────────────────────────────────────────────────
    _exact("Geotagged", "geotagged", "gps_present", "true"),

    # ── Tonality (from file_quality.exposure) ─────────────────────────────────
    _quality_range("Low key",          "low_key",          "exposure",   hi=0.20),
    _quality_range("Standard exposure","standard_exposure","exposure",   lo=0.20, hi=0.70),
    _quality_range("High key",         "high_key",         "exposure",   lo=0.70),
    _quality_range("Blown highlights", "blown_highlights", "highlights", lo=0.15),
    _quality_range("Crushed shadows",  "crushed_shadows",  "shadows",    lo=0.15),

    # ── Contrast (from luminance_std_dev) ─────────────────────────────────────
    _quality_range("Flat lighting",      "flat_lighting",     "luminance_std_dev", hi=0.08),
    _quality_range("Moderate contrast",  "moderate_contrast", "luminance_std_dev", lo=0.08, hi=0.22),
    _quality_range("High contrast",      "high_contrast",     "luminance_std_dev", lo=0.22),

    # ── Saturation (from saturation_mean) ─────────────────────────────────────
    _quality_range("Black and white",     "black_and_white",     "saturation_mean", hi=0.05),
    _quality_range("Muted colours",       "muted",               "saturation_mean", lo=0.05, hi=0.25),
    _quality_range("Standard saturation", "standard_saturation", "saturation_mean", lo=0.25, hi=0.50),
    _quality_range("Vibrant",             "vibrant",             "saturation_mean", lo=0.50),

    # ── Silhouette (high shadow ratio) ────────────────────────────────────────
    _quality_range("Silhouette", "silhouette", "shadows", lo=0.60),

    # ── Dominant hue ──────────────────────────────────────────────────────────
    _quality_range("Warm tones",  "warm_tones",  "dominant_hue", lo=20.0,  hi=70.0),
    _quality_range("Green tones", "green_tones", "dominant_hue", lo=75.0,  hi=165.0),
    _quality_range("Cool tones",  "cool_tones",  "dominant_hue", lo=180.0, hi=270.0),
]
