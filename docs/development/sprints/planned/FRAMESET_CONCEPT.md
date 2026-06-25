# Concept: Unified Media Preparation Layer (FrameSet + AudioTrack)

## Summary

Replace the current ad-hoc per-stage frame and audio extraction with a shared
preparation layer that normalises images, extracts video frames, and extracts
audio tracks once per file, applies quality filtering and scene-aware sampling,
and returns typed `FrameSet` and `AudioTrack` objects that downstream stages
consume without re-decoding the source file.

An optional persistent frame cache (the opportunistic cache) stores extracted
frames in `corpus.db`. Stages check for cached frames on startup and use them if
present and fresh; otherwise they call `prepare_visual()` live. Users who run
`enrich prepare` before describe/face/quality get true cross-stage decode
reduction. Users who skip it get current behaviour, unchanged.

---

## Problem Statement

### Multiple decode passes per file

Today each stage that needs pixel data opens and decodes the source file
independently:

| Stage | Image path | Video path |
|---|---|---|
| Hash | `_hash_image()` opens PIL directly | `_hash_video()` calls `get_video_frames()` → ffmpeg |
| Describe | `_describe_image()` opens PIL, resizes | `describe_video()` calls `_extract_frames()` → ffmpeg |
| Face | Opens PIL per file | Would call `get_video_frames()` → ffmpeg again |
| Quality | Opens PIL per file | Calls `get_video_frames()` → ffmpeg |

Audio has the same problem: transcribe, voice embedding (Resemblyzer), and
diarization (pyannote) each call `_extract_audio()` independently, spawning a
separate ffmpeg process on the same source file.

### Efficiency note for Option A (in-memory)

The primary wins from this refactor — unified preparation logic, consistent
quality filtering, typed frame metadata, profiles — are **code quality and
correctness**, not decode performance. Because pipeline stages run sequentially
and do not share memory, Option A (in-memory FrameSet) does not reduce decode
passes across stages. The decode reduction only materialises with Option B
(persisted bytes, via the opportunistic cache) or via `prepare_file()` which
combines frame and audio extraction into one ffmpeg call for video files
processed within a single stage run.

### No shared frame metadata

Frames are plain `bytes` (JPEG). There is no attached record of a frame's
timestamp, which scene it belongs to, its dimensions, brightness, sharpness,
or pHash. Each stage that needs any of that information recomputes it.

### Inconsistent quality handling

The quality filter (`_frame_quality()`) lives only inside `describe_video()`.
The hash stage, quality stage, and face stage do not benefit from it. A dark
frame that is correctly skipped for description still gets hashed and passed to
face detection.

### Scene detection is underpowered

The current "scene" mode in `get_video_frames()` is pHash deduplication of
uniformly-sampled frames. It discards near-duplicate consecutive frames but does
not detect scene boundaries or allocate frames per scene. A 10-scene documentary
and a single-scene home video receive the same flat frame budget.

### No corpus-level visibility

There is no way to visually browse the corpus in the web UI. Review pages are
text-driven. The face cluster review page recomputes PIL crops at request time.
Speech detection results (`has_speech`) are not persisted — there is no way to
filter files by audio content or show silent-file statistics without re-running
audio extraction.

---

## Naming

`prepare_visual()` is the correct name for the visual factory function. "Media"
is too broad — it encompasses audio, text, and documents as well as images and
video. The three functions have parallel, unambiguous names:

```
prepare_visual(file_path, config, ...)  →  FrameSet | None
prepare_audio(file_path, config, ...)   →  AudioTrack | None
prepare_file(file_path, config, ...)    →  tuple[FrameSet | None, AudioTrack | None]
```

A video file can be sent to either `prepare_visual()` or `prepare_audio()`
depending on what the stage needs, or to `prepare_file()` when it needs both.
Each function returns `None` when the requested content type is absent:

| File type | `prepare_visual()` | `prepare_audio()` |
|---|---|---|
| Image | `FrameSet` (single frame) | `None` |
| Video | `FrameSet` (sampled frames) | `AudioTrack` |
| Audio | `None` | `AudioTrack` |

---

## Module Layout

```
src/media/
    __init__.py
    frameset.py    — Frame, FrameSet, VisualProfile, prepare_visual(), load_frameset()
    audiotrack.py  — AudioTrack, AudioProfile, prepare_audio()
    prepare.py     — prepare_file() (combined entry point, imports both)
```

