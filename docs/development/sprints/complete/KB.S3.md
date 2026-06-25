# Sprint KB.S3 — FrameSet: Shared Visual Preparation Layer

## Scope

Introduce `src/media/frameset.py` with `Frame`, `FrameSet`, and
`VisualProfile` dataclasses and a `prepare_visual()` factory function. Wire
the three visual-consuming stages to use it, replacing independent PIL and
ffmpeg calls. Primary win: consistent quality filtering across describe, face,
and quality stages, which directly improves face centroid quality by ensuring
face detections are not made on dark or blurry frames.

No schema changes. No new API endpoints or CLI commands. No UI changes.

## Builds On

- KB.S2 (AudioTrack): `src/media/` package exists; same context-manager and
  "never raises" patterns established
- KB.P15 (Face Stage): face.py exists and opens PIL directly per file
- KB.11 (Quality Stage): quality.py calls `get_video_frames()` for video
- KB.8 (Describe Stage): describe.py has `_describe_image()` and delegates
  video to `video.py:describe_video()`

## Baseline

Record current test count at sprint start (see ROADMAP.md).

## Deliverables

### New module

**`src/media/frameset.py`**

```python
@dataclass(slots=True)
class Frame:
    jpeg_bytes:     bytes
    width:          int
    height:         int
    timestamp_ms:   int | None   # None for still images
    scene_id:       int | None   # None if scene detection not run
    brightness:     float        # mean grayscale 0–255
    sharpness:      float        # variance of discrete Laplacian
    phash:          str | None
    passed_quality: bool
    enhanced:       bool         # True if autocontrast was applied

@dataclass
class FrameSet:
    file_path:  Path
    file_type:  str              # "image" | "video"
    frames:     list[Frame]      # always ≥ 1 (quality gate guarantee)
    rejected:   list[Frame]

    @property
    def best_frame(self) -> Frame: ...

@dataclass
class VisualProfile:
    name:                   str
    max_px:                 int  = 1024
    max_frames:             int | None = None
    scene_detection:        bool = False
    scene_detection_method: str  = "phash"
    max_scene_frames:       int | None = None
    enhance:                bool = False

# Built-in named profiles
DEFAULT    = VisualProfile("default")
ARCHIVAL   = VisualProfile("archival",    enhance=True)
DOCUMENTARY= VisualProfile("documentary", scene_detection=True, max_scene_frames=3)
QUICK      = VisualProfile("quick",       max_px=512, max_frames=3)

def prepare_visual(
    file_path: Path,
    config: Config,
    *,
    profile: VisualProfile | None = None,
    max_px: int | None = None,
    max_frames: int | None = None,
    scene_detection: bool | None = None,
    enhance: bool | None = None,
) -> FrameSet | None: ...
```

**Image preparation path:**

1. Detect animated GIF (`n_frames > 1`) → route to video path.
2. Apply `ImageOps.exif_transpose()` unconditionally.
3. Check PIL decompression bomb threshold; log WARNING and return `None` if
   exceeded rather than letting PIL raise.
4. Normalise colour space to RGB.
5. Resize so longer dimension ≤ `max_px`.
6. Encode as JPEG at quality 85.
7. Compute brightness and sharpness via `_frame_quality()`.
8. If `enhance=True` and brightness below threshold: apply
   `ImageOps.autocontrast(cutoff=1)`, re-encode, recompute.
9. Set `passed_quality`. Apply quality gate guarantee.
10. Return single-frame `FrameSet`.

**Video preparation path:**

1. Get duration via ffprobe.
2. If `scene_detection=True` and `scene_detection_method="phash"`: use
   existing `_select_scene_frames()` pHash dedup logic (moved from
   `video.py`). The "ffmpeg" and "pyscenedetect" methods are not implemented.
3. Sample `max_frames` timestamps uniformly; extract via ffmpeg.
4. pHash deduplication: walk sorted by timestamp; discard frames within
   `config.phash_threshold` of the previous kept frame.
5. Quality filter via `_frame_quality()`. Apply enhancement if `enhance=True`.
6. Apply quality gate guarantee: if all frames fail, promote highest-brightness
   rejected frame with `passed_quality=False`.
7. Return `FrameSet`.

