# KB.AN2 — Voice Diarization Accuracy & Performance

**Status:** Complete
**Preceding sprint:** KB.AN1 (Pipeline Execution Correctness) — required
first; accuracy validation needs runs that actually complete in reasonable
time, and KB.AN1 removes the per-file pyannote Pipeline reload this sprint
would otherwise be validating against.
**Concept doc:** none
**Result:** 1911 tests passing, 2 skipped (+24 net from the 1887 baseline
recorded at sprint start — that baseline itself included an unrelated,
already-in-progress pattern-rules-staleness feature committed separately at
the start of this sprint, not part of KB.AN2)

## Implementation Notes

- **Criterion A (pooling):** `run_voice_diarize` groups `diarize_audio()`
  turns by local `speaker_label`, calls a new `embed_pooled_voice_segments()`
  once per label (concatenated audio across that label's spans), and
  propagates the resulting `embedding`/`matched_person_id`/`matched_cluster_id`/
  `similarity` to every `file_voice_segments` row sharing that label. The old
  per-turn `embed_voice_segment()` was removed (only caller was this loop) in
  favor of `embed_pooled_voice_segments()`, which subsumes it (a one-span pool
  behaves identically).
- **Criterion B (duration floor + model caching):** `_MIN_DURATION_S` raised
  to 1.5s. `_build_voice_encoder()` and `_load_diarization_pipeline()` extracted
  as the sole model-construction boundary; `run_voice`/`run_voice_diarize`
  build each lazily on first use (skipped entirely when there's no pending
  work) and reuse for the rest of the run. **Deliberate behavior change:** a
  `Pipeline.from_pretrained()` failure previously degraded silently to zero
  segments per file (reloaded, and re-failed, on every file); since loading
  now happens once, a load failure raises `ModelLoadError` and aborts the run
  instead — one clear failure instead of N silent no-ops, consistent with how
  `ModelLoadError` is used elsewhere (e.g. `run_face`'s upfront checks). Not
  spelled out in the original acceptance criteria; flagged to the user during
  implementation.
- **Criterion C (overlap-aware matching):** new pure function
  `_find_overlapping_indices()` flags turns overlapping a turn from a
  *different* speaker label (same-label overlap is not cross-talk and isn't
  flagged). Flagged turns are excluded from their label's pooling spans but
  still get their own `file_voice_segments` row with the label's propagated
  match — transcript attribution (raw label) is unaffected.
- **Criterion D (duration-weighted confidence):** linear ramp confirmed with
  the user — `base_threshold + 0.10` at the 1.5s floor, relaxing to
  `base_threshold` at 5.0s and beyond (`_duration_weighted_threshold()`).
  `embed_pooled_voice_segments()`'s return type changed from `bytes | None` to
  `(bytes, duration_ms) | (None, None)` so the caller can weight the threshold
  by actual pooled duration — applied to both the person-match and
  cluster-join/create decisions in `run_voice_diarize`. `run_voice`'s
  per-file (unpooled) matching is untouched.
- **Criterion E (warning scoping):** `warnings.catch_warnings()` +
  `filterwarnings("ignore", message=r"(?i).*torchcodec.*", category=UserWarning)`
  scoped narrowly around the `from pyannote.audio import Pipeline` line in
  `_load_diarization_pipeline()` — restores prior filter state on exit, and
  only matches the torchcodec message so other warnings from that import
  still surface.
- **Regression caught mid-implementation (Criterion B):** the first pass had
  `run_voice`/`run_voice_diarize` import `resemblyzer`/`pyannote.audio`
  directly before calling the (test-mocked) `embed_voice`/`diarize_audio`,
  bypassing the integration tests' mocks entirely — 14 tests failed since
  neither package is installed in this dev environment. Fixed by routing all
  real-model construction through `_build_voice_encoder()`/
  `_load_diarization_pipeline()`, which the integration tests now patch
  alongside the existing embed/diarize mocks.
- **Outstanding:** manual validation against the real `test-run` KB (compare
  match rate before/after) has **not** been run this session — resemblyzer/
  pyannote.audio are not installed in this dev environment, and this is a
  GPU/LLM-adjacent stage validated manually per the working agreement, not in
  CI. All other Test Coverage Expectations below are covered by the automated
  suite (1911 passing, 2 skipped).

## Goal

Replace "embed every diarized turn independently" with pooled per-speaker
embedding in `voice_diarize`, cut redundant model-loading cost, and align
matching heuristics with resemblyzer's actual reliability envelope —
improving both accuracy and throughput identified during a session
investigating `voice_diarize` behavior.

## Builds On

- `diarize_audio()`, `embed_voice_segment()`, `embed_voice()`,
  `run_voice_diarize()`, `run_voice()` in `src/stages/voice.py`.
- `cosine_similarity`/`update_centroid` in `src/pipeline/embeddings.py`.
- `config.voice_similarity_threshold`, `config.voice_diarization_min_segment_ms`
  (`src/config.py:62-63`).
- `voice_speaker_clusters`/`upsert_voice_speaker_cluster`
  (`src/db/corpus.py`, schema in `src/migrations/corpus/0011_voice_segments.sql`).
- `KB.AN1`'s per-run model-loading groundwork (single `Pipeline` load per run).

## Acceptance Criteria

### A. Pool embeddings per local speaker label, not per turn
- `run_voice_diarize` groups all `diarize_audio()` turns sharing the same
  pyannote local `speaker_label` within a file, concatenates their audio, and
  computes **one** resemblyzer embedding per local label per file.
- The resulting match (person or cluster) propagates to every turn under
  that label — `file_voice_segments` rows keep individual turn timestamps
  but share one `matched_person_id`/`matched_cluster_id`/`similarity` per
  local label.
- Encoder calls per file drop from "one per turn" to "one per unique local
  speaker."

### B. Align duration floor and cache model instances
- `_MIN_DURATION_S` raised from 1.0s to 1.5s (resemblyzer's GE2E training
  window), applied to the pooled per-speaker audio rather than per-turn.
- A single `VoiceEncoder` instance constructed once per `run_voice`/
  `run_voice_diarize` call and reused across the run (was: constructed fresh
  per `embed_voice`/`embed_voice_segment` call).
- `VoiceEncoder(verbose=False)` to silence the third-party per-construction
  log line.
- A single pyannote `Pipeline` instance loaded once per `run_voice_diarize`
  call (was: loaded inside `diarize_audio()` per file — the dominant cost
  identified during investigation, addressed here since it's inseparable
  from the per-speaker pooling change to the same loop).

### C. Overlap-aware matching
- Detect pyannote turns that overlap in time (possible with the powerset
  segmentation used by `pyannote/speaker-diarization-3.1`); exclude
  overlapping spans from the pooled audio used for identity matching.
  Transcript speaker-label attribution (which uses the raw label, not the
  embedding match) is unaffected.

### D. Duration-weighted match confidence
- A match built from less pooled audio requires a higher cosine-similarity
  bar than the flat `config.voice_similarity_threshold` used today. Exact
  curve/thresholds tuned against `test-run` KB data during implementation,
  not fixed in advance — flag as an open design decision to confirm before
  finalizing this criterion.

### E. Logging cleanliness
- Scope a `warnings.filterwarnings` around the pyannote import to suppress
  the confirmed-harmless torchcodec `UserWarning` (already sidestepped by
  passing a preloaded waveform dict) without hiding genuinely new warnings.

## Out of Scope

- Any change to `/run`/`/cancel`/progress infrastructure — `KB.AN1`.
- A dedicated review-UI feature for "orphaned" short segments — expected to
  become rare by construction once (A) lands; revisit only if real data
  shows otherwise after this sprint.
- GPU/DirectML acceleration for resemblyzer — encoder already runs in well
  under 100ms per call on CPU; not worth the added complexity.

## Test Coverage Expectations

- Unit test: turns sharing a local speaker label are pooled into one
  embedding call, not N.
- Unit test: overlapping turns are excluded from pooled identity-matching
  audio but still recorded with their raw `speaker_label` for transcript
  attribution.
- Unit test: duration-weighted confidence rejects a short-pooled-audio match
  that would have cleared the old flat threshold.
- Integration test: `run_voice_diarize` on a synthetic multi-speaker fixture
  produces fewer resemblyzer calls than turns, with matches propagating
  correctly across all turns under one label.
- Manual validation against the real `test-run` KB: compare match rate
  before/after on the same files (GPU/LLM-adjacent stage — manual check per
  the working agreement, not CI).
