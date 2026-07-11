# KB.AM1 — Knowledge Settings: Schema & Gating Engine

**Status:** Complete
**Preceding sprint:** KB.AL1 (Health Page Redesign, 1792 tests)
**Baseline:** 1797 tests passing, 2 skipped
**Result:** 1832 tests passing, 2 skipped (+35 net)
**Concept doc:** `sprints/planned/KNOWLEDGE_SETTINGS_CONCEPT.md`

## Goal

Introduce the People/Places/Dates domain-toggle schema and the gating engine
that reads it, and wire that engine into the stages that can be gated
without touching shared LLM-context code. No UI this sprint — API and CLI
only, matching how KB.P2 (incremental ingest via API) preceded KB.P3 (its
UI).

## Builds On

- `entity_match.py`'s existing `get_entity_tables(kb_conn)` loop (KB.P8) —
  filtering is additive, no restructuring.
- `classify.py`'s existing `category` column on `classify_rules` and the
  `if all_life_events:` guard (KB.4) — filtering is additive.
- `export.py::_write_people`'s `export_biometric: bool` gate — precedent for
  the shape of a domain toggle at the DB-helper/API layer.
- `src/pipeline/dag.py`'s `STAGE_GROUPS`/`DEPENDENCIES` — the new
  `STAGE_REQUIRES` table in `knowledge_gates.py` is a sibling lookup, not a
  replacement.

## Acceptance Criteria

### Schema
- New `knowledge.db` migration (next sequential number) creates
  `knowledge_settings(category TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)`.
- Migration seeds all three rows (`people`, `places`, `dates`) with
  `enabled = 1` — both for newly created KBs and for existing KBs picking up
  the migration, so no behavioural change occurs until a user acts.

### Gating engine — `src/pipeline/knowledge_gates.py`
- `STAGE_REQUIRES: dict[str, frozenset[str]]` per the concept doc (`face`,
  `face_meta`, `voice`, `voice_diarize`, `attribute_speakers` → `{people}`;
  `geolocate`, `geo_meta` → `{places}`; `temporal` → `{dates}`).
- `TAG_CATEGORY_REQUIRES: dict[str, frozenset[str]]` (`calendar`, `temporal`
  → `{dates}`; `life_event` → `{people, dates}`).
- `get_enabled_categories(kb_conn) -> frozenset[str]` — reads
  `knowledge_settings`, returns the set of enabled category keys.
- `stage_is_enabled(stage: str, enabled_categories: frozenset[str]) -> bool`
  — `True` if `stage` has no entry in `STAGE_REQUIRES`, or its required set
  is a subset of `enabled_categories`.

### Stage dispatch
- **Corrected during pre-sprint review:** there is no single CLI/API
  dispatch chokepoint for the eight `STAGE_REQUIRES` stages. `cli/pipeline.py::run()`
  only walks `resolve_plan("export", completed)`, which covers the
  ingest→export chain — `temporal` has its own CLI command
  (`cli/pipeline.py::temporal`) but `face`, `face_meta`, `voice`,
  `voice_diarize`, `attribute_speakers`, `geolocate`, and `geo_meta` aren't
  in `run()`'s `_stage_runners` dict at all and live as separate commands in
  other CLI modules. The API side does have one shared point
  (`_make_stage_routes` in `api/pipeline.py`), but relying on that alone
  would miss the CLI paths.
- **Revised approach:** the `stage_is_enabled()` check goes inside each
  gated stage function itself (`src/stages/face.py`, `face_meta.py`,
  `voice.py`, `voice_diarize` (module TBD — confirm exact filename during
  implementation), `attribute_speakers.py`, `geolocate.py`, `geo_meta.py`,
  `temporal.py`), at the very top, before any DB checkpoint work or model
  load. This guarantees enforcement regardless of which CLI command or API
  route reached it, and avoids duplicating the check at every call site —
  the same duplication risk already flagged for the `FileContext`/
  `search_text.csv` chokepoint in `KB.AM2`.
- No GPU/ML import or model load happens for a skipped stage — the
  early-return must precede those imports (most of these stages already
  lazy-import ML libraries inside the function body per the architecture's
  lazy-import rule, so an early return before that point is sufficient).
- Skipped runs must be distinguishable from "ran, zero files pending" in
  whatever status/progress signal the stage reports — exact mechanism
  (e.g. a `skipped_reason` field alongside the existing progress/status
  shape) to be finalized during implementation, since `ProgressReporter`
  currently has no concept of "skipped" (only `update`/`done`, plus an
  ad-hoc `failed` on the SSE reporter). `KB.AM3`'s badge logic depends on
  this signal existing, so get the shape right here rather than retrofitting
  it in `KB.AM3`.

### In-stage filtering
- `entity_match.py`: the `get_entity_tables()`-driven loop skips the
  `people` row when `people` is disabled and the `locations` row when
  `places` is disabled. Other registered entity tables are unaffected
  (custom tables are out of scope per the concept doc).
