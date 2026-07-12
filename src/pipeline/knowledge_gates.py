from __future__ import annotations

import sqlite3

STAGE_REQUIRES: dict[str, frozenset[str]] = {
    "face": frozenset({"people"}),
    "face_meta": frozenset({"people"}),
    "voice": frozenset({"people"}),
    "voice_diarize": frozenset({"people"}),
    "attribute_speakers": frozenset({"people"}),
    "geolocate": frozenset({"places"}),
    "geo_meta": frozenset({"places"}),
    "temporal": frozenset({"dates"}),
}

TAG_CATEGORY_REQUIRES: dict[str, frozenset[str]] = {
    "calendar": frozenset({"dates"}),
    "temporal": frozenset({"dates"}),
    "life_event": frozenset({"people", "dates"}),
}

ALL_CATEGORIES: frozenset[str] = frozenset({"people", "places", "dates"})


def get_enabled_categories(conn: sqlite3.Connection) -> frozenset[str]:
    """Return the set of enabled knowledge-domain categories for this KB."""
    rows = conn.execute("SELECT category FROM knowledge_settings WHERE enabled = 1").fetchall()
    return frozenset(r[0] for r in rows)


def stage_is_enabled(stage: str, enabled_categories: frozenset[str]) -> bool:
    """True if `stage` has no domain requirement, or its requirement is fully met."""
    required = STAGE_REQUIRES.get(stage)
    if required is None:
        return True
    return required.issubset(enabled_categories)


def tag_category_is_enabled(category: str, enabled_categories: frozenset[str]) -> bool:
    """True if a derived-tag category (e.g. 'calendar', 'life_event') is allowed to surface."""
    required = TAG_CATEGORY_REQUIRES.get(category)
    if required is None:
        return True
    return required.issubset(enabled_categories)


def excluded_tag_categories(enabled_categories: frozenset[str]) -> list[str]:
    """Derived-tag categories that must NOT surface given the enabled domains."""
    return sorted(
        category
        for category, required in TAG_CATEGORY_REQUIRES.items()
        if not required.issubset(enabled_categories)
    )


def excluded_entity_tables(enabled_categories: frozenset[str]) -> list[str]:
    """Built-in entity tables (people/locations) that must NOT surface given the enabled domains."""
    excluded = []
    if "people" not in enabled_categories:
        excluded.append("people")
    if "places" not in enabled_categories:
        excluded.append("locations")
    return excluded


def report_stage_skipped(progress, stage: str, enabled_categories: frozenset[str]) -> dict:
    """Signal a gated stage was skipped and return its standard skip result dict.

    Used by every stage function listed in STAGE_REQUIRES as its early-return
    when the required knowledge domain(s) are disabled. `progress` only needs
    `update`/`done` (the ProgressReporter protocol).
    """
    missing = sorted(STAGE_REQUIRES.get(stage, frozenset()) - enabled_categories)
    reason = f"requires {', '.join(missing)}, which {'is' if len(missing) == 1 else 'are'} disabled"
    progress.update(0, 0, f"Skipped — {reason}")
    progress.done()
    return {"files_processed": 0, "skipped": True, "skipped_reason": reason}
