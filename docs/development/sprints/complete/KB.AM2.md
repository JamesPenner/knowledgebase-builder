# KB.AM2 — Knowledge Settings: Context & Export Filtering

**Status:** Complete
**Preceding sprint:** KB.AM1 (Knowledge Settings: Schema & Gating Engine)
**Baseline:** 1832 tests passing, 2 skipped
**Result:** 1853 tests passing, 2 skipped (+21 net)
**Concept doc:** `sprints/planned/KNOWLEDGE_SETTINGS_CONCEPT.md`

## Goal

Close the two chokepoints identified in the entwinement survey: the shared
`FileContext` aggregator (`src/text/context.py`) and the independent
`search_text.csv` export query (`export.py::_write_search_text`) both mix
People/Places/Dates content with no filtering today. `KB.AM1` stops the
*generating* stages from running when a domain is off; this sprint stops
already-generated or cross-domain content from leaking into LLM prompts and
export output.

**This is deliberately isolated from `KB.AM1`.** It touches five existing
call sites (not four — see Pre-Sprint Review Findings) across
`describe.py`, `suggest.py` (twice), `summarize.py`, `retag.py`, plus
`export.py`, and is the highest-risk part of this feature — a signature
change to a module several stages depend on.

## Pre-Sprint Review Findings (confirmed against current code before implementation)

1. **Five call sites, not four.** `suggest.py` calls `build_file_context`
   twice: `_run_level_a` (line 131) and `_run_level_c` (line 336), both
   passing `kb_conn=None` as the second argument (vocab-term/captured-field
   lookups aren't needed for token extraction). This doesn't block the
   sprint — both `_run_level_a` and `_run_level_c` already receive a real
   `kb_conn` as a function parameter, so `enabled_categories` can be fetched
   from that real connection once before their per-file loops, independent
   of what gets passed into `build_file_context` itself. Full call site list:
   `describe.py:213`, `summarize.py:164`, `retag.py:106`,
   `suggest.py:131` (`_run_level_a`), `suggest.py:336` (`_run_level_c`).
   All five already have `kb_conn` open before their loop starts.
2. **`enabled_categories` must default to all-enabled, not be a bare
   required parameter.** `tests/unit/test_file_context.py` has ~15 existing
   test methods calling `build_file_context(corpus_db, kb_db, fid)` with the
   current 3-arg signature, none of which test domain filtering — they test
   description/transcript/summary assembly. Making the new parameter
   required would force touching every one of those unrelated tests. Signature
   becomes `build_file_context(corpus_conn, kb_conn, file_id, *,
   enabled_categories=ALL_CATEGORIES)` (keyword-only, imported from
   `src.pipeline.knowledge_gates`) — existing tests and any future caller
   that doesn't pass it get today's unfiltered behavior, matching current
   default behavior exactly.
3. **`get_file_derived_tags` has no `category` column today.** It currently
   returns `list[str]` (`SELECT tag FROM file_derived_tags WHERE
   file_id=?`), with no way to apply `TAG_CATEGORY_REQUIRES` filtering. It
   has exactly one caller (`context.py:81`), so its query changes to
   `SELECT tag, category FROM file_derived_tags WHERE file_id=?` returning
   `list[sqlite3.Row]`; `build_file_context` does the
   `tag_category_is_enabled` filtering and reduces to the plain
   `list[str]` `FileContext.derived_tags` still expects. No other caller to
   break.
4. **"Shared helper" between `FileContext` and `_write_search_text` means
   shared *mapping*, not shared *code path*.** `_write_search_text` is a
   single `GROUP_CONCAT`-based batch SQL query across every file in the KB;
   `build_file_context`'s filtering is inherently per-file Python
   (`tag_category_is_enabled` called per row for one file at a time).
   Forcing `_write_search_text` to call the same per-file Python function
   would mean N per-file queries replacing one batch query — a real
   performance regression on large corpora (KB.AK1's test KB has ~27k
   files). Instead: add two small pure helpers to
   `knowledge_gates.py` — `excluded_tag_categories(enabled) -> list[str]`
   and `excluded_entity_tables(enabled) -> list[str]` (e.g. returns
   `["calendar", "life_event"]` when `dates` is off) — and have **both**
   sides consult them: `build_file_context` uses the returned lists for
   Python `not in` checks, `_write_search_text` uses them to build a SQL
   `AND category NOT IN (...)` / `AND table_name NOT IN (...)` fragment.
   Same source of truth, no drift, no perf regression.

## Builds On

- `KB.AM1`'s `get_enabled_categories(kb_conn)` and `TAG_CATEGORY_REQUIRES`.
- `KB.S4`'s `FileContext`/`build_file_context()` (the module this sprint
  modifies).