`load_frameset()` lives in `frameset.py` alongside `prepare_visual()` because it
is a `FrameSet` factory — it returns a `FrameSet` built from cached DB rows rather
than from a source file. Stage modules call one or the other; the cache-or-compute
decision lives in a thin wrapper in each stage rather than inside the factories.

No module in `src/media/` imports from `src/stages/` or `src/db/`. The
`load_frameset()` function accepts a raw `sqlite3.Connection` rather than
importing from `db/corpus.py`. Stage modules import from `src/media/`; the
dependency is one-way.

---

## Core Dataclasses

### Frame

```python
@dataclass(slots=True)
class Frame:
    jpeg_bytes:     bytes
    width:          int           # encoded width in pixels
    height:         int           # encoded height in pixels
    timestamp_ms:   int | None    # None for still images
    scene_id:       int | None    # None if scene detection was not run
    brightness:     float         # mean grayscale value (0–255)
    sharpness:      float         # variance of discrete Laplacian
    phash:          str | None    # perceptual hash hex string
    passed_quality: bool          # True if brightness and sharpness met thresholds
    enhanced:       bool          # True if autocontrast was applied before quality re-test

    def __repr__(self) -> str:
        return (
            f"Frame(ts={self.timestamp_ms}ms, {self.width}×{self.height}, "
            f"brightness={self.brightness:.1f}, sharpness={self.sharpness:.1f}, "
            f"passed={self.passed_quality}, enhanced={self.enhanced})"
        )
```

`slots=True` reduces per-instance memory overhead. For a video with many
extracted frames this compounds: a 9-frame `FrameSet` at default settings uses
roughly 30–40% less memory with slots than without. Requires Python 3.10+.

`jpeg_bytes` is excluded from `__repr__` — printing a `Frame` in logs or a
debugger should show metadata, not binary data.

### FrameSet

```python
@dataclass
class FrameSet:
    file_path:  Path
    file_type:  str           # "image" | "video"
    frames:     list[Frame]   # always ≥ 1 entry (see quality gate below)
    rejected:   list[Frame]   # frames that failed quality and were not promoted

    @property
    def best_frame(self) -> Frame:
        """Return the highest-quality frame: passed_quality first, then brightness."""
        return max(self.frames, key=lambda f: (f.passed_quality, f.brightness))

    def __repr__(self) -> str:
        return (
            f"FrameSet({self.file_path.name!r}, {self.file_type}, "
            f"{len(self.frames)} frames, {len(self.rejected)} rejected)"
        )
```

**Quality gate guarantee:** `FrameSet.frames` always contains at least one
frame. If all candidates fail the quality threshold after filtering (and after
any enhancement pass), the frame with the highest brightness is promoted from
the rejected pool into `frames` with `passed_quality=False`. This keeps the
"no usable frames" decision at the call site, where the stage has enough context
to choose between skipping VLM inference, falling back to a transcript, or
attempting description anyway.

**`best_frame` property:** Returns the highest-quality frame from `frames`,
prioritising `passed_quality=True` over raw brightness. Stages that need a
single representative frame (describe fallback, thumbnail generation) use this
rather than reimplementing selection logic independently.

For **still images**, description is always attempted regardless of
`passed_quality` — there is no transcript fallback for images, and the image
is the only available visual signal.

For **video**, if no frame in `frames` has `passed_quality=True` and a
transcript is available, the describe stage may skip VLM inference. The stage
checks `any(f.passed_quality for f in frameset.frames)` and decides based on
its own fallback logic.

### AudioTrack

```python
@dataclass
class AudioTrack:
    file_path:        Path
    wav_path:         Path          # extracted 16 kHz mono WAV; context manager owns cleanup
    sample_rate:      int           # always 16000 after normalisation
    duration_ms:      int
    has_speech:       bool | None   # None if VAD not run; False = silence-only
    peak_db:          float | None  # peak amplitude in dBFS before normalisation
    has_clipping:     bool          # True if peak amplitude saturated (distorted source)
    normalised:       bool          # True if level normalisation was applied
    segment_start_ms: int | None    # None = full file
    segment_end_ms:   int | None    # None = full file
    owned:            bool          # True if wav_path is a tmp file managed by context manager
```

**`has_speech` persistence:** After `prepare_audio()` runs (either standalone or
from within a stage), `has_speech` should be written to a `has_speech` column on
`corpus_files` (nullable BOOLEAN, new migration). The column is `NULL` until
audio preparation runs; `False` means VAD found no speech; `True` means speech
detected. Stages check this column before loading Whisper or Resemblyzer:

