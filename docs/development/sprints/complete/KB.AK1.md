# KB.AK1 — Corpus File Browser

**Status:** Complete
**Branch:** `clean-master`
**Baseline:** 1755 tests passing, 2 skipped
**Result:** 1786 tests passing, 2 skipped (+31 net)
**Preceding sprint:** KB.AJ2 (Face/Voice Review: Centroid Quality Focus, 1755 tests)

## Goal

Source concept: `docs/development/sprints/planned/UI_REDESIGN_CONCEPT.md` §5 —
a paginated, filterable file list, the missing piece for the "select specific
files for a focused pipeline run" exploratory-seeding workflow. Also named in
`VISION.md`'s Componentized Media Management vision as one of the four
natural sibling-app concepts ("a general-purpose media library browser").
The query layer was built general-purpose rather than narrowly shaped to this
page, as a concrete first step toward the shared-library architecture
direction, without extracting anything yet.

## Scope Decision (confirmed with user before implementation)

**"Use as scope" is filter-based, not selection-based.** The pipeline's scope
system (`CorpusFilterSpec`, `RunRequest`, the workbench's scope bar) only
understands declarative criteria (source/type/date-range/name-pattern) —
there is no file-ID-list dimension anywhere. The one prior mechanism for that
(`file_set_members`) was deliberately removed in migration `0024` in favor of
pure criteria-based sets. Rather than reopening that architecture, "Use as
scope" pushes the browser's active filter panel (the same six
`CorpusFilterSpec` fields) into the workbench's scope bar — zero new
plumbing. Row checkboxes / arbitrary multi-select are **out of scope**
(no such UI pattern exists anywhere in the codebase, and it wouldn't feed
into anything without the file-ID-list scope mechanism this decision defers).

## Corrections to the concept doc's premises (confirmed via investigation)

- The doc said join `corpus_files` — the actual table is `files`.
- The doc said join `pipeline_checkpoints` for per-file state — that table is
  stage-aggregate only (one row per stage, not per file). Per-file state
  comes from `LEFT JOIN`s against each stage's own table, following
  `get_coverage_per_file()`'s established shape.
- "Captured date" isn't a `files` column — it requires a join to
  `file_metadata_fields WHERE canonical_name='captured_date'` (sparse; grouped
  by `file_id` with `MIN(value)` to avoid row fan-out, since that table has no
  uniqueness constraint on `(file_id, canonical_name)`).
- `file_type` values are `'images'` (plural), `'video'`, `'audio'`.
- No schema changes were needed.

## What Was Built

### `src/db/corpus.py`

- `get_files_for_browser(conn, spec, *, state, sort_by, sort_order, limit,
  offset)` — paginated, filtered, sorted file listing joined to
  `descriptions`/`transcriptions` for stage flags, `sources` for source path,
  and the grouped `file_metadata_fields` subquery for captured date.
- `count_files_for_browser(conn, spec, *, state)` — matching count query.
- Private `_browser_where(spec, state)` shared by both, always excluding
  duplicates (`f.canonical_id IS NULL`) per existing `get_pending_*`
  convention. `state` accepts `None | "described" | "not_described" |
  "transcribed" | "not_transcribed" | "hashed" | "not_hashed"`.

### `src/api/kb.py`

- `GET /{name}/files` — JSON endpoint, `{"items": [...], "total": int}`,
  following the `_get_kb_folder` + path-param convention of sibling
  `/sets`/`/folders`/`/sources` routes, extended with `limit`/`offset`
  pagination. This is also the shape a future sibling app would call.

### `src/api/ui.py`

- `GET /corpus-files` — page shell, `Depends(resolve_kb)` + `?kb=` pattern
  matching `corpus_stats_page`.
- `GET /corpus-files/partials/list` — HTMX partial for filter changes and
  Load More pagination. Re-renders the *entire* filter bar (not just the
  table) on every request, so it fetches `sources` fresh each time — missing
  this on the first pass left the Source `<select>` empty after any reload
  (caught during manual verification against the real 27k-file `test-run`
  KB; fixed and covered by a regression test).

