"""Stage: Technical quality metrics — sharpness, exposure, highlights, shadows."""
import logging
import threading
import time
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)


def _analyze_frame(source) -> dict:
    """Compute quality metrics for a single frame.

    source may be a Path (image file) or bytes (JPEG from video frame extraction).
    Returns dict with keys: sharpness, exposure, highlights, shadows,
    luminance_std_dev, saturation_mean, dominant_hue.
    """
    import io
    import numpy as np
    from PIL import Image

    if isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(source))
    else:
        img = Image.open(source)

    with img:
        gray = np.array(img.convert("L"), dtype=float)
        rgb = np.array(img.convert("RGB"), dtype=float) / 255.0

    # Sharpness — Laplacian variance via finite differences
    d2 = (
        np.roll(gray, 1, axis=0) + np.roll(gray, -1, axis=0)
        + np.roll(gray, 1, axis=1) + np.roll(gray, -1, axis=1)
        - 4.0 * gray
    )
    sharpness = float(np.var(d2))

    # Tonality
    gray_norm = gray / 255.0
    exposure = float(gray_norm.mean())
    luminance_std_dev = float(gray_norm.std())
    highlights = float(np.mean(gray > 250))
    shadows = float(np.mean(gray < 5))

    # Saturation (HSV S channel)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin
    with np.errstate(divide="ignore", invalid="ignore"):
        sat = np.where(cmax > 0, delta / cmax, 0.0)
    saturation_mean = float(sat.mean())

    # Dominant hue (0–360) — only from sufficiently saturated pixels
    saturated = sat > 0.1
    if saturated.any():
        with np.errstate(divide="ignore", invalid="ignore"):
            hue = np.where(
                delta == 0, 0.0,
                np.where(
                    cmax == r, (60.0 * ((g - b) / delta)) % 360,
                    np.where(
                        cmax == g, (60.0 * ((b - r) / delta + 2)) % 360,
                        (60.0 * ((r - g) / delta + 4)) % 360,
                    ),
                ),
            )
        hist, edges = np.histogram(hue[saturated], bins=36, range=(0.0, 360.0))
        peak = int(np.argmax(hist))
        dominant_hue = float((edges[peak] + edges[peak + 1]) / 2.0)
    else:
        dominant_hue = 0.0

    return {
        "sharpness": sharpness,
        "exposure": exposure,
        "highlights": highlights,
        "shadows": shadows,
        "luminance_std_dev": luminance_std_dev,
        "saturation_mean": saturation_mean,
        "dominant_hue": dominant_hue,
    }


def _aggregate_frame_metrics(frames: list[dict]) -> dict:
    """Average metrics across multiple frames (video aggregation)."""
    return {k: sum(f[k] for f in frames) / len(frames) for k in frames[0]}


def run_quality(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    source_id: int | None = None,
    file_type: str | None = None,
    set_id: int | None = None,
) -> dict:
    from src.db.corpus import (
        get_pending_quality_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_quality_score,
        compute_quality_rank_scores,
    )
    from src.media.frameset import prepare_visual

    corpus_conn = open_corpus(corpus_path)
    scored = errors = 0
    start = time.monotonic()

    try:
        pending = get_pending_quality_files(corpus_conn, source_id=source_id, file_type=file_type, set_id=set_id)
        total = len(pending)

        for i, row in enumerate(pending):
            if cancel_event.is_set():
                break

            progress.update(i, total, f"Quality: {i + 1}/{total}")
            file_id = row["id"]
            file_type = row["file_type"] or ""

            try:
                if file_type == "images":
                    frameset = prepare_visual(Path(row["path"]), config)
                    if frameset is None:
                        errors += 1
                        continue
                    src = frameset.frames[0] if frameset.frames else frameset.rejected[0]
                    metrics = _analyze_frame(src.jpeg_bytes)
                    frame_count = 1
                elif file_type == "video":
                    frameset = prepare_visual(Path(row["path"]), config)
                    if frameset is None:
                        errors += 1
                        continue
                    all_frames = frameset.frames + frameset.rejected
                    frame_metrics = [_analyze_frame(f.jpeg_bytes) for f in all_frames]
                    metrics = _aggregate_frame_metrics(frame_metrics)
                    frame_count = len(frame_metrics)
                else:
                    continue

                upsert_quality_score(
                    corpus_conn,
                    file_id,
                    metrics["sharpness"],
                    metrics["exposure"],
                    metrics["highlights"],
                    metrics["shadows"],
                    frame_count,
                    metrics.get("luminance_std_dev"),
                    metrics.get("saturation_mean"),
                    metrics.get("dominant_hue"),
                )
                scored += 1

            except Exception as exc:
                logger.warning("Quality: file_id=%d failed: %s", file_id, exc)
                errors += 1

            if scored % 50 == 0:
                corpus_conn.commit()

        corpus_conn.commit()

        if not cancel_event.is_set():
            compute_quality_rank_scores(corpus_conn)
            corpus_conn.commit()
            duration = time.monotonic() - start
            update_pipeline_checkpoint(corpus_conn, "quality", scored, 0, errors, duration)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()

    return {"scored": scored, "errors": errors}