```python
if get_has_speech(db, file_id) is False:
    continue   # skip without loading model
```

**`has_clipping` logging:** Clipped audio produces degraded transcription and
voice embeddings. Stages receiving an `AudioTrack` with `has_clipping=True`
should emit a `logger.warning` so the user can identify problematic source files.

---

## Profiles

A profile is a named bundle of parameter overrides for `prepare_visual()` or
`prepare_audio()`. Profiles allow a KB to declare its corpus characteristics
once in `config.yaml` rather than threading individual parameters through every
stage call. Stages pull `config.visual_profile` / `config.audio_profile` rather
than specifying settings directly.

### VisualProfile

```python
@dataclass
class VisualProfile:
    name:                   str
    max_px:                 int = 1024
    max_frames:             int | None = None
    scene_detection:        bool = False
    scene_detection_method: str = "phash"   # "phash" | "ffmpeg" | "pyscenedetect"
    max_scene_frames:       int | None = None
    enhance:                bool = False    # autocontrast pass on brightness-failed frames
```

Built-in named profiles:

| Profile | Intent | Key settings |
|---|---|---|
| `DEFAULT` | Modern digital media | max_px=1024, enhance=False, scene_detection=False |
| `ARCHIVAL` | Digitised/scanned material, old recordings | max_px=1024, enhance=True |
| `DOCUMENTARY` | Long-form multi-scene video | max_px=1024, scene_detection=True, max_scene_frames=3 |
| `QUICK` | Low-cost preview or re-index pass | max_px=512, max_frames=3 |

### AudioProfile

```python
@dataclass
class AudioProfile:
    name:      str
    normalise: bool = False   # peak-normalise WAV before writing
    vad:       bool = True    # run RMS silence gate to set has_speech
```

Built-in named profiles:

| Profile | Intent | Key settings |
|---|---|---|
| `DEFAULT` | Modern recordings | normalise=False, vad=True |
| `ARCHIVAL` | Old/quiet recordings | normalise=True, vad=True |

Custom profiles can be defined in `config.yaml` using the same schema and
referenced by name from any stage.

Individual parameters passed to `prepare_visual()` or `prepare_audio()` at the
call site override the active profile for that call only.

### Config field migration

`Config` already carries `describe_min_frame_brightness`,
`describe_min_frame_sharpness`, `describe_frames`, and `scene_threshold`. These
fields move into `VisualProfile` as the canonical location. The `Config` fields
are retained as deprecated fallbacks during the transition sprint: if
`visual_profile` is not set, `prepare_visual()` reads from the legacy `Config`
fields directly. Once all callers reference a profile, the legacy fields are
removed in a follow-on sprint.

---

## prepare_visual()

```python
def prepare_visual(
    file_path: Path,
    config: Config,
    *,
    profile: VisualProfile | None = None,   # falls back to config.visual_profile
    max_px: int | None = None,              # overrides profile.max_px
    max_frames: int | None = None,
    scene_detection: bool | None = None,
    scene_detection_method: str | None = None,
    max_scene_frames: int | None = None,
    enhance: bool | None = None,
) -> FrameSet | None:
```

**Never raises.** Returns `None` if the file has no visual content (audio-only
file, unsupported format) or if any unrecoverable error occurs (corrupt file,
truncated data, permission error). An outer `try/except Exception` wraps the
entire function body; failures are logged at WARNING level with the file path and
exception string. This makes all downstream stages safe by construction — no
stage needs to handle `prepare_visual()` raising.

All keyword arguments override the corresponding field in `profile`; omitted
arguments use the profile value.

### Image preparation path

1. Detect format. If the file is an animated GIF, route to the video preparation
   path and treat keyframes as a multi-frame `FrameSet`. Detection uses PIL's
   `format` attribute and `n_frames > 1` check before any other processing.
2. Apply `ImageOps.exif_transpose()` to correct EXIF orientation.
   **This step is unconditional.** Face bounding boxes and spatial quality
   metrics computed on an un-rotated phone photo produce wrong coordinates;
   writeback would assign misaligned regions back to the file. PIL does not
   auto-apply EXIF rotation in all versions.
