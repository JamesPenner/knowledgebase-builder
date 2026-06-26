# Sprint KB.V1 — Pipeline Workbench: Sources + Scope Restructure

## Context

The pipeline page currently has a single `wb-scope` block that conflates three
distinct concepts: what files exist in the KB (sources), which files to process
in this run (scope filters), and how to process them (resume vs re-run). Sources
management is additionally buried inside the ingest stage's help row — a
mechanism intended for stage descriptions, not functional UI.

This sprint separates the three concepts cleanly, promotes source management to
a first-class collapsible header at the top of the pipeline, and introduces a
shared `FilterSpec` architecture so source-level and scope-level filters use the
same schema and UI component.

## Design

The top of the pipeline page becomes three distinct sections:

```
╔═══════════════════════════════════════════════════════════════╗
║ ▾ Sources   2 sources · 4,219 files ingested                  ║  ← collapsible
║   [source table + add-source form + saved sets]               ║
╠═══════════════════════════════════════════════════════════════╣
║ Source: [All sources ▾]   Type: [All ▾]   Set: [None ▾]       ║  ← always visible
╠═══════════════════════════════════════════════════════════════╣
║ Mode: [Resume] [Re-run]   [Run all]  [Run selected]           ║  ← always visible
╚═══════════════════════════════════════════════════════════════╝
  stage rows, each with [Run] [↺ Resume] split button
```

**Sources header** — collapsible, persistent config, set-and-forget. Shows count
chip and file total when collapsed. Auto-expands when 0 sources are configured.
Body contains the existing HTMX sources panel partial (unchanged content).

**Scope bar** — always visible, per-run filter selection. Source selector only
appears when ≥2 sources are configured. Set selector only appears when sets exist.

**Run mode + actions** — Resume/Re-run toggle applies globally to all stages.
Each stage row has a split button: `[Run]` runs with current mode, `[↺]` cycles
the per-stage mode override without affecting the global setting. Ingest's split
button uses `[Full scan]` / `[Incremental]` instead of Resume/Re-run (maps to
the existing `incremental` flag on `IngestRunRequest`).

## FilterSpec — Shared Filter Architecture

A new `FilterSpec` dataclass in `src/pipeline/filter_spec.py` defines the
canonical filter schema used at both levels:

```python
@dataclass
class FilterSpec:
    file_type: str = "all"          # all | images | video | audio
    glob: str | None = None         # fnmatch on filename
    count_limit: int | None = None  # truncate to first N
    modified_after: str | None = None   # ISO date string; compare fs mtime
    exclude_patterns: list[str] = field(default_factory=list)  # skip matching path components
```

Source-level adds `recursive: bool` (filesystem traversal).
Scope-level adds `source_id`, `set_id` (corpus-level selectors — already in `get_pending_*`).

The `FilterSpec` is consumed by `apply_source_filters()` (filesystem scan, ingest-time)
and serialised into `filters_json` when a source is added.

## Backend Changes

### 1. `src/pipeline/filter_spec.py` (new)
- `FilterSpec` dataclass as above
- `FilterSpec.from_dict(d: dict) -> FilterSpec` — deserialise from `filters_json`
- `FilterSpec.to_dict() -> dict` — serialise for storage

### 2. `src/stages/ingest.py`
- `apply_source_filters(files, filters)` gains two new filter stages:
  - `modified_after`: convert ISO string to timestamp, compare `file.stat().st_mtime`
  - `exclude_patterns`: skip any file whose path components match any pattern via `fnmatch`
- Existing `glob` and `count_limit` logic unchanged
- Internal: accept either `dict` or `FilterSpec`; normalise at entry

### 3. `src/api/kb.py`
- `SourceCreateRequest` gains `modified_after: str | None = None` and
  `exclude_patterns: list[str] = []`
- `kb_add_source`: serialise new fields into `filters_json` via `FilterSpec`
- `kb_preview_source`: apply new filter fields during filesystem scan
- No endpoint URL changes

### 4. `src/api/pipeline.py`
- `RunRequest`: rename `scope_mode` → `run_mode: str = "resume"` (values: `resume` | `rerun` only)
  — `by_source`, `by_type`, `by_set`, `new_files` modes removed; `source_id`,
  `file_type`, `set_id` are now independent filters applied unconditionally when present
- All `_*_runner` functions: apply `source_id`/`file_type`/`set_id` unconditionally
  (not gated on `scope_mode`); `rerun` resets the stage before running
- `IngestRunRequest` unchanged — `incremental: bool` already exists
- `_buildBody` in `pipeline.js` sends `run_mode` not `scope_mode`; ingest sends
  `incremental` from its per-stage mode toggle

## Frontend Changes

### 5. `templates/pipeline.html`
- **Remove** `<div class="wb-scope">` block entirely (scope mode select, scope type
  checkboxes, scope source select, scope set select, scope summary)
- **Add** above the stage groups, in order:
  1. `<div class="wb-sources" id="wb-sources">` — sources header + collapsible body
     (body contains HTMX load of `/api/kb/{name}/sources/panel` same as now)
  2. `<div class="wb-scope-bar">` — source filter select + type filter select +
     set filter select (each conditional on count > 1 / non-empty)
  3. Update `<div class="wb-actions">` — add resume/re-run mode toggle buttons
- **Stage rows**: Run button column becomes a split pair:
  `<div class="stage-run-split">` containing `[Run]` + `[mode-toggle]` buttons
- **Ingest help row**: remove HTMX sources panel; replace with the stage description
  string from `STAGE_DESCRIPTIONS["ingest"]` (same pattern as all other stages)
- Remove `ingest_no_sources` special-case logic (lines 132–134 in current file)

