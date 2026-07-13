# KB.AN1 — Pipeline Execution Correctness

**Status:** Complete
**Preceding sprint:** KB.AM3 (Knowledge Settings: UI, 1865 tests)
**Concept doc:** none — findings from ad hoc investigation of a user-reported
`voice_diarize` issue (this session), not a pre-existing concept document.
**Result:** 1887 tests passing, 2 skipped (+7 net)

## Implementation Notes

- **`_progress`/`_active_cancels` keyed by a plain `(kb, stage)` tuple**, not a
  wrapper type — `src/pipeline/progress.py` gained `is_running(kb, stage)` as
  the single guard check reused by all four route groups. `SseProgressReporter`
  now takes `kb` in its constructor and stores the composite key once.
- **All four route groups got the identical fix independently** —
  `_make_stage_routes` plus the hand-rolled `export`/`summarize`/`suggest`
  groups in `src/api/pipeline.py` — rather than consolidating them onto one
  shared implementation first. Deduplicating those three hand-rolled groups
  remains a separate, larger refactor (noted in Out of Scope), not required to
  fix this bug.
- **Cancel no longer closes the `EventSource` or resets the UI eagerly.**
  `static/js/pipeline.js` gained `_attachStream(stage, kb)`, extracted from
  `runStage`'s inline SSE wiring so `cancelStage` can reuse the exact same
  `done`/`failed` handling instead of resetting the Run button/badge itself.
  `runStage` also now tolerates a `409` response by attaching to the
  already-running job's stream instead of treating it as a failure — this
  matters because two tabs (or a stale Run click) can legitimately race to
  start the same `(kb, stage)`.
- **New nullable `files.voice_checked_at` / `files.voice_diarize_checked_at`**
  (migration `0025_voice_processing_markers.sql`) replace the old
  "pending = no row in `file_voice_embeddings`/`file_voice_segments`" queries.
  A file that's silent, too short, or hits a swallowed per-file error is now
  marked checked regardless of outcome, so `voice`/`voice_diarize` can
  actually reach 0 pending on a corpus that contains non-speech files.
  `reset_voice_embeddings`/`reset_voice_segments` (used by `--force`/rerun)
  now also clear the corresponding marker column — found via an existing test
  (`test_force_resets_segments`) that would otherwise have silently stopped
  re-selecting force-reset files as pending.
- **Test isolation gap found and fixed in `tests/conftest.py`.** Introducing
  a guard that *reads* `_progress` (a process-global dict) before allowing a
  new run exposed that several existing tests stub the stage runner function
  without ever calling `progress.done()`/`.failed()` — their `"running"`
  entries were leaking across tests that reused the same `kb`/stage name,
  spuriously 409-ing unrelated tests. Added an autouse fixture that clears
  `_progress` before/after every test.
- **Manual verification caveat:** the first attempt to start a server for
  live verification (`python -m src.cli serve`) silently failed (`src.cli`
  has no `__main__`); the `curl` calls that followed actually hit a
  *different*, pre-existing dev server (PID 15684, started earlier by the
  user, running pre-KB.AN1 code) and sent it two real `validate` runs
  against the user's actual `test-run` KB before this was noticed. That
  process had already exited on its own by the time it was investigated;
  confirmed with the user before restarting it via the proper `enrich serve`
  entrypoint. Verification then proceeded cleanly against the correctly
  started server (409 guard, per-kb isolation, and the cancel/reload flow
  all confirmed via `curl` and a real browser session).

## Pre-Sprint Review Findings (confirmed against current code before implementation)

1. **Baseline:** `python -m pytest tests/ -q` → 1880 passed, 2 skipped (up from
   the 1865 recorded after `KB.AM3` — unrelated uncommitted work already in
   the tree, a pattern-rules-staleness feature touching `ui.py`/`corpus.py`/
   `kb.py`/some templates; not touched by this sprint).
