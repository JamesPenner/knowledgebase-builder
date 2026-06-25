DEPENDENCIES: dict[str, list[str]] = {
    "ingest":         [],
    "analyse":        ["ingest"],
    "normalize":      ["analyse"],
    "extract_meta":   ["normalize"],
    "extract_fields": ["extract_meta"],
    "entity_match":   ["extract_fields"],
    "classify":       ["entity_match"],
    "hash":           ["normalize"],
    "validate":       ["hash"],
    "describe":       ["hash"],
    "transcribe":     ["hash"],
    "summarize":      ["describe", "transcribe"],
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
    "validate":       [],
    "describe":       ["suggest", "retag", "summarize"],
    "transcribe":     ["suggest", "retag", "summarize"],
    "summarize":      ["suggest"],
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

STAGE_GROUPS: list[dict] = [
    {
        "id": "discovery",
        "label": "Discovery",
        "description": "Sets up what the corpus knows about",
        "stages": ["ingest", "analyse"],
    },
    {
        "id": "metadata",
        "label": "Metadata",
        "description": "Fast, no ML — structural information",
        "stages": ["normalize", "extract_meta", "extract_fields", "hash", "validate", "temporal"],
    },
    {
        "id": "ml_analysis",
        "label": "ML Analysis",
        "description": "Slow, GPU-bound — content understanding",
        "stages": ["describe", "transcribe", "summarize", "quality", "aesthetic", "face", "voice", "voice_diarize"],
    },
    {
        "id": "enrichment",
        "label": "Enrichment",
        "description": "Synthesis — cross-reference with knowledge base",
        "stages": ["entity_match", "classify", "geolocate", "attribute_speakers"],
    },
    {
        "id": "vocabulary",
        "label": "Vocabulary",
        "description": "Knowledge-building against review queues",
        "stages": ["suggest", "retag"],
    },
    {
        "id": "output",
        "label": "Output",
        "description": "Finalise and deliver",
        "stages": ["writeback", "export"],
    },
]

STAGE_DESCRIPTIONS: dict[str, str] = {
    "ingest":             "Discovers and registers files from configured source folders",
    "analyse":            "Tokenises filenames and existing descriptions into searchable terms",
    "normalize":          "Applies approved normalisation decisions to the token vocabulary",
    "extract_meta":       "Reads EXIF and file-system metadata for every file",
    "extract_fields":     "Maps raw EXIF tags to canonical knowledge-base fields",
    "hash":               "Computes perceptual and cryptographic hashes for deduplication",
    "validate":           "Checks that corpus files still exist and have not changed since ingest",
    "temporal":           "Derives time-based classifications (season, time of day, day of week)",
    "describe":           "Generates AI descriptions of image and video content (requires GPU)",
    "transcribe":         "Transcribes speech in audio and video files",
    "summarize":          "Produces a one-sentence summary combining description and transcript",
    "quality":            "Scores technical quality: sharpness, exposure, highlights, shadows",
    "aesthetic":          "Scores visual aesthetic quality using a neural network (requires GPU)",
    "face":               "Detects and clusters faces for people identification (requires GPU)",
    "voice":              "Embeds speaker voice samples for identity matching",
    "voice_diarize":      "Segments audio by speaker using diarization",
    "entity_match":       "Links file metadata to registered locations, people, and events",
    "classify":           "Applies classification rules to assign domain-specific tags",
    "geolocate":          "Reverse-geocodes GPS coordinates to place names",
    "attribute_speakers": "Assigns speaker identities to transcript segments",
    "suggest":            "Proposes new vocabulary terms from descriptions and transcripts",
    "retag":              "Refines tags using LLM review against approved vocabulary (requires GPU)",
    "writeback":          "Writes approved metadata back to files via ExifTool",
    "export":             "Bundles the knowledge base and corpus data into export files",
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
