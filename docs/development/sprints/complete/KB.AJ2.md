# KB.AJ2 — Face/Voice Review: Centroid Quality Focus

**Status:** Complete
**Branch:** `clean-master`
**Baseline:** 1726 tests passing, 2 skipped
**Result:** 1755 tests passing, 2 skipped (+29 net)
**Preceding sprint:** KB.AJ1 (Cluster Result Typing, 1726 tests)

## Goal

Source concept: `docs/development/sprints/planned/UI_REDESIGN_CONCEPT.md` §2-3 —
reframe Face Review and Speaker/Voice Review around centroid *reliability*
rather than cluster-assignment *completeness*, consistent with the project's
core philosophy (knowledge-building workbench, not a catalogue-completeness
tool). This sits directly downstream of KB.AJ1, which gave face
cluster-assignment centroid tracking parity with voice.

## Scope Decisions (confirmed with user before implementation)

1. **Metric**: mean cosine similarity computed live from assigned embeddings
   (`file_face_regions`/`file_voice_segments` rows already tagged with
   `person_id`), not the existing `face_centroid_spread` column (face-only,
   often NULL, no voice equivalent).
2. **Page shell**: stayed on the current flat layout for
   `face_review.html`/`speaker_review.html`. No migration to
   `review_base.html` — that remains a separate, previously-flagged
   (KB.AG2) UI-restructure item.
3. **People Registry badge**: replaced the existing face-only
   Insufficient/Fair/Robust/Strong badge with the same
   reliable/needs-more-samples/too-few-samples status, for both face and
   voice.
4. **`merge_people()` bug fix included**: it merged voice centroids on
   person-merge but never called `merge_face_centroid()` for
   `face_clusters` — fixed by mirroring the voice loop.

## What Was Built

### `src/pipeline/embeddings.py`

- `mean_similarity_to_centroid(centroid, embeddings)` — mean cosine
  similarity of a list of embeddings to a centroid; `None` if no centroid
  or no embeddings.
- `classify_centroid_status(cluster_count, mean_similarity, *, min_clusters,
  min_similarity)` — pure 3-state classifier: `"too_few_samples"` (0
  clusters), `"needs_more_samples"` (below either threshold or no
  similarity data), `"reliable"` (both thresholds met).
- `rank_clusters_by_similarity(clusters, people, centroid_col)` — annotates
  each cluster with `best_person_id`/`best_person_name`/`best_similarity`
  and sorts descending; clusters with no centroid or no people to compare
  sort last, stably.

### `src/db/corpus.py`

- `get_pending_face_clusters()` / `get_pending_speaker_clusters()` now
  select `centroid` (previously omitted) so ranking has something to
  compare.

### `src/db/kb.py`

- New `get_voice_embeddings_for_person()` — mirrors
  `get_face_embeddings_for_person()`.
- `get_people_with_cluster_counts()` extended: adds `voice_samples`
  (previously missing), `face_mean_similarity`/`voice_mean_similarity`
  (computed only when the respective cluster count > 0, to skip the
  embedding fetch otherwise).
- New `annotate_people_centroid_status()` and `get_centroid_quality()` —
  config-agnostic (take raw threshold values, not a `Config` object,
  keeping the DB layer decoupled from config) data-assembly functions
  shared by `src/api/ui.py` (face/speaker review pages, people registry)
  and `src/api/knowledge.py` (`GET /api/knowledge/people`).
- `merge_people()`: added the missing `face_clusters` fetch +
  `merge_face_centroid()` loop, mirroring the existing `voice_clusters`
  loop.

### `src/config.py`

Four new threshold fields, following the existing `face_meta_*`/`voice_*`
naming convention: `face_centroid_reliable_min_clusters` (5),
`face_centroid_reliable_min_similarity` (0.7),
`voice_centroid_reliable_min_clusters` (5),
`voice_centroid_reliable_min_similarity` (0.7). Registered in both the
`_typed(...)` default block and the `_threshold_map` YAML-override dict.

### `src/api/ui.py`

- Face and speaker page/partial route handlers load per-KB config, rank
  pending clusters via `rank_clusters_by_similarity()`, and compute
  per-person status via `get_centroid_quality()`.