3. Check image dimensions against PIL's decompression bomb threshold (default
   178 MP). If the image exceeds the limit, log a WARNING and either raise the
   limit deliberately for known archival sources or return `None`. Do not let PIL
   raise `DecompressionBombError` unhandled — a single oversized file would
   crash the stage worker for that file.
4. Normalise colour space to RGB (handles CMYK scans, palette images, grayscale,
   16-bit, and alpha-channel variants).
5. Resize so the longer dimension does not exceed `max_px` (default 1024).
6. Encode as JPEG at quality 85.
7. Record `width` and `height` from the encoded output.
8. Compute brightness and sharpness via `_frame_quality()`.
9. If `enhance=True` and brightness is below threshold: apply
   `ImageOps.autocontrast(cutoff=1)`, re-encode, recompute brightness. If the
   frame now passes, set `passed_quality=True` and `enhanced=True`. Enhancement
   is brightness-only; sharpening is not attempted — blurry frames are not
   recoverable by sharpening.
10. Set `passed_quality` based on final brightness and sharpness vs thresholds.
11. Return a single-frame `FrameSet`. The quality gate guarantee applies: if the
    frame fails, it is still included in `frames` with `passed_quality=False`.

**Additional image sources:**

- **Animated GIF** — detected at step 1 and routed to the video preparation path.
  ffmpeg extracts keyframes; the resulting frames go through the same quality
  filter and pHash dedup as video frames, producing a multi-frame `FrameSet`.
- **RAW formats** — if PIL cannot open the file, fall back to the embedded JPEG
  thumbnail (present in most RAW formats from common camera makers). Requires
  `rawpy` for full-decode support; thumbnail fallback needs no extra dep.
- **Scanned material with consistent borders** — optionally apply `Image.getbbox()`
  to auto-crop uniform black or white borders before encoding.

### Video preparation path

#### Step 0 — Error handling and partial results

`_extract_frames()` returns however many frames it successfully extracted. If
ffmpeg fails for some timestamps (corrupt segment, seek error), the partial list
is used as-is. A `FrameSet` with fewer frames than `max_frames` is valid; stages
must not assume exact counts. If zero frames are extracted (complete ffmpeg
failure), `prepare_visual()` returns `None`.

#### Step 0.5 — Timeout budget

A total timeout budget is enforced across all frame extractions for a single
file, not per-frame. Default: `config.prepare_frame_timeout_s` (120 seconds).
If the budget is exhausted mid-extraction, the frames collected so far are used
and a WARNING is logged with the file path and elapsed time. This prevents a
single malformed video from blocking a pipeline worker indefinitely.

#### Step 1 — Scene detection (optional, default off)

Scene detection defaults to `False`. When enabled, `scene_detection_method`
selects the implementation:

**`"phash"` — pHash distance on dense uniform sample (default method, no new deps)**

Sample `scene_probe_frames` frames uniformly (e.g. 3× `max_frames`). Walk the
sequence; when the pHash distance between adjacent frames exceeds
`config.scene_threshold * 64`, mark a scene boundary. Already partially
implemented in `_select_scene_frames()`. For continuous home video with no hard
cuts, this method is likely more appropriate than the ffmpeg filter, which is
tuned for abrupt colour histogram changes.

**`"ffmpeg"` — ffmpeg `select=gt(scene,N)` filter**

Use ffmpeg's built-in scene scoring during extraction. No extra Python
dependencies. Produces accurate boundary timestamps. Timestamp extraction
requires parsing `showinfo` filter output from stderr, which is fragile; the
implementation must handle this carefully. Better suited to content with hard
cuts (documentaries, edited footage) than to continuous home video.

**`"pyscenedetect"` — PySceneDetect library**

Most accurate detection; adds a dependency and a separate processing pass.
Reserve if the ffmpeg filter proves insufficient for the target corpus.

When `scene_detection=False`, all frames are assigned `scene_id=0`.

#### Step 2 — Per-scene frame sampling

For each detected scene:
- Sample up to `max_scene_frames` frames uniformly across the scene's time range.
- If `max_scene_frames` is not set, distribute `max_frames` evenly across scenes
  (`max_scene_frames = max(1, max_frames // n_scenes)`).

Total extracted frames are capped at `max_frames` regardless of scene count.

#### Step 3 — pHash deduplication

Compute pHash for every extracted frame. Walk the list sorted by timestamp;
discard any frame whose pHash distance from the previous kept frame is below
`config.phash_threshold`. Removes near-identical frames that survived scene
boundaries (static title cards, fade holds).

