# KB.AN2 — Voice Diarization Accuracy & Performance

**Status:** Planned
**Preceding sprint:** KB.AN1 (Pipeline Execution Correctness) — required
first; accuracy validation needs runs that actually complete in reasonable
time, and KB.AN1 removes the per-file pyannote Pipeline reload this sprint
would otherwise be validating against.
**Concept doc:** none

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