**Never raises.** Returns `None` on unrecoverable error (corrupt file, no
frames extracted, unsupported format). Failures logged at WARNING.

### What moves from existing modules

From **`src/stages/video.py`** → **`src/media/frameset.py`**:
- `_frame_quality()` — brightness + sharpness computation
- `_select_scene_frames()` — pHash-based scene diversity filter
- `_compute_phash()` and `_phash_distance()` — moved as private helpers

These functions remain in `video.py` until the end of this sprint to avoid
breaking changes mid-refactor; they are removed once all callers use
`prepare_visual()`.

`make_collage()` and `get_video_frames()` remain in `video.py` — `make_collage`
is used by the hash stage (collage mode), and `get_video_frames` may still be
used by the hash stage's video path.

### Modified modules

**`src/stages/describe.py`**

- `_describe_image(file_path, model, prompt)` replaced by:
  ```python
  frameset = prepare_visual(file_path, config)
  if frameset:
      description = _describe_frame(frameset.best_frame.jpeg_bytes, session, prompt)
  ```
- `describe_video()` in `video.py` replaced by a loop over `frameset.frames`
  calling `_describe_frame()` per frame, then `_aggregate_descriptions()`.
  The `is_image`/`is_video` branch in `run_describe()` dissolves — both routes
  call `prepare_visual()` and the `FrameSet.file_type` drives frame count.
- `run_describe_file()` (quick-describe) also uses `prepare_visual()`.
- Quality check for images: always attempt VLM inference regardless of
  `passed_quality` — no transcript fallback for images.
- Quality check for video: if no frame has `passed_quality=True` and a
  transcript exists, describe stage may log a warning but still proceeds
  (stage decides, not `prepare_visual`).

**`src/stages/face.py`**

- Replace direct PIL open with `prepare_visual()` call.
- Iterate `frameset.frames` for face detection rather than opening PIL per
  file. Dark/blurry frames with `passed_quality=False` are skipped for face
  detection (log at DEBUG).
- For video: extract one representative frame per scene if scene detection
  enabled, otherwise iterate `frameset.frames` as returned.

**`src/stages/quality.py`**

- For images: read `Frame.brightness` and `Frame.sharpness` directly from
  the `FrameSet` rather than recomputing.
- For video: read per-frame metrics from `FrameSet.frames` rather than
  calling `get_video_frames()`.

**`src/stages/hash.py`**

- Video path: pass `FrameSet.frames` (as `[f.jpeg_bytes for f in frameset.frames]`)
  to `make_collage()` rather than calling `get_video_frames()`. Per-frame
  pHash values already computed; read `Frame.phash` directly.
- Image path: **no change** — `_hash_image()` continues to open PIL directly
  from the source file. The area hash (`_compute_area_hash`) requires
  full-resolution PIL Image for `img.crop()` operations; the JPEG bytes in
  `FrameSet` are at reduced resolution and would degrade hash quality.

**`src/config.py`**

- Add `visual_profile: str = "default"` — named visual profile for the KB.
- Existing `describe_min_frame_brightness`, `describe_min_frame_sharpness`,
  `describe_frames`, `scene_threshold` retained as deprecated fallbacks read
  by `prepare_visual()` when no profile is set. Removal deferred to a later
  sprint once all callers use profiles.

## Acceptance Criteria

1. `python -m pytest tests/ -q` → all prior tests pass; ≥ 15 new tests pass
2. `ruff check src/ tests/` → 0 errors
3. `from src.media.frameset import Frame, FrameSet, VisualProfile, prepare_visual` succeeds
4. `describe.py` contains no `_describe_image()` definition and no direct
   `Image.open(file_path)` call in the describe path
5. `face.py` contains no direct `Image.open(file_path)` call in the detection
   path (uses `frame.jpeg_bytes` from `FrameSet`)
6. `quality.py` contains no `get_video_frames()` call
7. `hash.py` image path still calls `Image.open()` directly (area hash
   requires full-res); video path does not call `get_video_frames()`
8. `FrameSet.frames` always has ≥ 1 entry (quality gate guarantee holds
   even when all frames fail quality checks)
9. A dark JPEG passed to `prepare_visual()` returns a `FrameSet` with one
   frame where `passed_quality=False` (not `None`)