The pHash computed here is stored in each `Frame.phash` — no second computation
needed by the Hash stage.

#### Step 4 — Quality filtering

Run `_frame_quality()` (brightness + Laplacian sharpness) on every remaining
frame.

If `enhance=True`: frames below the brightness threshold receive one
`ImageOps.autocontrast(cutoff=1)` pass and are re-tested. `Frame.enhanced=True`
is set for any frame that received this treatment.

Frames that pass go to `FrameSet.frames`; frames that fail go to
`FrameSet.rejected`. The quality gate guarantee applies: if `frames` would be
empty, the frame with the highest brightness from `rejected` is promoted into
`frames` with `passed_quality=False`.

---

## prepare_audio()

```python
def prepare_audio(
    file_path: Path,
    config: Config,
    *,
    profile: AudioProfile | None = None,    # falls back to config.audio_profile
    normalise: bool | None = None,          # overrides profile.normalise
    vad: bool | None = None,                # overrides profile.vad
    segment_start_ms: int | None = None,    # extract a time window only
    segment_end_ms:   int | None = None,
) -> AudioTrack | None:
```

Returns `None` if the file has no audio stream. Used as a context manager so
the temporary WAV file is cleaned up reliably:

```python
with prepare_audio(file_path, config) as track:
    if track is None:
        continue
    if track.has_clipping:
        logger.warning("audio: clipping detected in %s", file_path)
    if track.has_speech:
        transcribe(track.wav_path)
        embed_voice(track.wav_path)
```

### Preparation steps

1. Probe for audio stream presence via ffprobe (already done in `_extract_audio`).
   Return `None` if absent.
2. Extract and normalise in one ffmpeg call: mono downmix, resample to 16 kHz,
   WAV PCM. If `segment_start_ms` / `segment_end_ms` are set, pass `-ss` / `-to`
   to extract only that window.
3. Detect clipping: scan the WAV for samples at or near the INT16 maximum.
   Set `has_clipping=True` if any block saturates. Clipped audio produces
   degraded transcription; surfaces as a loggable warning in stages.
4. If `normalise=True`: peak-normalise the WAV in memory via numpy (no extra
   dep). Record original `peak_db` before normalisation. Set `normalised=True`.
   Particularly useful for quiet archival recordings and far-field microphones.
5. If `vad=True`: compute RMS energy over 100 ms windows. If all windows are
   below the silence threshold, set `has_speech=False`. This allows the transcribe
   stage to skip Whisper on silent tracks without loading the model.
6. Return `AudioTrack`; the WAV file lives in a `tempfile.TemporaryDirectory`
   managed by the context manager.

### Cross-stage sharing and its limitation

The `_extract_audio()` duplication exists across three independent pipeline
stages (transcribe, voice, diarize). An `AudioTrack` created by one stage
cannot be passed to another because the stages run sequentially as separate
DAG nodes with no shared in-memory state.

Within a single stage, `prepare_audio()` still improves correctness and API
clarity (clipping detection, normalisation, VAD gate, typed metadata).

Full cross-stage sharing requires one of:

- **Combined audio stage** — transcribe + voice embedding + diarization run in
  a single stage that creates one `AudioTrack` and passes `wav_path` to all
  three. This is the most efficient path but requires redesigning the DAG node
  boundaries.
- **Short-lived WAV cache in corpus** — store the extracted WAV path in a temp
  column; subsequent stages reuse it if it still exists on disk. Fragile and
  adds invalidation complexity.

The combined audio stage is the better option if the multi-decode cost proves
significant. It would replace three DAG nodes with one, and `prepare_audio()`
provides the clean internal API that stage would use.

---

## prepare_file()

```python
def prepare_file(
    file_path: Path,
    config: Config,
    *,
    need_frames: bool = True,
    need_audio: bool = True,
    visual_profile: VisualProfile | None = None,
    audio_profile: AudioProfile | None = None,
    **visual_kwargs,
) -> tuple[FrameSet | None, AudioTrack | None]:
```

For video files where both frames and audio are needed, `prepare_file()` issues
**one ffmpeg command** that writes selected frame JPEGs and the normalised audio
WAV in parallel output streams. This halves source file I/O compared to two
independent calls.

For image files: delegates to `prepare_visual()` only; audio slot is `None`.
For audio-only files: delegates to `prepare_audio()` only; visual slot is `None`.

