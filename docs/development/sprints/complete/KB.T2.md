# Sprint KB.T2 — Scope Selector

## Status: Complete

**Tests:** 1275 passing, 2 skipped, ruff clean (+25 net)

---

## What Was Built

A scope selector on the Pipeline Workbench that controls which files each stage acts on.
Five scope modes implemented: Resume (default), Re-run, New files, By source, By file type.

### AC1 — `GET /api/kb/{name}/sources`
New endpoint in `src/api/kb.py` returning active source folders (id, path, file_count)
for the "By source" dropdown. Uses existing `get_sources()` helper.

### AC2 — Filter params on `get_pending_*` functions
Six functions in `src/db/corpus.py` extended with `source_id` and/or `file_type` optional
keyword params using `(? IS NULL OR f.col = ?)` pattern — no dynamic SQL, no breaking change:
- `get_pending_describe_files` — source_id + file_type
- `get_pending_transcribe_files` — source_id + file_type
- `get_pending_quality_files` — source_id + file_type
- `get_pending_aesthetic_files` — source_id + file_type
- `get_pending_summarize_files` — source_id only
- `get_pending_retag_files` — source_id only

### AC3 — Scope fields on RunRequest
`RunRequest` in `src/api/pipeline.py` gains `scope_mode`, `source_id`, `file_type`.
`SummarizeRunRequest` gains `scope_mode` and `source_id`.
`_make_stage_routes` extracts scope from request and passes `scope=dict` to runner functions.

### AC4 — Runner scope implementation
Non-scoped runners: `_analyse_runner`, `_normalize_runner`, `_extract_meta_runner`,
`_extract_fields_runner`, `_hash_runner`, `_entity_match_runner`, `_classify_runner`,
`_temporal_runner`, `_geolocate_runner`, `_validate_runner`, `_writeback_runner` — all
receive `**_` to absorb the scope kwarg.

Fully scoped runners (reset + filter): `_describe_runner`, `_transcribe_runner`,
`_quality_runner`, `_aesthetic_runner`, `_retag_runner`.

Stage functions updated to accept `source_id`/`file_type` keyword args:
`run_describe`, `run_transcribe`, `run_quality`, `run_aesthetic`, `run_retag`, `run_summarize`.

Re-run mode calls the appropriate existing reset function before running the stage.

### AC5 — Scope selector UI
Scope bar added to `templates/pipeline.html` above the action bar:
- `#scope-mode` dropdown (Resume / Re-run / New files / By source / By file type)
- `#scope-source` dropdown (hidden unless mode=by_source; populated from `window.KB_SOURCES`)
- `#scope-type` checkboxes image/video/audio (hidden unless mode=by_type)
- `#scope-summary` one-line plain-English description of current scope
- `window.KB_SOURCES` injected from server-side context

### AC6 — JS scope state
`static/js/workbench.js`:
- `WB.getScope()` reads DOM state → `{scope_mode, source_id, file_type}`
- `WB.onScopeChange()` shows/hides secondary controls and updates summary
- `_loadSources()` populates source dropdown from `window.KB_SOURCES` on DOMContentLoaded
- `_runPlan()` updated: reads scope at start, handles new_files mode (prepend ingest,
  remove ingest from effectiveCompleted), stores result in `window.KB_SCOPE`

`static/js/pipeline.js`:
- `_buildBody(stage, kb)` reads `window.KB_SCOPE` and spreads it into the request body
- For ingest + new_files mode: adds `incremental: true` automatically
- CSS added to `static/css/main.css` for `.wb-scope`, `.wb-scope-controls`, etc.

---

## Files Changed

| File | Change |
|---|---|
| `src/db/corpus.py` | Filter params on 6 `get_pending_*` functions |
| `src/api/kb.py` | `GET /api/kb/{name}/sources` endpoint |
| `src/api/pipeline.py` | Scope fields on RunRequest/SummarizeRunRequest; scope wiring in runners |
| `src/api/ui.py` | Pass `sources` list to `pipeline_page()` context |
| `src/stages/describe.py` | `run_describe` accepts source_id, file_type kwargs |
| `src/stages/transcribe.py` | `run_transcribe` accepts source_id, file_type kwargs |
| `src/stages/quality.py` | `run_quality` accepts source_id, file_type kwargs |
| `src/stages/aesthetic.py` | `run_aesthetic` accepts source_id, file_type kwargs |
| `src/stages/retag.py` | `run_retag` accepts source_id kwarg |
| `src/stages/summarize.py` | `run_summarize` accepts source_id kwarg |
| `templates/pipeline.html` | Scope selector bar + `window.KB_SOURCES` injection |
| `static/js/workbench.js` | Scope state management, source loading, new_files plan logic |
| `static/js/pipeline.js` | `_buildBody` spreads `window.KB_SCOPE` |
| `static/css/main.css` | `.wb-scope` styles |
| `tests/unit/test_corpus_scope_filters.py` | 13 new unit tests |
| `tests/integration/test_scope_selector.py` | 12 new integration tests |

---

## Notes

- "Re-run" is implemented for the 5 ML Analysis runners that have existing reset functions.
  Non-ML runners accept scope but ignore it (existing behavior is already idempotent for most).
- Face, voice, and diarize runners accept scope kwarg but do not filter pending files in T2.
  Blanket re-run for face would reset cluster assignments — deferred to a later sprint.
- Browser-side scheduling (datetime picker) remains deferred (→ KB.T3 or later).
