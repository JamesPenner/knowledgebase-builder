# Sprint KB.T1 — Pipeline Workbench

## Goal

Replace the flat 25-row pipeline table with a grouped, interactive workbench.
Users can see stages organised by function, understand dependency state at a
glance, and run any selection of stages in dependency order with a single click.
Also wires `validate` into the DAG and API (it has been CLI-only since KB.P23).

Scope selector (By source / By file type) and browser-side scheduling are
deferred to KB.T2 to keep this sprint focused on the structural UI rewrite.

---

## Acceptance Criteria

### AC1 — validate wired into DAG and API

**dag.py**
- Add `"validate": ["hash"]` to `DEPENDENCIES` (sha256 must be populated for
  move detection to work)
- Add `"validate": []` to `INVALIDATES`

**pipeline.py**
- Add `_validate_runner(corpus_path, kb_path, config, progress, cancel)` that
  calls `run_validate(corpus_path, kb_path.parent, progress, cancel)` — uses
  `kb_path.parent` as the KB folder; ignores `config` (validate has none);
  does not pass `export=True` (export is CLI-only)
- Register with `_make_stage_routes("validate", _validate_runner)`

---

### AC2 — STAGE_GROUPS and STAGE_DESCRIPTIONS added to dag.py

`STAGE_GROUPS: list[dict]` — each entry:
```
{
  "id":          str,    # kebab-case identifier
  "label":       str,    # display name
  "description": str,    # one-line group description
  "stages":      list[str],
}
```

Groups (in order):

| id | label | stages |
|---|---|---|
| discovery | Discovery | ingest, analyse |
| metadata | Metadata | normalize, extract_meta, extract_fields, hash, validate, temporal |
| ml_analysis | ML Analysis | describe, transcribe, summarize, quality, aesthetic, face, voice, voice_diarize |
| enrichment | Enrichment | entity_match, classify, geolocate, attribute_speakers |
| vocabulary | Vocabulary | suggest, retag |
| output | Output | writeback, export |

`STAGE_DESCRIPTIONS: dict[str, str]` — one sentence per stage for inline help:

| stage | description |
|---|---|
| ingest | Discovers and registers files from configured source folders |
| analyse | Tokenises filenames and existing descriptions into searchable terms |
| normalize | Applies approved normalisation decisions to the token vocabulary |
| extract_meta | Reads EXIF and file-system metadata for every file |
| extract_fields | Maps raw EXIF tags to canonical knowledge-base fields |
| hash | Computes perceptual and cryptographic hashes for deduplication |
| validate | Checks that corpus files still exist and have not changed since ingest |
| temporal | Derives time-based classifications (season, time of day, day of week) |
| describe | Generates AI descriptions of image and video content (requires GPU) |
| transcribe | Transcribes speech in audio and video files |
| summarize | Produces a one-sentence summary combining description and transcript |
| quality | Scores technical quality: sharpness, exposure, highlights, shadows |
| aesthetic | Scores visual aesthetic quality using a neural network (requires GPU) |
| face | Detects and clusters faces for people identification (requires GPU) |
| voice | Embeds speaker voice samples for identity matching |
| voice_diarize | Segments audio by speaker using diarization |
| entity_match | Links file metadata to registered locations, people, and events |
| classify | Applies classification rules to assign domain-specific tags |
| geolocate | Reverse-geocodes GPS coordinates to place names |
| attribute_speakers | Assigns speaker identities to transcript segments |
| suggest | Proposes new vocabulary terms from descriptions and transcripts |
| retag | Refines tags using LLM review against approved vocabulary (requires GPU) |
| writeback | Writes approved metadata back to files via ExifTool |
| export | Bundles the knowledge base and corpus data into export files |

---

### AC3 — resolve-plan API endpoint

`POST /api/stages/resolve-plan`

Request body:
```json
{ "stages": ["describe", "quality"], "completed": ["ingest", "analyse", "hash"] }
```

Response:
```json
{ "plan": ["describe", "quality"] }
```

- Uses the existing `resolve_plan()` from `dag.py`
- Runs plan resolution for each requested stage and merges the results in order
- Touchpoint entries in the plan are included in the response as
  `{"touchpoint": "normalise_review"}` objects (same format as `resolve_plan`)
- If any stage name is unknown, returns 422 with a descriptive error

---

### AC4 — ui.py pipeline_page updated

`pipeline_page()` passes to the template:

- `groups`: list of group dicts from STAGE_GROUPS, each enriched with:
  - `stages`: list of stage dicts (name, description, checkpoint, done count,
    total count, dependency state)
  - Dependency state per stage: `"done"` if checkpoint exists, `"ready"` if all
    deps are done and stage is pending, `"blocked"` if any dep is pending/missing
- `touchpoints`: dict of touchpoint name → `{"completed": bool, "url": str}` for
  normalise_review, suggest_review, new_terms_review
- `kb`: KB name (unchanged)

Touchpoint completion logic:
- `normalise_review`: completed if there are no pending analyse tokens (i.e.
  all tokens have a decision). Use existing `get_analyse_token_counts()`.
- `suggest_review`: completed if there are no pending vocab candidates. Use
  `get_pending_candidates()`.
- `new_terms_review`: completed if there are no pending new-terms decisions.
  Use the same decisions query used by the new-terms page.

---

### AC5 — pipeline.html rewrite

Structure:

```
[Run selected]  [Run all]         ← top action bar (Run selected disabled when nothing checked)

┌─ Discovery ──────────────── [Run group] ─┐
│ ☐ ingest    done   2026-06-01   412/412   [Run] [Cancel]  ▸ help  │
│ ☐ analyse   done   2026-06-01   412/412   [Run] [Cancel]  ▸ help  │
└──────────────────────────────────────────┘

┄ Normalise Review — approve vocabulary terms before continuing  [Go to review →]  ✓/pending

┌─ Metadata ───────────────── [Run group] ─┐
│ ☐ normalize    ready  —   —   [Run] [Cancel]  ▸ help  │
│ ☐ extract_meta blocked  —   —  ⬡ blocked: normalize  │
│ ...
└──────────────────────────────────────────┘
...
```

Details:
- Group header: label, description, "Run group" button
- Gate banner between groups where a touchpoint exists: label, one-liner, link
  to review page, status indicator (done/pending)
- Each stage row: checkbox (id=`check-{stage}`), stage name, status badge
  (`done` / `ready` / `blocked`), last-run date, files done/total, Run/Cancel
  buttons (same as current), progress span, inline help disclosure
- Inline help disclosure (`▸` toggle): expands to show the STAGE_DESCRIPTIONS
  sentence and the list of dependencies (derived from DEPENDENCIES)
- "Run selected" button: disabled when no checkboxes are checked; enabled
  whenever ≥ 1 is checked
- `blocked` rows: Run button is disabled; a note shows which dep is pending
- `done` rows: Run button remains enabled (allows re-run with force later)
- Gates appear at the correct positions per TOUCHPOINT_BEFORE:
  - Before normalize (after Discovery group)
  - Before retag (within Vocabulary group, after suggest)
  - Before writeback (after Vocabulary group, before Output group)

---

### AC6 — pipeline.js updated for multi-stage orchestration

New functions:
- `runSelected()` — collects all checked stage names, calls
  `POST /api/stages/resolve-plan` with current `kb` and checked stages +
  current checkpoints, then runs the returned plan stages sequentially
  (each stage completes its SSE stream before the next starts)
- `runGroup(groupId)` — checks all stages in the group and calls `runSelected()`
- `runAll()` — checks all stages and calls `runSelected()`
- `toggleHelp(stage)` — shows/hides the inline help disclosure

Existing functions (`runStage`, `cancelStage`, SSE event handling) remain
unchanged. Multi-stage run just calls `runStage` in sequence.

The "Run selected" button is wired to `onchange` on all checkboxes to
enable/disable the button.

Checkpoints (completed stages) are available in the page as a JSON object
injected from the server (`window.KB_CHECKPOINTS`), used by the resolve-plan
call to pass `completed`.

---

## Files Changed

| File | Change |
|---|---|
| `src/pipeline/dag.py` | Add validate to DEPENDENCIES/INVALIDATES; add STAGE_GROUPS, STAGE_DESCRIPTIONS |
| `src/api/pipeline.py` | Add `_validate_runner`, register via `_make_stage_routes` |
| `src/api/pipeline.py` | Add `POST /api/stages/resolve-plan` endpoint |
| `src/api/ui.py` | Update `pipeline_page()` to pass groups + touchpoint state |
| `templates/pipeline.html` | Complete rewrite — grouped workbench layout |
| `static/js/pipeline.js` | Add multi-stage orchestration functions |

No new migrations. No schema changes. No new templates.

---

## Tests

Target: **+25 tests** (1249 total)

### Unit — `tests/unit/test_dag.py` (extend existing)
- `test_validate_in_dependencies` — `"validate"` in `DEPENDENCIES`, deps = `["hash"]`
- `test_validate_in_invalidates` — `"validate"` in `INVALIDATES`
- `test_stage_groups_covers_all_dag_stages` — every key in DEPENDENCIES appears
  in exactly one STAGE_GROUPS entry (or is deliberately absent — normalize is
  in metadata group, so all should be covered)
- `test_stage_descriptions_covers_all_stages` — every key in DEPENDENCIES has
  an entry in STAGE_DESCRIPTIONS
- `test_resolve_plan_includes_validate` — `resolve_plan("validate", set())`
  includes `"hash"` and `"validate"` in the right order

### Integration — `tests/integration/test_pipeline_workbench.py` (new)
- `test_validate_run_endpoint` — POST /api/stages/validate/run returns started
- `test_validate_cancel_endpoint` — POST /api/stages/validate/cancel returns cancelled
- `test_validate_status_endpoint` — GET /api/stages/validate/status returns idle
- `test_resolve_plan_endpoint_single_stage` — POST /api/stages/resolve-plan with
  `stages=["analyse"]` returns plan containing ingest + analyse in order
- `test_resolve_plan_endpoint_multi_stage` — POST with `["describe", "quality"]`
  returns plan with hash before describe
- `test_resolve_plan_endpoint_with_completed` — POST with completed=["ingest"]
  omits ingest from returned plan
- `test_resolve_plan_endpoint_unknown_stage` — POST with unknown stage returns 422
- `test_pipeline_page_includes_groups` — GET /pipeline?kb=... response contains
  group labels (Discovery, Metadata, ML Analysis, Enrichment, Vocabulary, Output)
- `test_pipeline_page_touchpoint_state` — response context has `touchpoints` dict
  with normalise_review, suggest_review, new_terms_review keys
- `test_pipeline_page_stage_dependency_state` — a stage whose deps are all done
  has state "ready"; a stage whose deps are not done has state "blocked"
- `test_pipeline_page_validate_in_metadata_group` — validate appears in the
  metadata group in the response context

---

## Out of Scope (→ KB.T2)

- Scope selector: Resume / Re-run / New files / By source / By file type
- Browser-side scheduling (datetime picker + setTimeout)
- Filter params on `get_pending_*` DB helpers
- `GET /api/kb/{name}/sources` endpoint