- `classify.py`: `rules` is filtered to exclude rules whose `category` is a
  key in `TAG_CATEGORY_REQUIRES` with an unmet requirement, before the main
  per-file loop. The `if all_life_events:` block additionally requires both
  `people` and `dates` enabled.

### DB helpers — `src/db/kb.py`
- `get_knowledge_settings(conn) -> dict[str, bool]`
- `set_knowledge_category_enabled(conn, category: str, enabled: bool) -> None`
  — validates `category` is one of `people`/`places`/`dates`.

### API (Pattern 3-shaped)
- `GET /api/kb/{name}/settings` → `{"people": bool, "places": bool, "dates": bool}`
- `POST /api/kb/{name}/settings` → body `{"category": str, "enabled": bool}`,
  returns the updated full settings object.

### CLI
- `enrich kb settings <name>` — prints current toggle values.
- `enrich kb set-setting <name> <category> <on|off>` — updates one category,
  following the flat-verb naming already used by `set-active`,
  `seed-registers`, `generate-taxonomy`.

## Design Authority Updates (required this sprint)

Pre-sprint review found two things that need resolving in `SPEC.md`, since
it's the canonical schema/UI reference and both are new information, not
just implementation detail:

1. **New table entry.** `SPEC.md`'s `knowledge.db` schema block (starting
   ~line 20) lists every table inline (`vocabulary`, `capture_rules`,
   `classify_rules`, etc.) but predates several tables added by later
   migrations (`stage_prompts`, `pattern_rules`, `vocab_proposals`,
   `taxonomy_proposals`) — this sprint is not responsible for backfilling
   that pre-existing drift, but must add `knowledge_settings` following the
   same inline format when it's introduced.
2. **Naming collision.** `SPEC.md`'s "UI Design" section (~line 1898)
   already lists a planned "Settings panel for config.yaml fields" — a
   different, not-yet-built concept (per-KB config like `date_resolution`,
   tool paths) unrelated to domain toggles. Nothing currently implements a
   `/settings` route, so there's no code conflict, but building a panel
   also called "Settings" would collide with that reserved name later. This
   sprint's UI-facing feature (surfaced in `KB.AM3`) should be labeled
   **"Knowledge Settings"** throughout — matching the `knowledge_settings`
   table name — and a one-line note added near `SPEC.md`'s existing bullet
   clarifying the two are distinct.

## Out of Scope (this sprint)

- Any change to `build_file_context`, `describe.py`, `suggest.py`,
  `summarize.py`, `retag.py`, or `export.py::_write_search_text` — that's
  `KB.AM2`. Disabling a domain this sprint stops the relevant stage/rules
  from running, but does not yet retroactively filter what already-generated
  LLM prompts or `search_text.csv` surface.
- Settings UI — `KB.AM3`.
- Custom entity table domain classification.

## Test Coverage Expectations

- Unit tests for `get_enabled_categories`, `stage_is_enabled`, and both new
  DB helpers, including the invalid-category rejection path.
- Unit tests for `classify.py`'s rule filtering (calendar rules excluded
  when dates off; life-events excluded when either people or dates off;
  technical/quality rules unaffected).
- Unit tests for `entity_match.py`'s table-skip filtering.
- Integration test covering: a stage in `STAGE_REQUIRES` reports "skipped"
  via the API when its domain is disabled, and runs normally when re-enabled
  (resume-on-restart style coverage per the working agreement's stage
  integration test requirement).
- Integration test for the settings API round-trip (GET reflects POST).

---

## What Was Built

### Schema
- `src/migrations/knowledge/0010_knowledge_settings.sql` — `knowledge_settings`
  table, seeded `people`/`places`/`dates` all `enabled=1`.

### Gating engine — `src/pipeline/knowledge_gates.py`
- `STAGE_REQUIRES`, `TAG_CATEGORY_REQUIRES`, `ALL_CATEGORIES` as planned.
- `get_enabled_categories(conn)`, `stage_is_enabled(stage, enabled)`,
  `tag_category_is_enabled(category, enabled)`.
- `report_stage_skipped(progress, stage, enabled_categories)` — added beyond
  the original plan to centralize the "skipped" signal (progress
  update/done call + `{"files_processed": 0, "skipped": True,
  "skipped_reason": str}` return dict) so all 8 gated stage functions emit
  an identical shape instead of each inventing its own. This is the concrete
  resolution of the "exact mechanism TBD" note in the original plan.

### DB helpers — `src/db/kb.py`
- `get_knowledge_settings(conn) -> dict[str, bool]`
- `set_knowledge_category_enabled(conn, category, enabled)` — raises
  `ValueError` on unknown category.

### In-stage filtering
- `entity_match.py`: `gps_tables`/`text_tables` now exclude the built-in
  `locations` row when `places` is disabled (custom GPS/text entity tables
  unaffected); the hardcoded people-name-matching block only populates
  `name_to_person` when `people` is enabled.
- `classify.py`: `rules` filtered through `tag_category_is_enabled` before
  the per-file loop; the life-events block additionally requires
  `{"people", "dates"}.issubset(enabled_categories)`.

