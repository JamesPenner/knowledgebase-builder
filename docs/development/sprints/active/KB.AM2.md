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
