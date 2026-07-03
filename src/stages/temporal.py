"""Stage: Temporal Field Derivation — derives year/decade/season/time-of-day/holiday from EXIF dates."""
import calendar as _cal
import datetime as _dt
import json
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

# Season bands (Northern Hemisphere)
_SEASON: dict[int, str] = {}
for _m in [3, 4, 5]:   _SEASON[_m] = "Spring"
for _m in [6, 7, 8]:   _SEASON[_m] = "Summer"
for _m in [9, 10, 11]: _SEASON[_m] = "Autumn"
for _m in [12, 1, 2]:  _SEASON[_m] = "Winter"

# Time-of-day bands (lo, hi inclusive, label) — matches builtin classify rules
_TOD_BANDS = [
    (0,  4,  "overnight"),
    (5,  5,  "blue_hour_morning"),
    (6,  7,  "golden_hour_morning"),
    (8,  10, "morning"),
    (11, 13, "midday"),
    (14, 16, "afternoon"),
    (17, 18, "golden_hour_evening"),
    (19, 19, "blue_hour_evening"),
    (20, 23, "night"),
]


def _parse_dt(s: str) -> tuple[str | None, int | None]:
    """Return (ISO-date-str YYYY-MM-DD, hour-int-or-None) from an EXIF or captured date string.

    Handles ExifTool format "YYYY:MM:DD HH:MM:SS" and ISO "YYYY-MM-DDTHH:MM:SS".
    Returns (None, None) when the string is not parseable.
    """
    s = s.strip()
    if len(s) < 10:
        return None, None
    # ExifTool uses colons as date separators: normalise first 10 chars
    date_part = s[:10].replace(":", "-")
    parts = date_part.split("-")
    if len(parts) < 3:
        return None, None
    try:
        int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None, None

    hour: int | None = None
    if len(s) >= 13 and s[10] in ("T", " "):
        try:
            hour = int(s[11:13])
        except ValueError:
            pass
    return date_part, hour


def _holiday(year: int, month: int, day: int) -> str | None:
    """Return a holiday name if the date matches a builtin calendar rule, else None."""
    from src.stages.classify_rules import BUILTIN_RULES, evaluate_rule

    fields = {
        "file_date": f"{year:04d}-{month:02d}-{day:02d}",
        "file_date_precision": "full",
    }
    for rule in BUILTIN_RULES:
        if rule.get("category") != "calendar":
            continue
        mt = rule["match_type"]
        if mt == "month_range":
            continue  # seasons, not holidays
        if mt == "computed":
            cfg = json.loads(rule.get("match_config") or "{}")
            if cfg.get("algorithm") in ("decade", "panoramic"):
                continue
        tag = evaluate_rule(rule, fields)
        if tag:
            return tag
    return None


def derive_temporal_fields(
    exif_date_taken: str | None,
    file_date: str | None = None,
) -> dict:
    """Pure derivation — returns a dict of temporal fields (absent keys = unknown).

    Prefers exif_date_taken (has time component); falls back to file_date for date portion.
    """
    date_str: str | None = None
    hour: int | None = None

    if exif_date_taken:
        date_str, hour = _parse_dt(exif_date_taken)

    if not date_str and file_date:
        date_str, hour_fb = _parse_dt(file_date)
        if hour is None:
            hour = hour_fb

    if not date_str:
        return {}

    parts = date_str.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) >= 2 else None
        day = int(parts[2][:2]) if len(parts) >= 3 else None
    except (ValueError, IndexError):
        return {}

    out: dict = {
        "year": year,
        "decade": f"{(year // 10) * 10}s",
    }

    if month:
        out["month_name"] = _cal.month_name[month]
        out["season"] = _SEASON.get(month)

    if month and day:
        try:
            out["day_name"] = _dt.date(year, month, day).strftime("%A")
            out["holiday"] = _holiday(year, month, day)
        except ValueError:
            pass

    if hour is not None:
        for lo, hi, label in _TOD_BANDS:
            if lo <= hour <= hi:
                out["time_of_day"] = label
                break

    return out


_BATCH = 500


def run_temporal(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    scope=None,
) -> None:
    from src.db.corpus import open_corpus, update_pipeline_checkpoint, upsert_temporal_fields
    from src.pipeline.filter_spec import CorpusFilterSpec
    from src.pipeline.stage_runner import run_stage_loop

    conn = open_corpus(corpus_path)
    spec = scope or CorpusFilterSpec()
    scope_frag, scope_params = spec.to_sql_fragment()
    try:
        rows = conn.execute(
            f"""
            SELECT f.id,
                   MAX(CASE WHEN fmf.canonical_name = 'exif_date_taken' THEN fmf.value END) AS exif_date_taken,
                   MAX(CASE WHEN fcf.field_name     = 'file_date'        THEN fcf.value END) AS file_date
            FROM files f
            LEFT JOIN file_metadata_fields fmf ON fmf.file_id = f.id
            LEFT JOIN file_captured_fields fcf ON fcf.file_id = f.id
            WHERE NOT EXISTS (
                SELECT 1 FROM file_temporal_fields ft WHERE ft.file_id = f.id
            ){scope_frag}
            GROUP BY f.id
            ORDER BY f.id
            """,
            scope_params,
        ).fetchall()

        batch_count = [0]

        def _process(row):
            fields = derive_temporal_fields(row["exif_date_taken"], row["file_date"])
            upsert_temporal_fields(conn, row["id"], fields)
            batch_count[0] += 1
            if batch_count[0] % _BATCH == 0:
                conn.commit()

        processed, errors = run_stage_loop(rows, _process, progress, cancel_event, label="temporal")
        conn.commit()
        update_pipeline_checkpoint(conn, "temporal", processed, 0, errors)
        conn.commit()
        logger.info("Temporal: derived fields for %d files (%d errors)", processed, errors)
    finally:
        conn.close()