### 6. `templates/partials/sources_panel.html`
- Add `modified_after` date input to the add-source form row (after glob input)
- Add `exclude_patterns` text input (comma-separated, placeholder: `@eaDir, #recycle`)
- Include both fields in `hx-include` attribute list on the form

### 7. `static/js/workbench.js`
- **`getScope()`** — return `{run_mode, source_id, file_type, set_id}`;
  `source_id` and `file_type` come from the new scope bar selects directly
  (not gated on a mode)
- **`onScopeChange()`** — reads new scope bar elements; updates `window.KB_SCOPE`
- **`WB.toggleSources()`** — expand/collapse `#wb-sources-body`; persist state in
  `localStorage` keyed by `kb-sources-open-{kb_name}`; update toggle arrow
- **`WB.setAllModes(mode)`** — set global run mode; update all stage mode buttons
  to match; mark global mode buttons active
- **`WB.toggleStageMode(stage)`** — cycle per-stage mode override;
  `_stageModes[stage]` stores the override; does not affect global or other stages
- **`WB.getStageMode(stage)`** — return `_stageModes[stage]` if set, else global mode
- **Init** (`DOMContentLoaded`): auto-expand sources if `window.KB_SOURCES.length === 0`;
  restore sources panel open state from localStorage; populate scope bar source/set
  selects from `window.KB_SOURCES` / `window.KB_SETS` (replaces old `_loadSources` /
  `_loadSets` that populated the old scope dropdown)

### 8. `static/js/pipeline.js`
- **`_buildBody(stage, kb)`**: spread `window.KB_SCOPE` which now has `run_mode`
  (not `scope_mode`); for ingest, send `incremental: WB.getStageMode('ingest') === 'incremental'`
  and omit `run_mode`; for all other stages, send `run_mode: WB.getStageMode(stage)`

### 9. `static/css/main.css`
New classes:
- `.wb-sources` — bordered container, same visual weight as `.wb-group`
- `.wb-sources-header` — flex row, cursor pointer, padding, `#f8fafc` background
- `.wb-sources-chips` — flex gap for chip badges
- `.wb-sources-chip` — small rounded badge (count / file total)
- `.wb-sources-chip--warning` — amber colouring for "No sources" state
- `.wb-sources-body` — collapsible wrapper
- `.wb-scope-bar` — flex row, same card style as `.wb-scope` (reuse or rename)
- `.wb-scope-bar-item` — label + select pair
- `.wb-run-mode` — inline button group for Resume / Re-run
- `.wb-mode-btn` — mode toggle button; `.wb-mode-btn--active` for selected state
- `.stage-run-split` — flex pair for `[Run]` + `[mode-toggle]`
- `.stage-run-mode` — the mode indicator button in the split pair

Remove `.wb-scope`, `.wb-scope-controls`, `.wb-scope-label`, `.wb-scope-select`,
`.wb-scope-types`, `.wb-scope-summary` (replaced by new classes above).

## No-Change Surfaces

- `src/db/corpus.py` — all `get_pending_*` functions unchanged; `source_id`,
  `file_type`, `set_id` params already exist and are now always applied
- All 7 sources/sets API endpoints (`POST/DELETE /{name}/sources`,
  `GET /{name}/sources/panel`, `GET/POST/DELETE /{name}/sets`)
- `src/pipeline/dag.py` — `STAGE_DESCRIPTIONS["ingest"]` is already defined
- No new database migration — `modified_after` and `exclude_patterns` go into
  the existing `filters_json` column on `sources`

## Tests

### New unit tests — `tests/unit/test_source_filters.py` (+3)
- `test_modified_after_excludes_old_files()` — files with mtime before threshold filtered out
- `test_modified_after_passes_new_files()` — files newer than threshold pass through
- `test_exclude_patterns_skips_matching_components()` — `@eaDir` in path excluded;
  non-matching paths pass

### New integration tests — `tests/integration/test_source_api.py` (+3)
- `test_add_source_with_modified_after()` — stored in `filters_json`
- `test_add_source_with_exclude_patterns()` — stored in `filters_json`
- `test_preview_source_with_exclude_patterns()` — preview skips matching files

### New integration tests — `tests/integration/test_pipeline_run_mode.py` (new file, +6)
- `test_run_request_run_mode_default_resume()` — `run_mode` defaults to "resume"
- `test_run_request_scope_filters_applied_independently()` — `source_id` + `file_type`
  filter without needing `scope_mode`
- `test_rerun_mode_resets_describe()` — `run_mode: "rerun"` triggers reset before run
- `test_rerun_mode_resets_quality()` — same for quality runner
- `test_source_id_filter_limits_files()` — only files from the specified source processed
- `test_file_type_filter_limits_files()` — only files of the specified type processed

### Existing test updates
- `tests/integration/test_source_management.py` — update any assertions that send
  `scope_mode` to instead send `run_mode`
- Remove any tests that assert `by_source` / `by_type` / `by_set` as `scope_mode` values

## Acceptance Criteria

1. `python -m pytest tests/ -q` — all tests pass; count ≥ 1350
2. `ruff check src/ tests/` — clean
3. Pipeline page with 0 sources: sources block auto-expanded, "No sources configured"
   chip visible, add-source form showing `modified_after` and `exclude_patterns` fields
4. Add a source: block collapses, chip updates to "1 source"
5. ≥2 sources: source selector appears in scope bar; 1 source: selector hidden
6. Global mode toggle "Re-run" updates all stage split buttons; switching one stage
   back to "Resume" does not affect other stages
7. Ingest split button shows "Full scan" / "Incremental" (not Resume/Re-run)
8. Running a stage sends `run_mode` (not `scope_mode`) in the request body
9. Ingest help row shows the stage description text (same as all other stages)
10. Sources panel HTMX refresh still works after add/remove operations

## Target Test Count

+18 new tests → **1350 passing**
