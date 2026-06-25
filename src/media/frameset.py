"""Shared visual preparation layer — EXIF correction, quality filtering, pHash dedup."""
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".m2ts"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Frame:
    jpeg_bytes:     bytes
    width:          int
    height:         int
    timestamp_ms:   int | None
    scene_id:       int | None
    brightness:     float
    sharpness:      float
    phash:          str | None
    passed_quality: bool
    enhanced:       bool


@dataclass
class FrameSet:
    file_path:  Path
    file_type:  str
    frames:     list[Frame]
    rejected:   list[Frame]

    @property
    def best_frame(self) -> Frame:
        passed = [f for f in self.frames if f.passed_quality]
        pool = passed if passed else self.frames
        return max(pool, key=lambda f: f.brightness)


@dataclass
class VisualProfile:
    name:                   str
    max_px:                 int = 1024
    max_frames:             int | None = None
    scene_detection:        bool = False
    scene_detection_method: str = "phash"
    max_scene_frames:       int | None = None
    enhance:                bool = False


DEFAULT     = VisualProfile("default")
ARCHIVAL    = VisualProfile("archival",    enhance=True)
DOCUMENTARY = VisualProfile("documentary", scene_detection=True, max_scene_frames=3)
QUICK       = VisualProfile("quick",       max_px=512, max_frames=3)

_NAMED_PROFILES: dict[str, VisualProfile] = {
    "default":     DEFAULT,
    "archival":    ARCHIVAL,
    "documentary": DOCUMENTARY,
    "quick":       QUICK,
}


# ---------------------------------------------------------------------------
# Private helpers — quality metrics
# ---------------------------------------------------------------------------

def _frame_quality(jpeg_bytes: bytes) -> tuple[float, float]:
    """Return (mean_brightness, sharpness) for a JPEG frame."""
    import io
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(jpeg_bytes)) as img:
        arr = np.array(img.convert("L"), dtype=np.float32)

    brightness = float(arr.mean())
    lap = (
        arr[:-2, 1:-1] + arr[2:, 1:-1]
        + arr[1:-1, :-2] + arr[1:-1, 2:]
        - 4.0 * arr[1:-1, 1:-1]
    )
    sharpness = float(lap.var())
    return brightness, sharpness


def _compute_phash(jpeg_bytes: bytes) -> str | None:
    try:
        import io
        import imagehash
        from PIL import Image
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def _phash_distance(a: str, b: str) -> int:
    try:
        import imagehash
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:
        return 0


def _dedup_phash(frames: list[Frame], threshold: int) -> list[Frame]:
    """Filter Frame list to scene-diverse subset using pHash distance."""
    selected: list[Frame] = []
    prev_phash: str | None = None
    for frame in frames:
        if prev_phash and frame.phash:
            if _phash_distance(prev_phash, frame.phash) < threshold:
                continue
        selected.append(frame)
        prev_phash = frame.phash
    return selected if selected else (frames[:1] if frames else [])


# ---------------------------------------------------------------------------
# Private helpers — ffmpeg / ffprobe wrappers (duplicated from video.py
# to avoid src/media importing from src/stages)
# ---------------------------------------------------------------------------

def _get_duration_ms(file_path: Path, ffprobe: str) -> int | None:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(file_path)],
            capture_output=True, text=True, timeout=30,
        )
        import json
        data = json.loads(result.stdout)
        duration_s = float(data["format"]["duration"])
        return int(duration_s * 1000)
    except Exception as exc:
        logger.warning("frameset: ffprobe failed for %s: %s", file_path, exc)
        return None