- `attribute_speakers.py::_resolve_label`'s existing fallback-to-generic-label
  path — reused for the transcript-speaker-suppression case, not
  reimplemented.

## Acceptance Criteria

### `build_file_context()` signature
- Gains keyword-only `enabled_categories: frozenset[str] = ALL_CATEGORIES`
  parameter (default from `src.pipeline.knowledge_gates.ALL_CATEGORIES`).
  All five call sites (`describe.py:213`, `summarize.py:164`,
  `retag.py:106`, `suggest.py:131`, `suggest.py:336`) fetch it once per
  stage run (not once per file) via `get_enabled_categories(kb_conn)` using
  the `kb_conn` already open in each enclosing function, and pass it
  through.

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
  still suppresses stale calendar/life-event tags. Requires the
  `get_file_derived_tags` return-shape change noted in Pre-Sprint Review
  Findings #3.

### Export consolidation
- `excluded_tag_categories(enabled_categories)` and
  `excluded_entity_tables(enabled_categories)` added to
  `src/pipeline/knowledge_gates.py` as the single source of truth for what
  "people"/"places"/"dates" exclude (see Finding #4). `build_file_context`
  and `_write_search_text` each consult these, applying the exclusion in
  whatever form suits their access pattern (Python filter vs. SQL fragment)
  — not a shared code path, a shared mapping.
- `_write_search_text` is rewritten to build its `WHERE`/`GROUP_CONCAT`
  filters from `excluded_tag_categories`/`excluded_entity_tables` instead of
  its current unfiltered join, so `search_text.csv` and LLM-prompt context
  can no longer drift apart on what counts as "people" or "places" content.

## Out of Scope

- Settings UI — `KB.AM3`.
- Any change to `metadata_date` suppression behaviour.
- Any change to `coverage.csv` or other export files not identified in the
  entwinement survey as blending People/Places/Dates.

## Test Coverage Expectations

- Unit tests for `excluded_tag_categories`/`excluded_entity_tables` in
  isolation (pure functions, no DB).
- Integration tests for `build_file_context`'s filtering: each of the four
  content types (entity_names by table, location, transcript speaker
  labels, derived_tags by category) filtered correctly under every
  combination of the three toggles, plus the all-enabled no-op case
  (existing ~15 tests in `test_file_context.py` must keep passing unchanged
  since the new parameter defaults to all-enabled).
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

## What Was Built

### `src/pipeline/knowledge_gates.py`
- `excluded_tag_categories(enabled_categories) -> list[str]` and
  `excluded_entity_tables(enabled_categories) -> list[str]` — pure functions,
  the single source of truth both `build_file_context` and
  `_write_search_text` consult (per Finding #4).

### `src/db/corpus.py`
- `get_file_derived_tags`: now returns `list[sqlite3.Row]` with `tag,
  category` (was `list[str]`, tag only) — its one caller (`context.py`)
  updated in the same change.
- `get_file_transcript_segments`: now also selects `end_ms` — needed for the
  transcript-suppression time-overlap re-derivation below. Its one caller
  updated in the same change.

### `src/text/context.py`
- `build_file_context` gains keyword-only `enabled_categories: frozenset[str]
  = ALL_CATEGORIES`.
- `entity_names` filtered by `table_name` via `excluded_entity_tables` before
  the dedup step.
- `derived_tags` filtered by `tag_category_is_enabled` at read time.
- `metadata_location` blanked when `places` disabled; `metadata_date` left
  untouched regardless of `dates`, per the explicit scope decision.
- `transcript`: new `_generic_speaker_labels()` helper. **Not spelled out
  verbatim in the sprint plan, so documenting the mechanism here.**
  `transcript_segments.speaker_label` is overwritten in place by
  `attribute_speakers.py::set_transcript_segment_speaker` — once a segment
  is attributed, the pre-resolution "generic" label (cluster label or raw
  pyannote label) no longer exists anywhere in that row. To produce the
  fallback the plan calls for ("the same generic label `_resolve_label`
  already produces when no person match exists"), `_generic_speaker_labels`
  re-runs the same time-overlap match (`get_voice_segments_for_file` +
  `_best_overlap`, both imported from `attribute_speakers.py`) and calls
  `_resolve_label(best, {}, cluster_map)` with an empty `people_map`, which
  falls through to the cluster label or raw label exactly as it would have
  before any person was matched. This only runs per-file when a transcript
  has speaker labels **and** `people` is disabled — reuses existing logic
  rather than reimplementing it, as the "Builds On" section anticipated, but
  does mean two extra DB queries (voice segments for the file, all voice
  clusters in the KB) in that specific case. No schema change.

### `src/stages/export.py`
- `_write_search_text` gains `enabled_categories: frozenset[str] | None =
  None` (defaults to `ALL_CATEGORIES` inside the function — module-level
  default avoided since `export.py` has no `from __future__ import
  annotations` and the project's `X | None` syntax already assumes Python
  3.10+, matching the existing `section: str | None = None` parameter a few
  lines below it). Builds `AND fdt.category NOT IN (...)` /
  `AND fem.table_name NOT IN (...)` SQL fragments from the same
  `excluded_tag_categories`/`excluded_entity_tables` helpers `context.py`
  uses — shared mapping, not shared code path, per Finding #4.
- `run_export` fetches `enabled_categories` once via `get_enabled_categories`
  and passes it to `_write_search_text`.

### Five call sites
`describe.py:213`, `summarize.py:164`, `retag.py:106`, `suggest.py:131`
(`_run_level_a`), `suggest.py:336` (`_run_level_c`) — each enclosing function
fetches `enabled_categories = get_enabled_categories(kb_conn)` once before
its per-file loop and passes it through to `build_file_context`, exactly as
Finding #1 anticipated (no `kb_conn` plumbing needed beyond what already
existed).

## Files Touched

| File | Change |
|---|---|
| `src/pipeline/knowledge_gates.py` | New `excluded_tag_categories`, `excluded_entity_tables` |
| `src/db/corpus.py` | `get_file_derived_tags` shape change; `get_file_transcript_segments` gains `end_ms` |
| `src/text/context.py` | `enabled_categories` param + filtering on all four content types; new `_generic_speaker_labels` helper |
| `src/stages/describe.py`, `summarize.py`, `retag.py`, `suggest.py` | Fetch + thread `enabled_categories` through their `build_file_context` call(s) |
| `src/stages/export.py` | `_write_search_text` filtering; `run_export` fetches `enabled_categories` |
| `tests/unit/test_knowledge_gates_unit.py` | +8 tests (`excluded_tag_categories`, `excluded_entity_tables`) |
| `tests/unit/test_file_context.py` | +11 tests (entity table filtering, location/date filtering, derived-tag category filtering incl. stale-tag case, transcript speaker-label fallback) |
| `tests/integration/test_export.py` | +2 tests (`search_text.csv` category/table filtering) |

## Test Coverage

+21 net tests (1832 → 1853). Every new function (`excluded_tag_categories`,
`excluded_entity_tables`, `_generic_speaker_labels` via its observable
effect on `ctx.transcript`) has direct coverage; all four `build_file_context`
content types are tested under a non-default toggle combination plus the
implicit all-enabled default (the ~15 pre-existing `test_file_context.py`
tests, unchanged, continue to exercise that path). The `metadata_date`
non-suppression is covered by an explicit regression-guard test asserting
identical output across all-enabled, dates-off, and nothing-enabled.

## Issues Surfaced

- No critical issues requiring a stop-and-raise — the pre-sprint review
  (already completed in a prior session, see the file header) had already
  resolved the two ambiguities that would otherwise have blocked
  implementation (call-site count, `get_file_derived_tags` shape change).
- The transcript-suppression re-derivation (`_generic_speaker_labels`) is
  the one piece of implementation not fully spelled out in the acceptance
  criteria — documented above under "What Was Built" for future reference,
  since a future reader might otherwise assume the fallback label is read
  directly off `transcript_segments` rather than recomputed.
- Same two pre-existing, unrelated ruff findings noted in `KB.AM1`
  (`test_face_unit.py` unused `MagicMock` import, `test_stage_runner.py`
  unused `pytest` import) are still present. Confirmed neither file was
  touched this sprint — still out of scope, still flagged rather than
  silently ignored.

## Next

`KB.AM3` — Knowledge Settings UI panel + cascading gate badges. Backend is
now fully wired (generation gating from `KB.AM1`, surfacing/export
filtering from this sprint); `KB.AM3` is UI over an already-working
backend.
