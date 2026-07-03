# KB.AH1 — Stage Loop Runner

**Status:** Active  
**Branch:** `clean-master`  
**Baseline:** 1665 tests passing  
**Target:** ≥1685 tests passing  
**Preceding sprint:** KB.AG2 (Structural Deduplication, 1665 tests)

## Goal

Introduce `src/pipeline/stage_runner.py` with a thin `run_stage_loop()` helper. Wire it into the three cleanest per-file stages (temporal, classify, extract_fields). Standardize type annotations on all untyped stage entry-points. Fix three documented bugs/inconsistencies in `geolocate.py`.

No new user-facing behaviour — goal is a codebase where the iteration skeleton exists in one place, `progress.done()` is always guaranteed, and stage signatures are consistent.

## Acceptance Criteria

### A — `src/pipeline/stage_runner.py`

New module. `run_stage_loop(pending, process, progress, cancel_event, *, label) -> tuple[int, int]`:
- Checks `cancel_event.is_set()` before each item
- Calls `progress.update(i+1, total)` per item
- Catches and logs per-item exceptions; does not propagate
- Calls `progress.done()` unconditionally via `try/finally`
- Returns `(processed, errors)`
- Never opens, closes, or touches DB connections

### B — Wire stages

**B1. `src/stages/temporal.py`** — replace inline loop skeleton with `run_stage_loop`; add `try/finally` for conn cleanup; update `update_pipeline_checkpoint` to use runner's returned `processed` count.

**B2. `src/stages/classify.py`** — same; batch commit every 200 stays inside `_process` closure.

**B3. `src/stages/extract_fields.py`** — same; batch commit every 100 stays inside `_process` closure. Early return (csv_path missing) calls `run_stage_loop([], ...)` or explicit `progress.done()`.

### C — Signature standardization

Add type annotations to 7 untyped entry-points: `aesthetic.py`, `face_meta.py`, `geo_meta.py`, `attribute_speakers.py`, `face.py`, `voice.py` (`run_voice` + `run_voice_diarize`). Zero behavior change.

### D — `geolocate.py` bug fixes

- D1: Add `progress.done()` to early-exit path (no region data found)
- D2: `progress.update(i, total, ...)` → `progress.update(i + 1, total, ...)`
- D3: Remove cancel guard from `update_pipeline_checkpoint` and `progress.done()` — make both unconditional
