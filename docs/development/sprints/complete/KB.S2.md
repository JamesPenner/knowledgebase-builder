# Sprint KB.S2 — AudioTrack: Shared Audio Preparation Layer

## Scope

Introduce `src/media/audiotrack.py` with an `AudioTrack` dataclass and
`prepare_audio()` context manager that encapsulates ffmpeg audio extraction,
silence detection (VAD gate), clipping detection, and optional normalisation.
Wire the three audio-consuming stages to use it. Persist `has_speech` to
`corpus_files` so stages can skip model loading on silent files without
re-extracting audio.

No schema changes beyond the `has_speech` column. No new API endpoints or CLI
commands. No UI changes.

## Builds On

- KB.S1 (LLMSession): establishes the `src/media/` package stub and the
  pattern of context-manager wrappers around expensive resources
- KB.P17 (Diarization): last audio stage added; `_extract_audio()` now exists
  independently in transcribe.py, voice.py, and `src/stages/diarize.py`

## Baseline

Record current test count at sprint start (see ROADMAP.md).

## Deliverables

### New module

**`src/media/__init__.py`** — empty package marker (created in KB.S1 if not
already present; otherwise no-op)

**`src/media/audiotrack.py`**

```python
@dataclass
class AudioProfile:
    name:      str
    normalise: bool = False
    vad:       bool = True

# Built-in named profiles
DEFAULT  = AudioProfile("default",  normalise=False, vad=True)
ARCHIVAL = AudioProfile("archival", normalise=True,  vad=True)

@dataclass
class AudioTrack:
    file_path:        Path
    wav_path:         Path        # 16 kHz mono WAV; owned by context manager
    sample_rate:      int         # always 16000
    duration_ms:      int
    has_speech:       bool | None # None = VAD not run
    peak_db:          float | None
    has_clipping:     bool
    normalised:       bool
    segment_start_ms: int | None
    segment_end_ms:   int | None
    owned:            bool        # True = wav_path is a tmp file

def prepare_audio(
    file_path: Path,
    config: Config,
    *,
    profile: AudioProfile | None = None,
    normalise: bool | None = None,
    vad: bool | None = None,
    segment_start_ms: int | None = None,
    segment_end_ms:   int | None = None,
) -> contextlib.AbstractContextManager[AudioTrack | None]:
    ...
```

**Preparation steps (in order):**

1. Probe for audio stream via ffprobe. Return `None` if absent.
2. Extract to 16 kHz mono WAV in a `TemporaryDirectory` via one ffmpeg call.
   If `segment_start_ms`/`segment_end_ms` are set, pass `-ss`/`-to`. Return
   `None` if ffmpeg fails or output is empty.
3. Detect clipping: scan WAV samples; set `has_clipping=True` if any 100 ms
   block has a peak amplitude ≥ 32700 (INT16 saturation threshold).
4. If `normalise=True`: peak-normalise in memory via numpy. Record `peak_db`
   before normalisation. Set `normalised=True`.
5. If `vad=True`: compute RMS energy over 100 ms windows. If all windows are
   below a fixed silence threshold (configurable via
   `config.vad_silence_threshold`, default -50 dBFS), set `has_speech=False`.
   Otherwise set `has_speech=True`. Conservative threshold — err toward
   calling audio "has speech" to avoid false-positive silence flags on quiet
   but real content.
6. Return `AudioTrack`; `__exit__` deletes the `TemporaryDirectory`.

**Never raises.** An outer `try/except Exception` wraps the function body;
failures are logged at WARNING level. Returns `None` on any unrecoverable
error.

**Usage pattern:**
```python
with prepare_audio(file_path, config) as track:
    if track is None:
        continue
    if track.has_clipping:
        logger.warning("audio: clipping detected in %s", file_path)
    if track.has_speech is False:
        continue   # skip without loading model
    run_whisper(track.wav_path)
```

### New migration

**`migrations/corpus/0019_has_speech.sql`**

```sql
ALTER TABLE corpus_files ADD COLUMN has_speech INTEGER;
```

Nullable BOOLEAN. `NULL` = audio preparation has not run for this file.
`0` = VAD found no speech. `1` = speech detected.

### New DB helper

**`src/db/corpus.py`**

- `get_has_speech(db, file_id) -> bool | None`
- `set_has_speech(db, file_id, value: bool) -> None`

### Modified modules

**`src/stages/transcribe.py`**

- Remove `_extract_audio()` — replaced by `prepare_audio()`
- Before loading Whisper: check `get_has_speech(db, file_id)`; if `False`,
  skip without loading model
- Inside the per-file loop: `with prepare_audio(file_path, config) as track:`
- After successful extraction: `set_has_speech(db, file_id, track.has_speech)`
- Log clipping warning if `track.has_clipping`

**`src/stages/voice.py`**

- `embed_voice()` currently calls `librosa.load()` directly on the source
  file. Replace with: accept `wav_path: Path` parameter (the already-extracted
  WAV from `AudioTrack`). The stage loop calls `prepare_audio()` and passes
  `track.wav_path` to `embed_voice()`.
- Check `get_has_speech(db, file_id)` before `prepare_audio()` — if already
  known False, skip.