- New `GET /knowledge/people/faces/partials/quality` and
  `GET /review/speakers/partials/quality` (+ `/knowledge/people/speakers/...`
  dual route) partials, refreshed on `decisionsChanged from:body` alongside
  the existing assigned/decisions partials.
- People registry page/partial handlers annotate each person with
  `face_status`/`voice_status` via `annotate_people_centroid_status()`.

### `src/api/knowledge.py`

- `GET /people` now loads config and annotates the same
  `face_status`/`voice_status` fields, keeping the JSON API and HTML UI in
  sync.

### Templates

- `partials/face_clusters_queue.html` / `speaker_clusters_queue.html` —
  "Suggested: {name} ({similarity})" hint on ranked pending cards.
- New `partials/face_quality.html` / `partials/speaker_quality.html` — a
  per-person quality table plus the "Centroids reliable — further review
  optional" banner (shown only when ≥1 tracked person exists and all are
  reliable).
- `face_review.html` / `speaker_review.html` — new "Centroid Quality"
  section wired to the new partials.
- `partials/person_list.html` — spread-based badge replaced with the
  shared status, for both voice and face columns.

## Bug Fixed Along the Way

Adding `centroid` to `get_pending_speaker_clusters()`'s `SELECT` broke
`GET /api/review/speakers/pending` (`src/api/review.py`) — it passes DB
rows straight to a JSON response, and a raw BLOB isn't UTF-8-serializable.
Fixed by popping `centroid` from the JSON items before returning (the JSON
API consumer never needed the raw embedding; only the HTML template routes
in `ui.py` do). Caught by the full test suite run, not by scoped testing —
a reminder that schema-shape changes to shared DB helpers need a full-suite
check even when the change looks additive.

## Files Touched

| File | Change |
|---|---|
| `src/pipeline/embeddings.py` | New: `mean_similarity_to_centroid`, `classify_centroid_status`, `rank_clusters_by_similarity` |
| `src/db/corpus.py` | Add `centroid` to two pending-cluster queries |
| `src/db/kb.py` | New `get_voice_embeddings_for_person`, `annotate_people_centroid_status`, `get_centroid_quality`; extended `get_people_with_cluster_counts`; fixed `merge_people` |
| `src/config.py` | 4 new `*_centroid_reliable_*` threshold fields |
| `src/api/ui.py` | Face/speaker page+partial handlers; new quality partials; people registry annotation |
| `src/api/knowledge.py` | `GET /people` annotates status fields |
| `src/api/review.py` | Fixed BLOB JSON-serialization bug in `GET /speakers/pending` |
| `templates/partials/face_clusters_queue.html`, `speaker_clusters_queue.html` | Suggested-match hint |
| `templates/partials/face_quality.html`, `speaker_quality.html` | New — quality table + banner |
| `templates/face_review.html`, `speaker_review.html` | New Centroid Quality section |
| `templates/partials/person_list.html` | Shared status replaces spread-based badge |
| `tests/unit/test_shared_utilities.py` | New coverage for the 3 embeddings.py functions |
| `tests/unit/test_people_registry_unit.py` | New coverage for kb.py additions + merge_people fix |
| `tests/integration/test_face_review_integration.py`, `test_speaker_review_integration.py` | Ranking order + quality banner regression tests |
| `tests/integration/test_people_registry_integration.py` | Status fields on API + person-list badge |

## Test Coverage

+29 net tests: pure-function unit coverage for the three new
`embeddings.py` functions (including edge cases — no centroid, no people,
null person centroid), unit coverage for the new/extended `kb.py`
functions and the `merge_people` face-centroid fix, and integration
coverage for ranked-queue ordering and the reliable/needs-more-samples
banner states on both review pages plus the people registry.

## Out of Scope — Deferred

- **`review_base.html` migration** for face/speaker review (tabs, action
  legend, shared JS) — separate UI-restructure item, previously flagged in
  KB.AG2.
- Remaining `UI_REDESIGN_CONCEPT.md` items: Health Page System vs Corpus
  split, Corpus File Browser, Vocabulary Review Improvements, Export Page
  Framing.
