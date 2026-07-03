# KB.AG2 — Structural Deduplication

**Status:** Active  
**Branch:** `clean-master`  
**Baseline:** 1645 tests passing  
**Target:** ≥1665 tests passing  
**Preceding sprint:** KB.AG1 (Correctness & Safety, 1645 tests)

## Goal

Eliminate exact code duplication across the stage layer, DB helpers, API handlers, CSS, and templates. No new user-facing behaviour — the goal is a codebase where each piece of logic exists in exactly one place.

---

## Acceptance Criteria

### A — Stage Layer: Shared Utility Extraction

**A1. Extract `cosine_similarity` and `update_centroid` to `src/pipeline/embeddings.py`**

`face.py` and `voice.py` each define their own copy of `cosine_similarity` (identical cosine distance over `float32` byte blobs) and `update_centroid` (identical running-mean centroid update — voice.py even documents this in a comment). Extract both to a new `src/pipeline/embeddings.py`. Both stage files import from there. Callers are unchanged.

**A2. Consolidate `_write_gps_clusters`**

`gps_cluster.py` and `export.py` each define an identical `_write_gps_clusters` function. Keep it in `gps_cluster.py`. `export.py` imports and calls it.

**A3. Consolidate `_write_validation_report`**

`validate.py` and `export.py` each define an identical `_write_validation_report` function. Keep it in `validate.py`. `export.py` imports and calls it.

**A4. Extract model-config guard in `vocab_llm.py`**

`vocab_llm.py` repeats `if not config.text_model: logger.warning(...); return None` identically in all four public functions (`suggest_synonyms`, `suggest_semantic_groupings`, `suggest_thematic_groupings`, `suggest_taxonomy`). Extract to a module-level `_require_text_model(config) -> bool` helper. Each function calls it at the top and returns early if `False`.

**A5. Rename `cancel` → `cancel_event` in stage entry-point signatures**

Six entry-point functions use `cancel` as the parameter name instead of the canonical `cancel_event`. Rename in: `face.py:run_face`, `voice.py:run_voice`, `voice.py:run_voice_diarize`, `face_meta.py:run_face_meta`, `attribute_speakers.py:run_attribute_speakers`, `geo_meta.py:run_geo_meta`, `aesthetic.py:run_aesthetic`. No logic changes — pure naming fix.

**A6. Extract `has_speech` + `prepare_audio` guard block**

`transcribe.py`, `voice.py:run_voice`, and `voice.py:run_voice_diarize` each contain an identical ~14-line block: check `get_has_speech`, call `prepare_audio`, update `has_speech` in DB, detect clipping, skip if `has_speech is False`. Extract to `prepare_audio_guarded(corpus_conn, file_id, file_path, config, logger)` in `src/media/audiotrack.py`. Returns the `AudioTrack` or `None` (caller `continue`s on `None`). All three callers use the new function.

---

### B — DB Layer: Deduplication

**B1. Extract `_configure` to `src/db/utils.py`**

`corpus.py` and `kb.py` define byte-for-byte identical `_configure(conn)` functions. Extract to a new `src/db/utils.py`. Both files import from there. No callers outside these two files.

**B2. Add missing single-row lookup helpers to `corpus.py`**

These queries appear inline in both `ui.py` and `review.py` (up to 6 occurrences each) with no named DB function:

- `get_analyse_token_by_id(conn, token_id: int) -> str | None` — `SELECT token FROM analyse_tokens WHERE id=?`
- `get_candidate_by_id(conn, candidate_id: int) -> dict | None` — `SELECT id, term FROM candidates WHERE id=?`
- `get_file_path_by_id(conn, file_id: int) -> str | None` — `SELECT path FROM files WHERE id=?`

Replace all inline occurrences in `ui.py` and `review.py` with calls to these helpers.

**B3. Unify face/voice centroid update helpers in `kb.py`**

`update_face_centroid`, `update_face_centroid_with_spread`, and `update_voice_centroid` are three variants of the same UPDATE on the `people` table. Replace all three with:

```python
def update_person_centroid(
    conn, person_id: int, blob: bytes, samples: int,
    *, kind: Literal["face", "voice"], spread: float | None = None
) -> None
```

Update all callers in `face.py`, `voice.py`, `face_meta.py`, `review.py`, `ui.py`.

**B4. Unify face/voice centroid read helpers in `kb.py`**

`get_people_with_centroids` and `get_people_with_voice_centroids` are the same SELECT with different column names. Replace both with:

```python
def get_people_with_centroids(conn, kind: Literal["face", "voice"]) -> list[dict]
```

Update callers in `face.py` and `voice.py`.

---

### C — API Layer: Handler Deduplication

**C1. Delete duplicate speaker handler functions in `ui.py`**

Four `_new`-suffixed handlers are byte-for-byte copies of existing handlers:

| To delete | Already exists |
|---|---|
| `speaker_queue_partial_new` | `speaker_queue_partial` |
| `speaker_decisions_partial_new` | `speaker_decisions_partial` |
| `ui_speaker_decide_new` | `ui_speaker_decide` |
| `ui_speaker_unassign_new` | `ui_speaker_unassign` |

