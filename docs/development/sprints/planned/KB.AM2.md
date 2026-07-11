# KB.AM2 — Knowledge Settings: Context & Export Filtering

**Status:** Planned
**Preceding sprint:** KB.AM1 (Knowledge Settings: Schema & Gating Engine)
**Concept doc:** `sprints/planned/KNOWLEDGE_SETTINGS_CONCEPT.md`

## Goal

Close the two chokepoints identified in the entwinement survey: the shared
`FileContext` aggregator (`src/text/context.py`) and the independent
`search_text.csv` export query (`export.py::_write_search_text`) both mix
People/Places/Dates content with no filtering today. `KB.AM1` stops the
*generating* stages from running when a domain is off; this sprint stops
already-generated or cross-domain content from leaking into LLM prompts and
export output.

**This is deliberately isolated from `KB.AM1`.** It touches four existing
call sites (`describe.py`, `suggest.py`, `summarize.py`, `retag.py`) plus
`export.py`, and is the highest-risk part of this feature — a signature
change to a module several stages depend on. Per the working agreement, if
pre-sprint review turns up additional callers of `build_file_context` beyond
those four (e.g. a quick-describe CLI path), that's a discrepancy to confirm
before proceeding, not to silently absorb.

## Builds On

- `KB.AM1`'s `get_enabled_categories(kb_conn)` and `TAG_CATEGORY_REQUIRES`.
- `KB.S4`'s `FileContext`/`build_file_context()` (the module this sprint
  modifies).
- `attribute_speakers.py::_resolve_label`'s existing fallback-to-generic-label
  path — reused for the transcript-speaker-suppression case, not
  reimplemented.

## Acceptance Criteria

### `build_file_context()` signature
- Gains `enabled_categories: frozenset[str]` parameter. All four call sites
  (`describe.py`, `suggest.py`, `summarize.py`, `retag.py`) fetch it once per
  stage run (not once per file) via `get_enabled_categories(kb_conn)` and
  pass it through.

### Filtering behaviour
- `entity_names`: `get_entity_matches_for_file` results are filtered by
  `table_name` before dedup — `people`-table matches included only if
  `people` enabled, `locations`-table matches only if `places` enabled.
  Matches from any other registered entity table are unaffected.
- `metadata_location`: set to `None` when `places` disabled.
- `metadata_date`: **unchanged regardless of the `dates` setting** — this is
  a deliberate decision from the concept doc, not an oversight. Do not
  "fix" this to blank on `dates` disabled without re-confirming with the
  user first.
- `transcript`: when `people` is disabled, speaker-attributed segments fall
  back to the same generic label `_resolve_label` already produces when no
  person match exists, rather than a resolved `preferred_name`.
- `derived_tags`: filtered through `TAG_CATEGORY_REQUIRES` against
  `enabled_categories` at read time — independent of whatever `classify`
  did or didn't write, so a domain toggled off *after* `classify` last ran
  still suppresses stale calendar/life-event tags.

### Export consolidation
- The filtering logic above is extracted into a shared, independently
  testable helper (exact location/name decided at implementation time — a
  function in `src/text/context.py` or a small new module is both
  reasonable) used by **both** `build_file_context` and
  `export.py::_write_search_text`.
- `_write_search_text` is rewritten to call the shared helper instead of
  its own bespoke SQL join, so `search_text.csv` and LLM-prompt context can
  no longer drift apart on what counts as "people" or "places" content.

## Out of Scope

- Settings UI — `KB.AM3`.
- Any change to `metadata_date` suppression behaviour.
- Any change to `coverage.csv` or other export files not identified in the
  entwinement survey as blending People/Places/Dates.

## Test Coverage Expectations

- Unit tests for the shared filter helper: each of the four content types
  (entity_names by table, location, transcript speaker labels, derived_tags
  by category) filtered correctly under every combination of the three
  toggles, plus the all-enabled no-op case.
- Unit test proving `metadata_date` is populated identically regardless of
  the `dates` toggle — a regression guard for the explicit scope decision
  above.
- Unit test for the stale-tag case: a `life_event`-category tag already
  present in `file_derived_tags` is excluded from `FileContext` when
  `people` or `dates` is disabled, even though it was written while both
  were enabled.
- Integration tests for `describe`, `suggest`, `summarize`, and `retag`
  confirming generated prompts/output omit gated content when the relevant
  domain is off (manual validation only for the LLM call itself per the
  working agreement's GPU/LLM stage exemption — but the `FileContext`
  object each stage builds before calling the LLM is fully testable).
- Integration test for `search_text.csv` reflecting the same filtering as
  `FileContext` for an identical file, across all toggle combinations.