- Log clipping warning.

**`src/stages/diarize.py`** (if exists as a separate module)

- Same pattern as voice.py: accept `wav_path`, check `has_speech`, log
  clipping warning.

**`src/db/corpus.py`**

- Add `get_has_speech` and `set_has_speech` named query functions.

**`src/config.py`**

- Add `vad_silence_threshold: float = -50.0` — dBFS threshold for RMS silence
  gate. Exposed in `config.yaml` under `thresholds:`.
- Add `audio_profile: str = "default"` — named audio profile for the KB.

## Acceptance Criteria

1. `python -m pytest tests/ -q` → all prior tests pass; ≥ 15 new tests pass
2. `ruff check src/ tests/` → 0 errors
3. `from src.media.audiotrack import AudioTrack, AudioProfile, prepare_audio` succeeds
4. `_extract_audio()` no longer defined in `transcribe.py` or any audio stage
5. `get_has_speech(db, file_id)` returns `False` for a file where VAD found
   no speech; subsequent stage run skips without loading model (verified by
   checking no model import occurs in the skipped branch)
6. Migration 0019 applies cleanly on a fresh corpus created by the test fixture
7. `AudioTrack` context manager cleans up its temp WAV file on exit even when
   the caller raises an exception inside the `with` block

## Test Targets — ~15 new tests

All in `tests/unit/test_audiotrack.py` and
`tests/integration/test_audio_preparation.py`. No real audio model required;
ffmpeg is available in the test environment.

### Unit tests (no ffmpeg)

- `test_audiotrack_dataclass_fields` — construct `AudioTrack` with all fields;
  verify attribute access
- `test_audioprofile_defaults` — DEFAULT profile has `normalise=False, vad=True`
- `test_vad_silent_returns_false` — given a numpy array of near-zero samples,
  the RMS gate sets `has_speech=False`
- `test_vad_noisy_returns_true` — given a numpy array with signal, returns
  `has_speech=True`
- `test_clipping_detected` — array with INT16-max samples triggers
  `has_clipping=True`
- `test_clipping_clean_audio` — array with mid-range samples: `has_clipping=False`
- `test_normalise_scales_peak` — after normalisation, peak amplitude near
  INT16_MAX; `normalised=True`

### Integration tests (uses ffmpeg, synthetic WAV in tmp_path)

- `test_prepare_audio_returns_track_for_video` — synthesise a short MP4 with
  audio via ffmpeg; `prepare_audio()` returns `AudioTrack` with `wav_path`
  existing and `duration_ms > 0`
- `test_prepare_audio_returns_none_for_image` — pass a JPEG; returns `None`
- `test_prepare_audio_returns_none_for_silent_video` — synthesise a video with
  silent audio; `track.has_speech is False`
- `test_prepare_audio_context_manager_cleans_up` — after `with` block exits,
  `track.wav_path` no longer exists on disk
- `test_has_speech_persisted_to_db` — after processing a silent file,
  `get_has_speech(db, file_id)` returns `False`
- `test_has_speech_skips_model_load` — file with `has_speech=False` in DB;
  verify transcribe stage does not call the model loader (mock the import)
- `test_segment_extraction` — `segment_start_ms=1000, segment_end_ms=3000`;
  `track.duration_ms` approximately 2000ms
- `test_prepare_audio_never_raises` — pass a corrupt/truncated file; returns
  `None` without raising

## Design Notes

### VAD method

RMS energy with a fixed threshold. Silero and WebRTC VAD are more accurate
but add dependencies not justified for a silence gate whose main job is
catching encoding artifacts and no-audio files. Conservative threshold (-50
dBFS) means quiet-but-real speech is never silently skipped. The
`config.vad_silence_threshold` field allows tuning per corpus.

### Cross-stage sharing limitation

`AudioTrack` cannot be shared across pipeline stages because stages run
sequentially as separate DAG nodes with no shared in-memory state. Each stage
that needs audio calls `prepare_audio()` independently. The VAD gate
(`has_speech` in DB) is the mechanism for avoiding repeated model loads, not
shared memory.

### `embed_voice()` signature change

`embed_voice()` currently calls `librosa.load()` on the source file path.
After this sprint it accepts a `wav_path: Path` of an already-extracted
16 kHz mono WAV. The stage loop provides this from `AudioTrack.wav_path`.
This is a breaking change to the `embed_voice()` signature but the function
is only called from one place (the voice stage loop).

### No combined audio stage

Transcribe, voice embedding, and diarization remain independent DAG nodes.
The cross-stage decode cost (3 × ffmpeg) is negligible compared to Whisper
and Resemblyzer inference time. Pipeline flexibility (running transcription
without voice embedding) is more valuable than the decode saving. Revisit
only if profiling shows ffmpeg is a measurable bottleneck.

## Out of Scope

- `prepare_file()` combined visual+audio entry point (`FRAMESET_CONCEPT.md`)
- Combined audio stage restructuring
- `AudioProfile` in `config.yaml` UI (config field added; UI exposure deferred)
- Any visual frame preparation (KB.S3)
- Any text assembly changes (KB.S4)