`prepare_file()` is the preferred entry point when a stage needs both. Stages
that only need one call `prepare_visual()` or `prepare_audio()` directly.

---

## Opportunistic Cache

The frame cache is entirely optional. Stages that need frames follow this
pattern:

```python
frameset = load_frameset(db, file_id, profile=active_profile)
if frameset is None:
    frameset = prepare_visual(file_path, config)
    if frameset is not None and cache_enabled:
        save_frameset(db, file_id, frameset, profile=active_profile)
```

Users who never run `enrich prepare` get current behaviour unchanged. Users who
do get true cross-stage decode reduction — describe, face, and quality all read
the same cached frames.

### `load_frameset()`

```python
def load_frameset(
    db: sqlite3.Connection,
    file_id: int,
    profile: VisualProfile,
) -> FrameSet | None:
```

Reads rows from `video_frames` where `file_id` matches and
`prepare_profile = profile.name`. Returns a `FrameSet` reconstructed from stored
JPEG bytes and metadata, or `None` if no rows exist for that file/profile
combination. Returns `None` (not raises) on any DB error.

### Staleness detection

The cache check is: *rows exist* **and** *profile name matches*. If the user
changes profile settings and re-runs `enrich prepare`, old rows (under the old
profile name) are replaced. Stages will not silently use frames extracted with
different parameters. A `prepare_profile` column is stored alongside each frame
row in `video_frames`.

### Atomic cache writes

All frame rows for a single file are written in a single transaction. If the
prepare pass is interrupted mid-file, no partial rows are committed. The
existence check relies on this guarantee: if any row for a file/profile
combination exists, all rows for that file/profile are present and complete.

### `enrich prepare` command

A standalone `enrich prepare` CLI command pre-populates the frame cache without
running any downstream stage. Options:

```
enrich prepare                      # all files
enrich prepare --videos-only        # skip images (fast to decode on demand)
enrich prepare --profile archival   # extract under a specific profile
```

Images are fast enough to decode on demand; videos benefit most from caching.
The command uses the existing `ProgressReporter` protocol so SSE and CLI
progress bars work identically to other stages.

---

## Cross-Stage Sharing: In-Memory vs Persisted

### Option A — In-memory (transient, within a stage run) — recommended first

`prepare_visual()` is called at the start of each stage that needs frames. The
resulting `FrameSet` is used for the stage's work and then discarded. The
primary gains are **code quality and consistency of quality filtering**, not
decode reduction across stages.

**Pros:** No DB schema change, no storage cost, simple.  
**Cons:** Each stage that runs separately still re-decodes the video.

### Option B — Opportunistic cache via `video_frames.jpeg_blob`

The `video_frames` table already stores `frame_index`, `timestamp_ms`, and
`phash_hex` per frame. Add `jpeg_blob`, `brightness`, `sharpness`, `scene_id`,
and `prepare_profile` columns. After `enrich prepare` runs (or after the first
stage populates the cache), subsequent stages call `load_frameset()` and bypass
ffmpeg entirely.

**Pros:** Single decode per file across all stages; face detection and describe
share exactly the same frames; thumbnail endpoint becomes trivial.  
**Cons:** Significant DB size increase for large video corpora (estimate: ~100 MB
per 1,000 videos at 9 frames × 50 KB/frame; a 50,000-file corpus could add 5 GB
to `corpus.db`). Invalidation handled by `prepare_profile` column.

At very large corpus scales, an external frame cache directory (`tmp/frames/`)
may be preferable to SQLite BLOBs. The cache strategy should be configurable.

### Recommendation

Start with Option A. Build the `src/media/` API and wire stages to it. Add
Option B (opportunistic cache + `load_frameset()` + `enrich prepare`) as a
follow-on sprint once the API is stable.

---

## Config Fields (proposed additions)

| Field | Default | Description |
|---|---|---|
| `visual_profile` | `"default"` | Named visual profile for this KB |
| `audio_profile` | `"default"` | Named audio profile for this KB |
| `describe_max_image_px` | `1024` | Max dimension for image normalisation (was hardcoded 512) |
| `describe_max_scene_frames` | `3` | Max frames extracted per detected scene |
| `describe_min_frame_brightness` | `30.0` | Already exists; deprecated in favour of profile |
| `describe_min_frame_sharpness` | `0.0` | Already exists; deprecated in favour of profile |
| `scene_detection` | `false` | Enable scene boundary detection (default off) |
| `scene_threshold` | `0.4` | Already exists; used by pHash and ffmpeg scene methods |
| `prepare_frame_timeout_s` | `120` | Total ffmpeg timeout budget per file across all frame extractions |

