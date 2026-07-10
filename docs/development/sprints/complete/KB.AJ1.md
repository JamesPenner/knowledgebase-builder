# KB.AJ1 — Cluster Result Typing

**Status:** Complete
**Branch:** `clean-master`
**Baseline:** 1710 tests passing, 2 skipped
**Result:** 1726 tests passing, 2 skipped (+16 net)
**Preceding sprint:** KB.AI1 (Navigation Restructure, 1710 tests) + face-bbox hotfix

## Goal

Source concept: `docs/development/sprints/planned/REFACTOR_CONCEPTS.md` §4 —
introduce a shared `ClusterAssignment` dataclass so face/voice cluster exports
stop being two near-identical, independently-maintained `sqlite3.Row`-based
CSV writers. Investigation ahead of implementation found the three candidate
cluster types (face, voice, GPS) are more divergent than the concept doc
assumed, and surfaced a real behavioral bug: `assign_face_cluster()` never
cascaded `person_id` to `file_face_regions` and never merged the assigned
cluster's centroid into `people.face_centroid`, unlike the equivalent voice
path (`assign_speaker_cluster()` + `merge_voice_centroid()`).

## Scope Decisions (confirmed with user before implementation)

1. **Narrow unification** — typed export layer only; DB-layer query/assign
   shape and review-API endpoint shape were not otherwise touched.
2. **Face + voice only** — GPS clusters excluded. GPS has no `person_id`, no
   pending/assigned workflow, uses `distance_m` (lower=better) instead of
   `similarity` (higher=better), and rebuilds via batch DBSCAN rather than
   incremental assignment — structurally the outlier of the three.
3. **Fix the face-assign bug as part of this sprint** — small, and directly
   touches the exact code this sprint was already unifying.

## What Was Built

### `src/pipeline/clusters.py` (new)

- `ClusterAssignment` — frozen dataclass: `file_path`, `person_id`, `score`
  (similarity; higher = better), `cluster_id`, `extra: dict` (type-specific
  fields: `region_index`/`bbox` for face, `start_ms`/`end_ms`/`speaker_label`
  for voice).
- `write_cluster_csv(path, assignments, fieldnames, row_fn)` — shared
  open/DictWriter/writeheader/writerows boilerplate. `row_fn` stays
  call-site-specific since face and voice export columns genuinely differ;
  the shared piece is typing + I/O boilerplate, not a forced common schema.
  CSV column names/order are unchanged from pre-refactor output — the export
  bundle is a documented downstream contract (VISION.md).

### `src/db/corpus.py`

- `get_face_regions_for_export()` / `get_voice_segments_for_export()` now
  return `list[ClusterAssignment]` instead of `list[sqlite3.Row]`.
- `assign_face_cluster()` / `unassign_face_cluster()` now cascade `person_id`
  to `file_face_regions` via the `face_cluster_members` join table, mirroring
  `assign_speaker_cluster()`'s cascade to `file_voice_segments`.

### `src/db/kb.py`

- `merge_face_centroid(conn, person_id, cluster_blob, cluster_count)` — new,
  mirrors `merge_voice_centroid()` exactly (weighted-average merge + L2
  normalise into `people.face_centroid`/`face_samples`).

### `src/api/ui.py`

- `ui_face_decide()` now fetches the cluster via `get_face_clusters()` and
  calls `merge_face_centroid()` before `assign_face_cluster()`, mirroring
  `ui_speaker_decide()`'s existing `merge_voice_centroid()` call.

### `src/stages/export.py`

- `_write_people()`'s `face_regions.csv` and `voice_segments.csv` writers
  now use `write_cluster_csv()`. Output format unchanged (verified by
  integration tests reading the CSV headers/content directly).

## Files Touched

| File | Change |
|---|---|
| `src/pipeline/clusters.py` | New — `ClusterAssignment`, `write_cluster_csv` |
| `src/db/corpus.py` | Typed export helpers; assign/unassign cascade fix |
| `src/db/kb.py` | New `merge_face_centroid` |
| `src/api/ui.py` | `ui_face_decide` calls `merge_face_centroid` |
| `src/stages/export.py` | Face/voice cluster writers use `write_cluster_csv` |
| `tests/unit/test_clusters.py` | New — dataclass + writer coverage |
| `tests/unit/test_face_review_unit.py` | Cascade tests; `merge_face_centroid` tests |
| `tests/unit/test_face_unit.py` | Updated for `ClusterAssignment` attribute access |
| `tests/unit/test_voice_diarize_unit.py` | Updated for `ClusterAssignment` attribute access |
| `tests/integration/test_face_integration.py` | New export regression tests |
| `tests/integration/test_face_review_integration.py` | Centroid-merge + cascade regression tests |

## Test Coverage

+16 net tests: `ClusterAssignment`/`write_cluster_csv` unit coverage, face
cascade-on-assign/unassign unit + integration coverage, `merge_face_centroid`
unit coverage (mirrors existing `merge_voice_centroid` coverage), and
face-export CSV-format regression tests (integration).

## Out of Scope — Deferred

- **GPS cluster unification** — excluded per scope decision above; revisit
  only if a concrete need for GPS/face/voice export symmetry emerges.
- **Review-API shape reconciliation** — face review's `knowledge.py` endpoints
  (`GET /people/faces/clusters` returning `{pending, assigned}` combined)
  still don't match the Pattern 2 shape (`GET /pending`, `POST /decide`,
  `GET /decisions`, `DELETE /decisions/{id}`) that speaker review's
  `review.py` already follows. Not touched this sprint.
- **Face/speaker review template duplication** — `templates/partials/
  face_clusters_*.html` vs `speaker_clusters_*.html` remain near-duplicate
  (previously flagged and deferred in KB.AG2 as a UI-restructure item).
- **Pre-existing ruff findings** — two unrelated lint issues found during
  verification (`tests/unit/test_face_unit.py:582` unused `MagicMock` import,
  `tests/unit/test_stage_runner.py:4` unused `pytest` import). Neither is
  near code this sprint touched; left as-is to stay in scope, flagged here so
  they don't get lost.