def _extract_frames(file_path: Path, ffmpeg: str, timestamps_ms: list[int]) -> list[bytes]:
    frames: list[bytes] = []
    for ts_ms in timestamps_ms:
        ts_s = ts_ms / 1000.0
        try:
            result = subprocess.run(
                [
                    ffmpeg, "-v", "quiet",
                    "-ss", str(ts_s),
                    "-i", str(file_path),
                    "-frames:v", "1",
                    "-f", "image2pipe",
                    "-vcodec", "mjpeg",
                    "pipe:1",
                ],
                capture_output=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout:
                frames.append(result.stdout)
        except Exception as exc:
            logger.warning("frameset: frame extraction at %dms failed: %s", ts_ms, exc)
    return frames


def _write_debug_frames(file_path: Path, frames: list[bytes], debug_dir: Path, tag: str) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem
    for i, jpeg_bytes in enumerate(frames):
        (debug_dir / f"{stem}_{tag}_{i:02d}.jpg").write_bytes(jpeg_bytes)
    logger.debug("frameset: wrote %d debug frames to %s (tag=%s)", len(frames), debug_dir, tag)


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------

def _prepare_image(file_path: Path, config, *, max_px: int, enhance: bool) -> "FrameSet | None":
    import io
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        logger.warning("frameset: Pillow not available: %s", exc)
        return None

    try:
        try:
            img = Image.open(file_path)
            img.load()
        except Image.DecompressionBombError:
            logger.warning("frameset: decompression bomb threshold exceeded for %s", file_path)
            return None

        with img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_px:
                ratio = max_px / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            final_w, final_h = img.size
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            jpeg_bytes = buf.getvalue()

        brightness, sharpness = _frame_quality(jpeg_bytes)
        enhanced = False

        if enhance and brightness < config.describe_min_frame_brightness:
            from PIL import ImageOps as _IO
            with Image.open(io.BytesIO(jpeg_bytes)) as img2:
                img2 = _IO.autocontrast(img2.convert("RGB"), cutoff=1)
                buf2 = io.BytesIO()
                img2.save(buf2, format="JPEG", quality=85)
                jpeg_bytes = buf2.getvalue()
            brightness, sharpness = _frame_quality(jpeg_bytes)
            enhanced = True

        passed = (
            brightness >= config.describe_min_frame_brightness
            and sharpness >= config.describe_min_frame_sharpness
        )

        frame = Frame(
            jpeg_bytes=jpeg_bytes,
            width=final_w,
            height=final_h,
            timestamp_ms=None,
            scene_id=None,
            brightness=brightness,
            sharpness=sharpness,
            phash=_compute_phash(jpeg_bytes),
            passed_quality=passed,
            enhanced=enhanced,
        )
        return FrameSet(file_path=file_path, file_type="image", frames=[frame], rejected=[])

    except Exception as exc:
        logger.warning("frameset: image preparation failed for %s: %s", file_path, exc)
        return None


# ---------------------------------------------------------------------------
# Video preparation
# ---------------------------------------------------------------------------

def _make_frame(jpeg_bytes: bytes, ts_ms: int, max_px: int, config, enhance: bool) -> "Frame | None":
    """Decode a raw JPEG from ffmpeg, resize, compute quality, return Frame."""
    import io
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_px:
                ratio = max_px / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            final_w, final_h = img.size
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            out_bytes = buf.getvalue()

        brightness, sharpness = _frame_quality(out_bytes)
        enhanced = False

        if enhance and brightness < config.describe_min_frame_brightness:
            from PIL import ImageOps
            with Image.open(io.BytesIO(out_bytes)) as img2:
                img2 = ImageOps.autocontrast(img2.convert("RGB"), cutoff=1)
                buf2 = io.BytesIO()
                img2.save(buf2, format="JPEG", quality=85)
                out_bytes = buf2.getvalue()
            brightness, sharpness = _frame_quality(out_bytes)
            enhanced = True

        passed = (
            brightness >= config.describe_min_frame_brightness
            and sharpness >= config.describe_min_frame_sharpness
        )

        return Frame(
            jpeg_bytes=out_bytes,
            width=final_w,
            height=final_h,
            timestamp_ms=ts_ms,
            scene_id=None,
            brightness=brightness,
            sharpness=sharpness,
            phash=_compute_phash(out_bytes),
            passed_quality=passed,
            enhanced=enhanced,
        )
    except Exception as exc:
        logger.warning("frameset: frame decode failed at %dms: %s", ts_ms, exc)
        return None


def _prepare_video(
    file_path: Path,
    config,
    *,
    max_px: int,
    max_frames: int,
    scene_detection: bool,
    max_scene_frames: int | None,
    enhance: bool,
) -> "FrameSet | None":
    duration_ms = _get_duration_ms(file_path, config.ffprobe)
    if not duration_ms or duration_ms <= 0:
        logger.warning("frameset: could not determine duration for %s", file_path)
        return None

    n = max(1, max_frames)
    interval_ms = max(1, duration_ms // (n + 1))
    timestamps_ms = [interval_ms * (i + 1) for i in range(n)]

    raw_frames = _extract_frames(file_path, config.ffmpeg, timestamps_ms)
    if not raw_frames:
        logger.warning("frameset: no frames extracted from %s", file_path)
        return None

    frames: list[Frame] = []
    for ts_ms, raw in zip(timestamps_ms, raw_frames):
        frame = _make_frame(raw, ts_ms, max_px, config, enhance)
        if frame is not None:
            frames.append(frame)

    if not frames:
        return None

    # pHash deduplication
    frames = _dedup_phash(frames, config.phash_threshold)

    # Scene detection: further reduce to diverse subset
    if scene_detection and max_scene_frames and len(frames) > max_scene_frames:
        step = max(1, len(frames) // max_scene_frames)
        frames = frames[::step][:max_scene_frames]

    if config.debug_frames_dir and frames:
        _write_debug_frames(file_path, [f.jpeg_bytes for f in frames], Path(config.debug_frames_dir), "prepare")

    # Separate into passed and rejected
    passed = [f for f in frames if f.passed_quality]
    rejected = [f for f in frames if not f.passed_quality]

    if not passed:
        # Quality gate guarantee: promote highest-brightness rejected frame
        best = max(rejected, key=lambda f: f.brightness)
        logger.info(
            "frameset: all %d frame(s) below quality threshold — promoting best rejected (brightness=%.1f) for %s",
            len(rejected), best.brightness, file_path.name,
        )
        passed = [best]
        rejected = [f for f in rejected if f is not best]

    return FrameSet(file_path=file_path, file_type="video", frames=passed, rejected=rejected)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def prepare_visual(
    file_path: Path,
    config,
    *,
    profile: VisualProfile | None = None,
    max_px: int | None = None,
    max_frames: int | None = None,
    scene_detection: bool | None = None,
    enhance: bool | None = None,
) -> FrameSet | None:
    """Prepare a visual file for AI processing.

    Returns a FrameSet with ≥1 frame, or None on unrecoverable error.
    Never raises.
    """
    ext = file_path.suffix.lower()
    is_image = ext in _IMAGE_EXTS
    is_video = ext in _VIDEO_EXTS

    if not is_image and not is_video:
        logger.debug("frameset: unsupported extension %r for %s", ext, file_path)
        return None

    # Resolve profile: explicit kwarg > config.visual_profile name > DEFAULT
    resolved = profile
    if resolved is None:
        resolved = _NAMED_PROFILES.get(getattr(config, "visual_profile", "default"), DEFAULT)

    # Per-call overrides
    eff_max_px = max_px if max_px is not None else resolved.max_px
    eff_max_frames = max_frames if max_frames is not None else (resolved.max_frames or getattr(config, "describe_frames", 9) or 9)
    eff_scene = scene_detection if scene_detection is not None else resolved.scene_detection
    eff_enhance = enhance if enhance is not None else resolved.enhance

    if is_image:
        return _prepare_image(file_path, config, max_px=eff_max_px, enhance=eff_enhance)
    else:
        return _prepare_video(
            file_path, config,
            max_px=eff_max_px,
            max_frames=eff_max_frames,
            scene_detection=eff_scene,
            max_scene_frames=resolved.max_scene_frames,
            enhance=eff_enhance,
        )
