import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Band thresholds — upper bound is exclusive except the final band
_NIMA_BANDS = [
    (0.75, "excellent"),
    (0.55, "good"),
    (0.30, "average"),
    (0.0,  "poor"),
]
_CLIP_BANDS = [
    (0.80, "excellent"),
    (0.62, "good"),
    (0.30, "average"),
    (0.0,  "poor"),
]
_COMBINED_BANDS = [
    (0.75, "excellent"),
    (0.50, "good"),
    (0.25, "average"),
    (0.0,  "poor"),
]
_BANDS = {
    "nima_mobilenet": _NIMA_BANDS,
    "clip_vit_b32":   _CLIP_BANDS,
    "combined_rank":  _COMBINED_BANDS,
}


class ModelLoadError(Exception):
    pass


def assign_band(score: float, model_name: str) -> str:
    """Map a score in [0, 1] to a band label for the given model."""
    thresholds = _BANDS.get(model_name, _COMBINED_BANDS)
    for min_score, label in thresholds:
        if score >= min_score:
            return label
    return "poor"


def score_nima(img_path: Path, model_path: str) -> float:
    """Run NIMA MobileNet ONNX inference on an image. Returns score in [0, 1]."""
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise ModelLoadError(f"onnxruntime not installed: {exc}") from exc

    model_file = Path(model_path)
    if not model_file.exists():
        raise ModelLoadError(f"NIMA model not found: {model_path}")

    try:
        from PIL import Image
    except ImportError as exc:
        raise ModelLoadError(f"Pillow not installed: {exc}") from exc

    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        session = ort.InferenceSession(str(model_file), providers=providers)
    except Exception as exc:
        raise ModelLoadError(f"Could not load NIMA model: {exc}") from exc

    img = Image.open(img_path).convert("RGB").resize((224, 224))
    arr = np.array(img, dtype=np.float32)
    arr = arr / 127.5 - 1.0  # Keras MobileNet normalization
    arr = arr[np.newaxis]     # NHWC [1, 224, 224, 3]

    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: arr})
    probs = outputs[0][0]

    if len(probs) == 10:
        # NIMA 10-class weighted mean → normalise to [0, 1]
        mean = sum((i + 1) * p for i, p in enumerate(probs))
        return float(max(0.0, min(1.0, (mean - 1) / 9)))
    return float(max(0.0, min(1.0, float(probs[0]))))


def score_clip(img_path: Path, model_dir: str) -> float:
    """Run CLIP ViT-B/32 + LAION linear head inference. Returns score in [0, 1]."""
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise ModelLoadError(f"onnxruntime not installed: {exc}") from exc

    dir_path = Path(model_dir)
    visual_path = dir_path / "visual.onnx"
    linear_path = dir_path / "linear.npz"
    if not visual_path.exists():
        raise ModelLoadError(f"CLIP visual model not found: {visual_path}")
    if not linear_path.exists():
        raise ModelLoadError(f"CLIP linear weights not found: {linear_path}")

    try:
        from PIL import Image
    except ImportError as exc:
        raise ModelLoadError(f"Pillow not installed: {exc}") from exc

    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        session = ort.InferenceSession(str(visual_path), providers=providers)
    except Exception as exc:
        raise ModelLoadError(f"Could not load CLIP model: {exc}") from exc

    weights = np.load(str(linear_path))
    w = weights["weight"]  # shape [512]
    b = weights["bias"]    # shape [1]

    # CLIP normalization constants (ImageNet)
    mean = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    std  = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

    img = Image.open(img_path).convert("RGB").resize((224, 224), Image.BICUBIC)
    arr = np.array(img, dtype=np.float32) / 255.0
    arr = (arr - mean) / std
    arr = arr.transpose(2, 0, 1)[np.newaxis]  # NCHW [1, 3, 224, 224]

    input_name = session.get_inputs()[0].name
    embedding = session.run(None, {input_name: arr})[0][0]

    # L2-normalise then apply linear head
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm
    raw_score = float(np.dot(embedding, w) + b[0])

    # LAION scores range [1, 9] → normalise to [0, 1]
    return float(max(0.0, min(1.0, (raw_score - 1) / 8)))


def run_aesthetic(corpus_path, kb_path, config, progress, cancel) -> dict:
    """Score all pending image files with configured NIMA and/or CLIP models."""
    from src.db.corpus import (
        compute_combined_rank_scores,
        get_pending_aesthetic_files,
        open_corpus,
        upsert_aesthetic_score,
    )

    conn = open_corpus(corpus_path)
    nima_count = 0
    clip_count = 0
    error_count = 0

    if config.aesthetic_nima:
        pending = get_pending_aesthetic_files(conn, "nima_mobilenet")
        total = len(pending)
        progress.update(0, total, "Scoring NIMA…")
        for i, row in enumerate(pending):
            if cancel.is_set():
                break
            try:
                score = score_nima(Path(row["path"]), config.aesthetic_nima)
                band = assign_band(score, "nima_mobilenet")
                upsert_aesthetic_score(conn, row["id"], "nima_mobilenet", score, band)
                conn.commit()
                nima_count += 1
            except ModelLoadError:
                raise
            except Exception as exc:
                logger.warning("NIMA failed for %s: %s", row["path"], exc)
                error_count += 1
            progress.update(i + 1, total)

    if config.aesthetic_clip:
        pending = get_pending_aesthetic_files(conn, "clip_vit_b32")
        total = len(pending)
        progress.update(0, total, "Scoring CLIP…")
        for i, row in enumerate(pending):
            if cancel.is_set():
                break
            try:
                score = score_clip(Path(row["path"]), config.aesthetic_clip)
                band = assign_band(score, "clip_vit_b32")
                upsert_aesthetic_score(conn, row["id"], "clip_vit_b32", score, band)
                conn.commit()
                clip_count += 1
            except ModelLoadError:
                raise
            except Exception as exc:
                logger.warning("CLIP failed for %s: %s", row["path"], exc)
                error_count += 1
            progress.update(i + 1, total)

    combined_count = 0
    if nima_count > 0 or clip_count > 0:
        combined_count = compute_combined_rank_scores(conn)

    progress.done()
    conn.close()
    return {
        "nima_scored": nima_count,
        "clip_scored": clip_count,
        "combined_computed": combined_count,
        "errors": error_count,
    }
