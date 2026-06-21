DEPENDENCIES: dict[str, list[str]] = {
    "ingest":         [],
    "analyse":        ["ingest"],
    "normalize":      ["analyse"],
    "extract_meta":   ["normalize"],
    "extract_fields": ["extract_meta"],
    "entity_match":   ["extract_fields"],
    "classify":       ["entity_match"],
    "hash":           ["normalize"],
    "describe":       ["hash"],
    "transcribe":     ["hash"],
    "suggest":        ["describe", "transcribe"],
    "retag":          ["suggest"],
    "writeback":      ["retag"],
    "export":         ["writeback"],
    "aesthetic":      ["ingest"],
    "quality":        ["ingest"],
    "temporal":       ["classify"],
    "face":           ["ingest"],
    "voice":               ["ingest"],
    "voice_diarize":       ["ingest"],
    "attribute_speakers":  ["transcribe", "voice_diarize"],
    "geolocate":           ["extract_fields"],
}

# Maps stage name → touchpoint that must be completed before that stage runs.
TOUCHPOINT_BEFORE: dict[str, str] = {
    "normalize": "normalise_review",
    "retag":     "suggest_review",
    "writeback": "new_terms_review",
}

TOUCHPOINTS: set[str] = set(TOUCHPOINT_BEFORE.values())

# Maps stage → downstream stages that are invalidated when the stage is re-run with --force.
INVALIDATES: dict[str, list[str]] = {
    "ingest":         [],
    "analyse":        [],
    "normalize":      ["describe", "suggest", "retag"],
    "extract_meta":   ["extract_fields", "entity_match"],
    "extract_fields": ["entity_match", "classify", "suggest", "retag"],
    "entity_match":   ["classify"],
    "classify":       [],
    "hash":           [],
    "describe":       ["suggest", "retag"],
    "transcribe":     ["suggest", "retag"],
    "suggest":        ["retag"],
    "retag":          ["writeback"],
    "writeback":      [],
    "export":         [],
    "aesthetic":      [],
    "quality":        [],
    "temporal":       [],
    "face":           [],
    "voice":              [],
    "voice_diarize":      [],
    "attribute_speakers": [],
    "geolocate":          [],
}


def resolve_plan(target: str, completed: set[str]) -> list:
    if target not in DEPENDENCIES:
        raise ValueError(f"Unknown stage: {target!r}")

    ordered: list = []
    _visit(target, completed, ordered, set())
    return ordered


def _visit(stage: str, completed: set[str], ordered: list, seen: set[str]) -> None:
    if stage in seen:
        return
    seen.add(stage)

    for dep in DEPENDENCIES.get(stage, []):
        _visit(dep, completed, ordered, seen)

    touchpoint = TOUCHPOINT_BEFORE.get(stage)
    if touchpoint and touchpoint not in completed:
        entry = {"touchpoint": touchpoint}
        if entry not in ordered:
            ordered.append(entry)

    if stage not in completed:
        ordered.append(stage)
