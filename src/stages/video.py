"""Video frame pipeline for Stage 3a: extract frames, describe each, aggregate."""
import logging
import math
import subprocess
from pathlib import Path

from src.config import Config

logger = logging.getLogger(__name__)


def make_collage(frames: list[bytes], cols: int = 3) -> bytes:
    """Stitch JPEG frames into a grid image, return as JPEG bytes.

    Cell size is fixed at 320x180 so the resulting pHash is resolution-independent.
    Returns empty bytes if frames is empty.
    """
    import io
    from PIL import Image

    if not frames:
        return b""
    cols = min(cols, len(frames))
    rows = math.ceil(len(frames) / cols)
    cell_w, cell_h = 320, 180
    collage = Image.new("RGB", (cols * cell_w, rows * cell_h))
    for i, frame_bytes in enumerate(frames):
        with Image.open(io.BytesIO(frame_bytes)) as img:
            resized = img.convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
        row, col = divmod(i, cols)
        collage.paste(resized, (col * cell_w, row * cell_h))
    buf = io.BytesIO()
    collage.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _get_duration_ms(file_path: Path, ffprobe: str) -> int | None:
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        import json
        data = json.loads(result.stdout)
        duration_s = float(data["format"]["duration"])
        return int(duration_s * 1000)
    except Exception as exc:
        logger.warning("video: ffprobe failed for %s: %s", file_path, exc)
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
                capture_output=True,
                timeout=60,
            )
            if result.returncode == 0 and result.stdout:
                frames.append(result.stdout)
        except Exception as exc:
            logger.warning("video: frame extraction at %dms failed: %s", ts_ms, exc)
    return frames


def _compute_phash(jpeg_bytes: bytes) -> str | None:
    try:
        import imagehash
        from PIL import Image
        import io
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


def _select_scene_frames(frames: list[bytes], phash_threshold: int) -> list[bytes]:
    """Filter a list of JPEG frames to scene-diverse subset using pHash distance."""
    selected: list[bytes] = []
    prev_phash: str | None = None
    for jpeg_bytes in frames:
        phash = _compute_phash(jpeg_bytes)
        if prev_phash and phash:
            if _phash_distance(prev_phash, phash) < phash_threshold:
                continue
        selected.append(jpeg_bytes)
        prev_phash = phash
    return selected if selected else (frames[:1] if frames else [])


def _frame_quality(jpeg_bytes: bytes) -> tuple[float, float]:
    """Return (mean_brightness, sharpness) for a JPEG frame using PIL + numpy only.

    mean_brightness: average grayscale value (0–255); low = dark frame.
    sharpness: variance of the discrete Laplacian (higher = sharper).
    """
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


def _write_debug_frames(file_path: Path, frames: list[bytes], debug_dir: Path, tag: str) -> None:
    """Write JPEG frames to debug_dir for manual inspection."""
    debug_dir.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem
    for i, jpeg_bytes in enumerate(frames):
        (debug_dir / f"{stem}_{tag}_{i:02d}.jpg").write_bytes(jpeg_bytes)
    logger.debug("debug_frames: wrote %d frames to %s (tag=%s)", len(frames), debug_dir, tag)


def get_video_frames(
    file_path: Path,
    config: Config,
    *,
    mode: str = "uniform",
    n_frames: int | None = None,
    interval_seconds: float | None = None,
) -> list[bytes]:
    """Return JPEG frame bytes from a video file.

    mode="uniform"  — n_frames evenly spaced across duration (default)
    mode="scene"    — n_frames sampled then deduplicated by pHash distance
    mode="interval" — one frame every interval_seconds; count derived from duration
    """
    duration_ms = _get_duration_ms(file_path, config.ffprobe)
    if not duration_ms or duration_ms <= 0:
        return []

    if mode == "interval":
        step_ms = int((interval_seconds or 1.0) * 1000)
        n = max(1, duration_ms // step_ms)
        timestamps = [step_ms * (i + 1) for i in range(n)]
    else:
        n = max(1, n_frames or config.describe_frames or 5)
        interval_ms = max(1, duration_ms // (n + 1))
        timestamps = [interval_ms * (i + 1) for i in range(n)]

    frames = _extract_frames(file_path, config.ffmpeg, timestamps)

    if mode == "scene":
        frames = _select_scene_frames(frames, config.phash_threshold)
    elif mode == "collage":
        cols = math.ceil(math.sqrt(max(1, len(frames))))
        collage = make_collage(frames, cols)
        frames = [collage] if collage else []
        if config.debug_frames_dir and frames:
            _write_debug_frames(file_path, frames, Path(config.debug_frames_dir), f"{mode}")
        return frames

    if config.debug_frames_dir and frames:
        _write_debug_frames(file_path, frames, Path(config.debug_frames_dir), f"{mode}")
    return frames