### Early-skip gating — all 8 `STAGE_REQUIRES` stages
`face.py`, `face_meta.py`, `voice.py` (`run_voice` + `run_voice_diarize`),
`attribute_speakers.py`, `geolocate.py`, `geo_meta.py`, `temporal.py`. In
each, the check was placed **before** any config/model validation, not just
before model loading — `run_face`'s `ModelLoadError` checks for
`face_detection_model`/`face_embedding_model` were reordered to come
*after* the gate, since a user with People disabled shouldn't be forced to
configure a face model just to get a clean skip. `geolocate.py` and
`temporal.py` didn't previously open `knowledge.db` at all — both now open a
short-lived `kb_conn` solely to read the setting, then close it before
proceeding (or returning).

### API — `src/api/kb.py`
- `GET /{name}/settings`, `POST /{name}/settings` (body:
  `KnowledgeSettingsUpdate{category, enabled}`), both via the existing
  `_get_kb_folder` pattern used by `/stats`/`/health`/`/sets` — not
  `Depends(resolve_kb)`, which a different subset of `kb.py` endpoints uses.

### CLI — `src/cli/kb.py`
- `enrich kb settings <name>` and `enrich kb set-setting <name> <category> <on|off>`.

## Files Touched

| File | Change |
|---|---|
| `src/migrations/knowledge/0010_knowledge_settings.sql` | New |
| `src/pipeline/knowledge_gates.py` | New |
| `src/db/kb.py` | New `get_knowledge_settings`, `set_knowledge_category_enabled` |
| `src/stages/entity_match.py` | Places/people filtering in the GPS/text/name-match loops |
| `src/stages/classify.py` | Rule + life-event filtering |
| `src/stages/face.py`, `face_meta.py`, `voice.py`, `attribute_speakers.py`, `geolocate.py`, `geo_meta.py`, `temporal.py` | Early-skip gating |
| `src/api/kb.py` | New `KnowledgeSettingsUpdate` model, `GET`/`POST /{name}/settings` |
| `src/cli/kb.py` | New `settings`, `set-setting` commands |
| `SPEC.md` | New `knowledge_settings` schema entry; disambiguating note on the pre-existing "Settings panel for config.yaml" bullet |
| `tests/unit/test_knowledge_gates_unit.py` | New — 9 tests |
| `tests/integration/test_knowledge_settings_integration.py` | New — 8 tests (DB helpers + CLI) |
| `tests/integration/test_kb_settings_api.py` | New — 3 tests |
| `tests/integration/test_knowledge_settings_stage_skip.py` | New — 9 tests (all 8 gated stages + one "not skipped when enabled" sanity check) |
| `tests/integration/test_classify.py` | +4 tests (dates-off, life-event × people/dates combinations) |
| `tests/integration/test_entity_match.py` | +2 tests (people-off, places-off with custom-table isolation) |
| `tests/integration/test_schema.py` | `_KB_TABLES` updated with `knowledge_settings` |
| `tests/unit/test_geo_meta_unit.py` | Added `get_enabled_categories` mock to all `open_kb`-patching tests (see Issues Surfaced) |

## Test Coverage

+35 net tests (1797 → 1832). Every new function has direct unit or
integration coverage; every gated stage has a dedicated skip test that
proves the gate runs before model/config validation (not just before model
loading) by using an unconfigured `Config()` and asserting a clean skip
instead of `ModelLoadError`.

## Issues Surfaced

- **Pre-existing mock brittleness in `test_geo_meta_unit.py`.** Its unit
  tests pass a bare `MagicMock()` for `kb_conn`. `MagicMock` auto-configures
  `__iter__` to yield nothing, so `get_enabled_categories`'s
  `frozenset(r[0] for r in rows)` silently produced an empty set, which
  made every gated-stage check evaluate false and short-circuit — the tests
  failed not because the new code was wrong, but because the mock didn't
  model realistic DB content. Fixed by patching
  `get_enabled_categories` to return all-three-enabled in that file's
  shared `_PATCHES` fixture. Worth keeping in mind for any future stage
  that starts reading `knowledge.db` inside a function currently tested
  with an unconfigured `MagicMock`.
- **Two pre-existing, unrelated ruff findings** (`test_face_unit.py`:
  unused `MagicMock` import; `test_stage_runner.py`: unused `pytest`
  import). Confirmed via `git diff --stat` that neither file was touched
  this sprint — not fixed, since it's outside this sprint's scope, but
  flagged here rather than silently ignored.
- No open design questions remain — both discrepancies found during the
  pre-sprint ritual (naming collision, dispatch chokepoint) were resolved
  and documented in this file's Design Authority Updates and Stage Dispatch
  sections before implementation began.

## Next

`KB.AM2` — Context & Export Filtering (`build_file_context` +
`export.py::_write_search_text`). This is the isolated, higher-risk sprint
flagged in the concept doc; nothing in `KB.AM1` blocks it.