## Test Targets — ~15 new tests

All in `tests/unit/test_frameset.py` and
`tests/integration/test_visual_preparation.py`.

### Unit tests (no ffmpeg, PIL available)

- `test_frame_dataclass_fields` — construct `Frame`; verify all fields
- `test_frameset_best_frame_prefers_passed_quality` — FrameSet with one
  passed and one failed frame; `best_frame` returns the passed one
- `test_frameset_best_frame_falls_back_to_brightness` — all frames
  `passed_quality=False`; `best_frame` returns highest brightness
- `test_visual_profile_defaults` — DEFAULT profile fields match expected values
- `test_quality_gate_guarantee_promotes_best_rejected` — all frames fail
  quality; `FrameSet.frames` has exactly one entry with `passed_quality=False`
- `test_quality_gate_guarantee_nonempty_when_all_fail` — same as above; len
  of frames is 1, not 0
- `test_phash_dedup_removes_near_duplicate` — two frames with pHash distance
  below threshold; only one survives dedup
- `test_phash_dedup_keeps_diverse_frames` — two frames with pHash distance
  above threshold; both survive

### Integration tests (uses PIL and ffmpeg, synthetic files in tmp_path)

- `test_prepare_visual_image_returns_single_frame` — pass a synthetic JPEG;
  returns `FrameSet` with `file_type="image"` and `len(frames)==1`
- `test_prepare_visual_image_exif_transpose` — pass a JPEG with EXIF
  rotation tag; returned frame dimensions are post-rotation
- `test_prepare_visual_image_resize` — pass a 2000×2000 JPEG with `max_px=512`;
  frame width/height ≤ 512
- `test_prepare_visual_video_returns_multiple_frames` — synthetic MP4;
  `len(frames) > 0`
- `test_prepare_visual_returns_none_for_audio` — pass an MP3; returns `None`
- `test_prepare_visual_never_raises_on_corrupt_file` — truncated JPEG;
  returns `None` without raising
- `test_prepare_visual_dark_image_passed_quality_false` — synthesise a black
  JPEG; frame `passed_quality=False` but still in `frames`

## Design Notes

### Face centroid quality rationale

The primary motivation for this sprint (beyond code cleanliness) is that face
detection on poor-quality frames produces lower-confidence bounding boxes and
embeddings. A centroid built from detections on dark or blurry frames will be
less reliable for matching at scale. Consistent quality filtering across the
describe and face stages ensures both use the same quality bar.

### Scene detection: pHash only

The "ffmpeg" (`select=gt(scene,N)`) and "pyscenedetect" methods are not
implemented. pHash deduplication of uniformly sampled frames handles home
video well (continuous recording, no hard cuts). The ffmpeg filter is designed
for abrupt colour histogram changes and is fragile to parse. Add the ffmpeg
method only if a concrete corpus use case exceeds what pHash can handle.

### `_hash_image()` exception

The hash stage image path continues to open PIL directly. `FrameSet` carries
JPEG bytes at reduced `max_px` resolution; `_compute_area_hash()` crops the
full-resolution image into an 8×8 grid and would produce degraded hashes from
reduced-resolution input. Do not force hash through `prepare_visual()`.

### `video.py` cleanup

`_frame_quality()`, `_select_scene_frames()`, `_compute_phash()`, and
`_phash_distance()` move to `frameset.py`. `describe_video()` is replaced by
the describe stage's FrameSet loop. `get_video_frames()` is retained for the
hash stage's collage mode only. If after this sprint `get_video_frames()` has
no remaining callers, it can be removed in a follow-on cleanup.

### `prepare_visual()` for images always returns a FrameSet

For still images, `FrameSet.frames` always contains the single frame even if
`passed_quality=False`. There is no transcript fallback for images and no
reason to return `None` for a successfully opened image file.

## Out of Scope

- Opportunistic frame cache (Option B) — `load_frameset()`, `save_frameset()`,
  `enrich prepare` CLI, `video_frames` schema extension
- `prepare_file()` combined visual+audio entry point
- Thumbnail API endpoint (requires frame cache)
- Animated GIF detection (deferred; route as single-frame image for now)
- RAW format fallback to embedded JPEG thumbnail
- Scene detection methods other than pHash