---

## Current State — What Already Exists

The following pieces are already implemented and would be folded into
`prepare_visual()` rather than rewritten:

- `_extract_frames()` — ffmpeg frame extraction at given timestamps (`video.py`)
- `_compute_phash()` — pHash computation on JPEG bytes (`video.py`)
- `_phash_distance()` — Hamming distance between pHash strings (`video.py`)
- `_select_scene_frames()` — pHash-based dedup of a frame list (`video.py`)
- `_frame_quality()` — brightness + Laplacian sharpness (`video.py`)
- `_write_debug_frames()` — optional debug output to `debug.frames_dir` (`video.py`)
- `get_video_frames()` — thin orchestrator (uniform / scene / collage modes) (`video.py`)
- `make_collage()` — stitch frames into a grid for hashing (`video.py`)
- `_describe_image()` — image open + resize + encode (`describe.py`)
- `_hash_image()` — PIL open + pHash + dhash + area_hash (`hash.py`)
- `_hash_video()` — calls `get_video_frames()` + collage hash (`hash.py`)
- `_extract_audio()` — ffmpeg audio extraction to temp WAV (`transcribe.py`)

### What needs to change

- `_describe_image()` replaced by the image path in `prepare_visual()`
- `describe_video()` replaced by the VLM loop consuming a `FrameSet`
- `run_describe()` calls `prepare_visual()` once per file; the `is_image` /
  `is_video` branch dissolves — `prepare_visual()` handles both
- `_hash_video()` receives a `FrameSet` instead of calling `get_video_frames()`
- `quality.py` receives a `FrameSet` for video files instead of calling
  `get_video_frames()` directly
- `_extract_audio()` in `transcribe.py` replaced by `prepare_audio()`

**Note on `_hash_image()`:** This function needs the raw PIL `Image` object for
`_compute_area_hash()` (`img.crop()` on the full-resolution image). A `FrameSet`
carries JPEG bytes at reduced resolution, which would degrade area hash quality.
`_hash_image()` should continue to open PIL directly from the source file for
now. The Hash stage is not a primary beneficiary of `prepare_visual()`.

---

## Downstream Consumers

### Visual

| Consumer | Current | With FrameSet |
|---|---|---|
| Describe (VLM) | Calls `_describe_image` or `describe_video` | Iterates `frameset.frames`, calls `_describe_frame` |
| Hash (video) | Calls `_hash_video()` → `get_video_frames()` | Receives `FrameSet`, reads `.phash` per frame (already computed) |
| Hash (image) | Opens PIL directly | Continues to open PIL directly (area_hash requires full-res Image) |
| Face detection | Opens PIL per file | Iterates `frameset.frames` directly |
| Quality metrics | Opens PIL / calls `get_video_frames()` | Reads `Frame.brightness` / `.sharpness`; video path receives `FrameSet` |
| Web UI thumbnail | — | `GET /api/files/{id}/thumbnail` reads `best_frame.jpeg_bytes` from cache |

### Audio

| Consumer | Current | With AudioTrack |
|---|---|---|
| Transcribe (Whisper / whisper-cli) | Calls `_extract_audio()` per file | Receives `AudioTrack.wav_path`; skips if `has_speech=False` |
| Voice embedding (Resemblyzer) | Calls `_extract_audio()` per file | Receives `AudioTrack.wav_path`; skips if `has_speech=False` |
| Diarization (pyannote) | Calls `_extract_audio()` per file | Receives `AudioTrack.wav_path`; skips if `has_speech=False` |

---

## UX and Pipeline Integration

### Thumbnail endpoint

`GET /api/files/{id}/thumbnail` — returns `best_frame.jpeg_bytes` from the frame
cache for a given file. Returns 404 if no cached frame exists for that file.
Requires Option B (opportunistic cache) to be populated.

The endpoint enables:
- Visual corpus browser (grid view of all ingested files)
- Face cluster review showing actual frame crops without runtime PIL processing
- Describe review showing the frames sent to the VLM
- Quality review showing the frames that triggered quality flags

### Health check: cache state

A new Group D health check entry:

```
Frame cache: 1,234 files prepared (487 MB) / 56 files pending
```