### Templates

- `templates/corpus_files.html` — page shell extending `base.html`.
- `templates/partials/file_browser_list.html` — filter panel (source, folder,
  type, date range, name pattern, state) + results table (Filename, Type,
  Source, Size, Captured Date, Described/Transcribed/Hashed badges) + "Use as
  scope" button. Reuses `.wb-scope-bar*`/`.stage-table`/`.badge*` CSS classes
  as-is — no new CSS.

### `static/js/review.js`

Extended `ReviewQueue` with two backward-compatible additions (existing
call sites — `suggest_review.html`, `normalise_review.html` — are unaffected):
- `opts.getExtraParams` — optional callback whose return value is appended
  to the reload URL, so filter state survives Load More / column-sort clicks.
- `init()` now returns `{ reload }`, so a page can trigger a reload from
  outside the module (needed for filter-panel `onchange`/`oninput` handlers,
  which `ReviewQueue`'s built-in delegated listeners don't cover).

### `static/js/corpus_files.js`

New `CF` namespace: `getFilters()` reads the six filter inputs + state
dropdown; `onFilterChange()` debounces (300ms) and calls the queue's
`reload()`; `useAsScope()` writes the current filter state to
`localStorage['kb-scope-' + kb]` in the exact shape `workbench.js`'s
`_persistScope()`/`_initScopeBar()` already read, then redirects to
`/pipeline?kb={kb}`.

### `templates/base.html`

"Files" nav link added to the Corpus section, before Stats.

## Files Touched

| File | Change |
|---|---|
| `src/db/corpus.py` | New `get_files_for_browser`, `count_files_for_browser`, `_browser_where` |
| `src/api/kb.py` | New `GET /{name}/files` |
| `src/api/ui.py` | New `GET /corpus-files` page + `GET /corpus-files/partials/list` |
| `templates/corpus_files.html` | New page |
| `templates/partials/file_browser_list.html` | New partial |
| `templates/base.html` | Nav link |
| `static/js/review.js` | `ReviewQueue`: `getExtraParams` option, `reload()` return value |
| `static/js/corpus_files.js` | New — filter state + "Use as scope" |
| `tests/unit/test_corpus_file_browser_unit.py` | New — 19 tests |
| `tests/integration/test_corpus_file_browser_integration.py` | New — 12 tests |

## Test Coverage

+31 net tests: unit coverage for every `CorpusFilterSpec` dimension
individually and combined, all 6 `state` values, duplicate exclusion, sort
columns (including unknown-column fallback) + direction, pagination, and the
empty-corpus case; integration coverage for the JSON API (shape, pagination,
filters, state, sort), the page load, the HTMX partial (row rendering, Load
More threshold, empty state, nav link), and a regression test for the
source-dropdown-repopulation bug found during manual verification.

## Manual Verification

Ran the dev server against the real `test-run` KB (27,136 files, 26,093 after
duplicate exclusion). Confirmed: page loads, partial renders real rows in
~0.9s, filter panel round-trips selected values correctly, `file_type=video`
filter narrows results correctly, Load More button appears/disappears
correctly at the total-count boundary. This is also where the source-dropdown
bug (`sources` missing from the partial's template context) was caught.

## Out of Scope — Deferred

- **File-ID-list scope mechanism** — row checkboxes / arbitrary multi-select
  selection would require reopening the criteria-only scope architecture
  from migration `0024`. Not attempted this sprint; flagged for a future
  design session if the filter-based "Use as scope" proves insufficient.
- Thumbnails — deferred until the opportunistic frame cache (FrameSet Option
  B) is evaluated, per the original concept doc.
- Remaining `UI_REDESIGN_CONCEPT.md` items: Vocabulary Review Improvements,
  Export Page Framing.