Wire the `/knowledge/people/speakers/…` partial and POST routes directly to the existing functions using additional `@router.get`/`@router.post` decorators on the same function. Confirm no template references the `_new` suffixed function names (they're used only as route handlers).

**C2. Add `_err_html` and `_ok_html` helpers in `ui.py`**

Approximately 20 handlers return an inline error `Response` with `color:#f87171` and 20 more return a success `Response` with `color:#4ade80` plus an optional `HX-Trigger` header, all formatted identically. Extract two private helpers:

```python
def _err_html(msg: str) -> Response: ...
def _ok_html(msg: str, trigger: str | None = None) -> Response: ...
```

Update all inline `Response(content="<p style='color:…'>…</p>", …)` call sites. Private to `ui.py`.

---

### D — CSS: Duplicate and Dead Rules

**D1. Merge duplicate badge class pairs**

Two pairs of rules are byte-for-byte identical in `main.css`:
- `.badge--ignore` and `.badge--ignored` — consolidate to `.badge--ignore`, remove `.badge--ignored`, update any template that uses `.badge--ignored`
- `.badge-done` and `.badge--accept` — consolidate to `.badge--accept`, remove `.badge-done`, update templates

**D2. Consolidate duplicate button classes**

- Remove `.btn-primary` (standalone class that duplicates `.btn` + `.btn--capture`). Update every template using `class="btn-primary"` to `class="btn btn--capture"`.
- Remove standalone `.btn-danger` (line ~565). Update templates using `class="btn-danger"` to `class="btn btn--danger"`.
- Three size-modifier aliases (`.btn--sm`, `.btn--small`, `.btn-sm`) all set identical properties. Keep `.btn--xs` and `.btn--sm`. Remove `.btn--small` and `.btn-sm`. Update the one template (`pattern_rule_list.html`) that uses `btn--small` to `btn--xs`, matching `vocabulary_list.html`.

**D3. Remove unused / empty CSS rules**

- `.nav-kb` — no template reference (templates use `.nav-kb-select`)
- `.sources-save-set` and `.sources-save-label` — no template reference
- `.wb-sets-panel { }` — empty rule, no properties; remove

**D4. Add missing CSS rules referenced in templates**

These classes appear in templates but are not defined in CSS:
- `.stage-row--done` — add; used in `corpus_stats.html` (green tint matching `.badge--accept` intent)
- `.col-term` — add column-width rule; referenced in `candidates_queue.html` (match `.col-files` pattern)
- `.token-count` — change the reference in `new_terms_queue.html` to `.token-freq` (the defined equivalent class), OR add `.token-count` as an alias

**D5. Add badge variant rules for vocabulary proposals**

`vocabulary_proposals.html` uses `badge--llm` and `badge--entity` with inline styles as fallback. Add both to `main.css` as proper rules, removing the inline `style=` overrides.

---

### E — Templates and JS: Targeted Fixes

**E1. Fix empty `hx-confirm=""` in `sources_panel.html`**

Line 39 uses `hx-confirm=""` which triggers a blank browser confirm dialog. Change to `hx-confirm="Remove this source?"`.

**E2. Fix `hx-put` + `hx-get` conflict in `prompt_library.html`**

The Activate button has both `hx-post` and `hx-get` on the same element. The `hx-get` is a leftover. Remove it.

**E3. Add btn class to unassign buttons**

`face_clusters_assigned.html` and `speaker_clusters_decisions.html` render `<button type="submit">Unassign</button>` with no CSS class. Add `class="btn btn--danger"` to both.

**E4. Extract `.page-subtitle` CSS class**

`pattern_rules.html:5` and `vocabulary.html:5` share identical inline style (`color:#64748b;margin-bottom:1.25rem;font-size:.9rem`). Add `.page-subtitle` to `main.css`; replace both inline `style=` occurrences.

**E5. Extract `.form-field-input` CSS class**

`pattern_rule_form.html` repeats the same inline style on 9+ `<input>`/`<select>` elements; `registry_edit_form.html` repeats it 4 times (same properties, same values). Add `.form-field-input` to `main.css`; replace all 13 inline `style=` occurrences.

**E6. Replace `runLevelC`/`cancelLevelC` with `WB.runStage`/`WB.cancelStage`**

`suggest_review.html` defines local `runLevelC()` and `cancelLevelC()` that duplicate the run/cancel SSE pattern already in `pipeline.js`. Verify `runStage`/`cancelStage` are accessible on the `WB` namespace (or export them), then replace the local definitions with calls to the shared functions. Remove the ~30 lines of local duplicate logic.

**E7. Refactor `toggleSources`/`toggleSets` in `workbench.js`**

`toggleSources`, `toggleSets`, `_initSources`, and `_initSets` are four near-identical functions sharing the same collapse/expand + arrow-char + localStorage pattern. Extract to a single `_makeCollapsible(bodyId, arrowId, storageKey, defaultOpen)` factory function; wire both pairs to call it.

---

## Out of Scope — Deferred to Future Sprints

The following patterns were identified in the audit but are excluded from KB.AG2:

| Item | Reason for deferral |
|---|---|
| Stage main loop abstraction (`StageRunner`) | Blast radius: 22 files, high regression risk; deserves its own sprint |
| `try/finally` enforcement in 9 unsafe stages | Bundled with loop abstraction |
| Batch commit magic-number standardisation | Bundled with loop abstraction |
| Import style standardisation (top-level vs deferred) | Low value, high churn |
| Action-dispatch logic shared between `ui.py` and `review.py` | Requires a service-layer design decision |
| Page-two-col layout abstraction (5 templates + CSS) | UI restructure sprint |
| Face/speaker review pages extending `review_base.html` | UI restructure sprint |
| Backend: return `display_count` / `gate_action` / confidence level | API/template boundary design needed |
| CSS custom properties / design tokens | Significant CSS restructure; plan separately |
| `prompt_library.html` full CSS extraction | Full page rewrite; plan separately |
| `hx-post` vs `hx-delete` for unassign standardisation | Part of HTMX policy decision sprint |
| `htmx.ajax()` vs `htmx.trigger()` standardisation | Part of HTMX policy decision sprint |
| Fetch error-handling standardisation across JS files | Low risk, large surface area; deferred |

---

## Files Touched

| File | Change |
|---|---|
| `src/pipeline/embeddings.py` | **New** — `cosine_similarity`, `update_centroid` |
| `src/db/utils.py` | **New** — `_configure` |
| `src/stages/face.py` | Import from `embeddings.py`; rename `cancel` → `cancel_event` |
| `src/stages/voice.py` | Import from `embeddings.py`; use `prepare_audio_guarded`; rename `cancel` → `cancel_event` |
| `src/stages/transcribe.py` | Use `prepare_audio_guarded` |
| `src/stages/gps_cluster.py` | Keep `_write_gps_clusters` (export.py now imports it) |
| `src/stages/validate.py` | Keep `_write_validation_report` (export.py now imports it) |
| `src/stages/export.py` | Import `_write_gps_clusters`, `_write_validation_report` from sibling stages |
| `src/stages/vocab_llm.py` | Extract `_require_text_model`; call in 4 functions |
| `src/stages/face_meta.py` | Rename `cancel` → `cancel_event`; update centroid calls |
| `src/stages/attribute_speakers.py` | Rename `cancel` → `cancel_event` |
| `src/stages/geo_meta.py` | Rename `cancel` → `cancel_event` |
| `src/stages/aesthetic.py` | Rename `cancel` → `cancel_event` |
| `src/media/audiotrack.py` | Add `prepare_audio_guarded` |
| `src/db/corpus.py` | Import `_configure` from `utils`; add 3 lookup helpers |
| `src/db/kb.py` | Import `_configure` from `utils`; unify centroid helpers |
| `src/api/ui.py` | Delete 4 `_new` speaker handlers; add `_err_html`/`_ok_html`; use new DB helpers |
| `src/api/review.py` | Use new DB helpers from `corpus.py` |
| `static/css/main.css` | Merge duplicate rules; remove dead rules; add missing rules |
| `static/js/workbench.js` | Extract `_makeCollapsible` |
| `templates/suggest_review.html` | Remove `runLevelC`/`cancelLevelC`; call `WB.runStage`/`WB.cancelStage` |
| `templates/prompt_library.html` | Fix `hx-put` + `hx-get` conflict |
| `templates/partials/sources_panel.html` | Fix `hx-confirm=""` |
| `templates/partials/face_clusters_assigned.html` | Add btn class to unassign button |
| `templates/partials/speaker_clusters_decisions.html` | Add btn class to unassign button |
| `templates/partials/pattern_rule_form.html` | Replace 9 inline styles with `.form-field-input` |
| `templates/partials/registry_edit_form.html` | Replace 4 inline styles with `.form-field-input` |
| `templates/pattern_rules.html` | Replace inline style with `.page-subtitle` |
| `templates/vocabulary.html` | Replace inline style with `.page-subtitle` |
| `templates/partials/candidates_queue.html` | Fix undefined `.col-term` class |
| `templates/partials/new_terms_queue.html` | Fix `.token-count` → `.token-freq` |
| `templates/corpus_stats.html` | `.stage-row--done` now defined in CSS |
| Various templates | `btn-primary` → `btn btn--capture`; `btn-danger` → `btn btn--danger` |

---

## Test Coverage

New modules `src/pipeline/embeddings.py`, `src/db/utils.py`, and the `prepare_audio_guarded` function in `audiotrack.py` each require unit tests. The three new DB helpers in `corpus.py` require integration tests.

All existing stage, API, and DB tests must continue to pass — this sprint changes no logic, only code location and naming.

**Target: ≥1665 passing** (≥20 net new tests from the new shared modules and helpers)