Shows: number of files with cached frames, estimated storage used, and files
that have been ingested but not yet prepared. This removes user uncertainty about
whether `enrich prepare` has run, and surfaces corpus size implications before
the DB grows unexpectedly.

### Pipeline UI: prepare as a visible optional step

The pipeline dashboard shows `enrich prepare` as a greyed-out optional step
between Hash and Describe, with a Run button and a tooltip explaining the
performance benefit. It does not block the main pipeline — stages proceed
normally whether or not the cache is populated.

### Progress reporting

`enrich prepare` reports progress through the existing `ProgressReporter`
protocol: one `update()` call per file processed. SSE stream and CLI progress
bar work identically to other stages. The same reporter is used when a stage
populates the cache on first call.

---

## Open Questions for the Review Session

1. **Scene detection method** — For home video (continuous recording, no hard
   cuts), the pHash method is likely more appropriate than the ffmpeg scene
   filter, which is optimised for abrupt colour histogram changes. Does the
   target corpus contain enough multi-scene content to justify `scene_detection`
   at all, or should it remain off by default and be profile-selectable only?

2. **Persisted vs transient frames** — Start with in-memory (Option A), then add
   opportunistic cache (Option B) as a follow-on sprint. Agreed?

3. **ffmpeg scene filter timestamp extraction** — The `showinfo` stderr parsing
   approach is fragile. Is there a cleaner way to get frame timestamps from the
   ffmpeg scene filter, or should `"ffmpeg"` method be deferred until there is
   a concrete use case that `"phash"` cannot handle?

4. **VAD method** — RMS energy is trivial to compute (numpy, no extra deps).
   A proper VAD (Silero, WebRTC) would be more accurate but adds a dependency.
   Is RMS sufficient as a first gate?

5. **Context manager for AudioTrack** — confirmed: `with prepare_audio(...) as track:`
   is the pattern. The WAV file must outlive the stage function call that creates
   it but be deleted before the stage exits.

6. **Combined audio stage** — transcribe + voice + diarize are the natural
   candidates for collapsing into a single DAG node that creates one `AudioTrack`
   and passes `wav_path` to all three. Is this the right approach to the
   cross-stage sharing problem, or is independent stage structure preferred for
   pipeline flexibility?

7. **Profiles in config.yaml** — custom profiles defined by the user should use
   the same schema as the built-in named profiles. What is the config.yaml key
   structure? Proposed: `visual_profiles:` dict keyed by name, with an
   `extends:` field for inheritance from a built-in.

8. **Sprint sequencing** — recommended order:
   - Sprint 1: `src/media/` + `prepare_visual()` in-memory (Option A); wire into
     `describe.py`, `face.py`, `quality.py`; unit tests for all non-GPU paths
   - Sprint 2: `prepare_audio()` in-memory; wire into `transcribe.py`,
     `voice.py`, `attribute_speakers.py`; persist `has_speech` to `corpus_files`
   - Sprint 3: Opportunistic cache — `load_frameset()`, `video_frames` schema
     extension, `enrich prepare` CLI, thumbnail endpoint, health check entry
   - Sprint 4: `prepare_file()` and combined audio stage (only once DAG
     restructuring is approved)

9. **`load_frameset()` location** — belongs in `src/media/frameset.py` (it is a
   `FrameSet` factory) but accepts a raw `sqlite3.Connection`. Is passing the
   raw connection acceptable, or should a named query function live in
   `db/corpus.py` and be imported? The former keeps `src/media/` self-contained
   at the cost of placing a DB query outside the named-function convention.

10. **Frame cache storage at scale** — at ~100 MB per 1,000 videos (9 frames ×
    50 KB/frame), a 50,000-file corpus adds ~5 GB to `corpus.db`. At that scale,
    an external `tmp/frames/<file_id>/` directory may be preferable to SQLite
    BLOBs. Should the cache strategy (inline BLOB vs filesystem) be a config
    option, or fixed to one approach initially?

---

## Related Concepts (already logged in memory)

- **Summarize vocabulary correction — Option A** (fuzzy pre-filter at assembly
  time): scan transcript for tokens within edit distance N of known vocab terms;
  pass only matched correction pairs to the summarize LLM.
- **Summarize vocabulary correction — Option B** (transcript pre-correction
  pass): a `corrections` table in `knowledge.db` applies `str.replace()`
  corrections before the transcript reaches the LLM.
- **Text-based people tagging**: extend Entity Match to scan filenames and
  transcripts for known person names from the people registry.