2. **Design Authority conflict found and resolved.** `ARCHITECTURE.md`'s
   Pattern 1 and `SPEC.md`'s Progress Reporting section (`_progress = {
   stage_name: {...} }`, `GET /api/stages/{stage}/stream`) both document
   `/cancel`, `/status`, `/stream` as taking no `kb` parameter — only `/run`
   carries `kb`, in its body. The approved fix (scope execution state by
   `(kb, stage)`, per session discussion) changes that documented shape.
   Raised to the user before implementation; confirmed to proceed with the
   `(kb, stage)`-scoped design (correct behavior for two KBs running the same
   stage concurrently) over the alternative (a global stage-name-only lock,
   which would leave Pattern 1 untouched but block legitimate concurrent
   same-stage/different-KB runs). See Design Authority Updates below.

## Design Authority Updates (required this sprint)

1. **`ARCHITECTURE.md` Pattern 1** (~line 142) — update the documented shape
   to show `kb` as a required query parameter on `/cancel`, `/status`, and
   `/stream` (matching `/run`, which already carries it in its body):
   ```
   POST   /api/stages/{stage}/run?kb={kb}       → {"job_id": str, "status": "started"}
   POST   /api/stages/{stage}/cancel?kb={kb}    → {"status": "cancelled"}
   GET    /api/stages/{stage}/status?kb={kb}    → {...}
   GET    /api/stages/{stage}/stream?kb={kb}    → text/event-stream
   ```
2. **`SPEC.md` Progress Reporting section** (~line 1622-1638) — update the
   `_progress` shape and stream example to reflect the composite key:
   ```python
   _progress = {}   # { (kb, stage_name): {current, total, rate, eta, status} }
   ```
   and note that `GET /api/stages/{stage}/stream` requires `?kb=` to select
   which KB's job to observe.

Both edits made as part of this sprint's implementation, not deferred.

## Goal

Fix a real concurrency/race bug in the stage run/cancel/progress
infrastructure discovered while diagnosing why `voice_diarize` appeared stuck
around 5 files, and close the gap that prevents `voice`/`voice_diarize` from
ever reaching true completion on a corpus containing non-speech files. This
sprint is infrastructure correctness only — no diarization/matching accuracy
changes (see `KB.AN2`).

## Builds On

- `_make_stage_routes()` factory and the three hand-rolled route groups
  (`export`, `summarize`, `suggest`) in `src/api/pipeline.py`.
- `SseProgressReporter`, `_progress`, `_active_cancels`, `get_progress`,
  `init_progress` in `src/pipeline/progress.py`.
- The `kb: str = Query(...)` convention already used elsewhere
  (`resolve_kb`, `src/api/deps.py:13`).
- `runStage`/`cancelStage`/`es.onmessage` in `static/js/pipeline.js`, and
  their call sites in `templates/normalise_review.html`,
  `templates/suggest_review.html`, `templates/partials/pipeline_groups.html`.
- The `has_speech`/`set_has_speech`/`get_has_speech` per-file marker pattern
  established in `KB.S2` (`src/db/corpus.py`) — precedent for the new
  per-file "checked" markers below.
- `get_files_without_voice_embedding` / `get_files_without_voice_segments`
  (`src/db/corpus.py:2305`, `:2354`).

## Acceptance Criteria

### A. Scope stage execution state by (kb, stage), not stage alone
- `_active_cancels` and `_progress` keyed by a composite `(kb, stage)` key.
- `SseProgressReporter.__init__`, `init_progress`, `get_progress` take a `kb`
  parameter alongside `stage`.
- `/cancel`, `/status`, `/stream` (currently kb-less) gain
  `kb: str = Query(...)`, for both `_make_stage_routes`-generated routes and
  the hand-rolled `export`/`summarize`/`suggest` groups.
- Running the same stage against two different KBs concurrently no longer
  shares state.

### B. Reject a concurrent run for the same (kb, stage)
- `POST /{stage}/run` checks the current progress state for `(kb, stage)`;
  if `status == "running"`, return HTTP 409 with a clear message instead of
  silently starting a second background worker.
- `pipeline.js`'s `runStage()` surfaces that 409 distinctly from a generic
  failure.

### C. Cancel waits for real termination instead of resetting eagerly
- `cancelStage()` no longer closes the `EventSource` or re-enables the Run
  button immediately on click. It POSTs `/cancel`, shows a "Cancelling…"
  badge state, and leaves the SSE connection open — the existing
  `es.onmessage` `done`/`failed` handling (already present) re-enables the
  Run button and closes the stream once the worker actually stops.
- Template call sites updated to pass `kb` into `cancelStage(stage, kb)`.

### D. `voice`/`voice_diarize` can reach true completion
- New migration `src/migrations/corpus/0025_voice_processing_markers.sql`
  adds nullable `voice_checked_at` and `voice_diarize_checked_at` columns to
  `files`, following the `has_speech` precedent from `KB.S2`.
- `run_voice`/`run_voice_diarize` set the relevant marker after processing a
  file, regardless of whether an embedding/segments were actually produced.
- `get_files_without_voice_embedding`/`get_files_without_voice_segments`
  filter on the new marker being `NULL` — a file that was checked and
  legitimately produced nothing is no longer perpetually "pending."
- Pending-count badges for these stages can now reach zero on a
  fully-processed corpus.

## Out of Scope

- Merging `export`/`summarize`/`suggest`'s hand-rolled routes into
  `_make_stage_routes` — the same (kb,stage)-scoping fix is applied to all
  four route groups individually; consolidating them into one shared
  implementation is a separate refactor not required to fix this bug.
- Any accuracy change to diarization/embedding matching — `KB.AN2`.
- `workbench.js`'s sequential multi-stage runner — already awaits each stage
  serially within one tab; not exposed to this bug.

## Test Coverage Expectations

- Integration test: two concurrent `/run` POSTs for the same `(kb, stage)` —
  second rejected with 409, only one worker's output lands in the DB.
- Integration test: `/run` for the same stage against two different KBs
  concurrently — both complete independently, no shared-state bleed.
- Integration test: cancel-then-immediate-run-again no longer produces
  duplicate/conflicting writes.
- Unit test: `get_files_without_voice_embedding`/
  `get_files_without_voice_segments` correctly exclude a file whose marker
  column is set even when it produced zero embeddings/segments.
- Manual verification: run `voice_diarize` against the real `test-run` KB
  (or a representative subset) and confirm the pending count reaches zero,
  and that cancel-then-run-again in the browser no longer reproduces the
  original interleaved-log symptom.
