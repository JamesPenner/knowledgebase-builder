import json
import logging
import threading
from pathlib import Path

from src.config import Config
from src.llm.session import ModelLoadError  # noqa: F401 — re-exported for callers
from src.pipeline.embeddings import cosine_similarity, update_centroid
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)


def detect_faces(img_bytes: bytes, detection_model_path: str) -> list[dict]:
    """Run SCRFD ONNX face detection on an image.

    Returns list of dicts with keys 'bbox' ([x1, y1, x2, y2]) and 'score'.
    """
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise ModelLoadError(f"onnxruntime not installed: {exc}") from exc

    model_file = Path(detection_model_path)
    if not model_file.exists():
        raise ModelLoadError(f"Face detection model not found: {detection_model_path}")

    try:
        import io as _io
        from PIL import Image
    except ImportError as exc:
        raise ModelLoadError(f"Pillow not installed: {exc}") from exc

    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        session = ort.InferenceSession(str(model_file), providers=providers)
    except Exception as exc:
        raise ModelLoadError(f"Could not load face detection model: {exc}") from exc

    img = Image.open(_io.BytesIO(img_bytes)).convert("RGB")
    orig_w, orig_h = img.size
    input_size = (640, 640)
    img_resized = img.resize(input_size, Image.BILINEAR)
    arr = np.array(img_resized, dtype=np.float32)
    # SCRFD normalization: subtract mean, divide std
    arr = (arr - np.array([127.5, 127.5, 127.5], dtype=np.float32)) / 128.0
    arr = arr.transpose(2, 0, 1)[np.newaxis]  # NCHW

    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: arr})

    # SCRFD outputs: scores [N,1], bboxes [N,4] in input_size space
    # Output layout varies by export; attempt standard 8/16/32 stride outputs
    faces = []
    score_threshold = 0.5

    # Flatten all stride outputs
    all_scores = []
    all_bboxes = []
    for out in outputs:
        if out.ndim == 2 and out.shape[1] == 1:
            all_scores.append(out[:, 0])
        elif out.ndim == 2 and out.shape[1] == 4:
            all_bboxes.append(out)

    if not all_scores or not all_bboxes:
        return faces

    scores = np.concatenate(all_scores)
    bboxes = np.concatenate(all_bboxes)

    scale_x = orig_w / input_size[0]
    scale_y = orig_h / input_size[1]

    # buffalo_l det_10g outputs normalized coords [0,1] relative to input_size.
    # Other SCRFD exports output absolute pixel coords in input_size space.
    # Detect by checking whether all bbox values are in the normalized range.
    coords_normalized = len(bboxes) > 0 and float(np.abs(bboxes).max()) < 2.0

    for score, bbox in zip(scores, bboxes):
        if float(score) < score_threshold:
            continue
        if coords_normalized:
            x1 = float(bbox[0]) * orig_w
            y1 = float(bbox[1]) * orig_h
            x2 = float(bbox[2]) * orig_w
            y2 = float(bbox[3]) * orig_h
        else:
            x1 = float(bbox[0]) * scale_x
            y1 = float(bbox[1]) * scale_y
            x2 = float(bbox[2]) * scale_x
            y2 = float(bbox[3]) * scale_y
        # Normalize coordinate order — model occasionally returns inverted coords
        # for faces near the image edge.
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue  # degenerate bbox — skip
        faces.append({"bbox": [x1, y1, x2, y2], "score": float(score)})

    return faces


def embed_face(img_bytes: bytes, bbox: list, embedding_model_path: str) -> bytes:
    """Crop a face region and run ArcFace ONNX embedding.

    Returns 512D float32 embedding as raw bytes.
    """
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise ModelLoadError(f"onnxruntime not installed: {exc}") from exc

    model_file = Path(embedding_model_path)
    if not model_file.exists():
        raise ModelLoadError(f"Face embedding model not found: {embedding_model_path}")

    try:
        import io as _io
        from PIL import Image
    except ImportError as exc:
        raise ModelLoadError(f"Pillow not installed: {exc}") from exc

    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        session = ort.InferenceSession(str(model_file), providers=providers)
    except Exception as exc:
        raise ModelLoadError(f"Could not load face embedding model: {exc}") from exc

    img = Image.open(_io.BytesIO(img_bytes)).convert("RGB")
    x1, y1, x2, y2 = (int(v) for v in bbox)
    # Clamp to image bounds
    w, h = img.size
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid face bbox after clamping: {bbox}")

    face_crop = img.crop((x1, y1, x2, y2)).resize((112, 112), Image.BILINEAR)
    arr = np.array(face_crop, dtype=np.float32)
    arr = (arr - 127.5) / 128.0
    arr = arr.transpose(2, 0, 1)[np.newaxis]  # NCHW [1, 3, 112, 112]

    input_name = session.get_inputs()[0].name
    embedding = session.run(None, {input_name: arr})[0][0]  # shape [512]

    # L2-normalise
    norm = float(np.linalg.norm(embedding))
    if norm > 0:
        embedding = embedding / norm

    return embedding.astype(np.float32).tobytes()


def compute_trimmed_centroid(
    embeddings: list[bytes],
    trim_fraction: float = 0.10,
) -> "tuple[bytes, int, float] | None":
    """Robust centroid via trimmed mean.

    Excludes the top `trim_fraction` of embeddings by distance from the initial mean,
    then recomputes. Returns (centroid_blob, retained_count, spread) or None if
    fewer than 2 embeddings remain after trimming.
    """
    import numpy as np
    if not embeddings:
        return None
    vecs = np.stack([np.frombuffer(e, dtype=np.float32) for e in embeddings])
    mean = vecs.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm > 0:
        mean = mean / norm
    dists = 1.0 - (vecs @ mean)  # cosine distance from initial mean
    n_exclude = max(0, int(len(embeddings) * trim_fraction))
    threshold = np.sort(dists)[-(n_exclude + 1)] if n_exclude < len(dists) else dists.max() + 1
    mask = dists <= threshold
    retained = vecs[mask]
    if len(retained) < 2:
        retained = vecs
    centroid = retained.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    # spread = mean cosine distance of retained embeddings from centroid
    spread = float((1.0 - (retained @ centroid)).mean())
    return centroid.astype(np.float32).tobytes(), len(retained), spread


def run_face(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> dict:
    """Detect faces in pending image files and match to known people."""
    import time as _time
    from src.db.corpus import (
        get_face_clusters,
        get_files_without_face_regions,
        insert_face_cluster_member,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_face_cluster,
        upsert_face_region,
    )
    from src.db.kb import (
        get_people_with_centroids,
        open_kb,
        update_person_centroid,
    )
    from src.pipeline.knowledge_gates import get_enabled_categories, report_stage_skipped, stage_is_enabled

    kb_conn = open_kb(kb_path)
    enabled_categories = get_enabled_categories(kb_conn)
    if not stage_is_enabled("face", enabled_categories):
        result = report_stage_skipped(progress, "face", enabled_categories)
        kb_conn.close()
        return result

    if not config.face_detection_model:
        raise ModelLoadError("face_detection_model is not configured")
    if not config.face_embedding_model:
        raise ModelLoadError("face_embedding_model is not configured")

    corpus_conn = open_corpus(corpus_path)

    files_processed = 0
    faces_detected = 0
    faces_matched = 0
    error_count = 0
    _start = _time.monotonic()

    try:
        # Pre-load people centroids into memory {person_id: {"blob": ..., "count": ..., "name": ...}}
        people_rows = get_people_with_centroids(kb_conn, "face")
        centroids: dict[int, dict] = {
            row["id"]: {
                "blob": bytes(row["face_centroid"]),
                "count": row["face_samples"],
            }
            for row in people_rows
        }

        # Pre-load unknown clusters if feature is enabled
        cluster_centroids: list[dict] = []
        if config.unknown_face_clusters:
            for row in get_face_clusters(corpus_conn):
                cluster_centroids.append({
                    "id": row["id"],
                    "blob": bytes(row["centroid"]),
                    "count": row["member_count"],
                })

        pending = get_files_without_face_regions(corpus_conn)
        total = len(pending)
        progress.update(0, total, "Detecting faces…")

        for i, row in enumerate(pending):
            if cancel_event.is_set():
                break

            img_path = Path(row["path"])
            file_id = row["id"]

            from src.media.frameset import prepare_visual
            frameset = prepare_visual(img_path, config)
            if frameset is None:
                logger.warning("Face: prepare_visual returned None for %s", img_path)
                error_count += 1
                progress.update(i + 1, total)
                continue

            region_index = 0
            file_detection_error = False
            for frame in frameset.frames:
                if not frame.passed_quality:
                    logger.debug("Face: skipping low-quality frame for %s", img_path)
                    continue

                try:
                    faces = detect_faces(frame.jpeg_bytes, config.face_detection_model)
                except ModelLoadError:
                    raise
                except Exception as exc:
                    logger.warning("Face detection failed for %s: %s", img_path, exc)
                    error_count += 1
                    file_detection_error = True
                    continue

                for face in faces:
                    faces_detected += 1
                    bbox = face["bbox"]
                    try:
                        embedding = embed_face(frame.jpeg_bytes, bbox, config.face_embedding_model)
                    except ModelLoadError:
                        raise
                    except Exception as exc:
                        logger.warning("Face embedding failed for %s face %d: %s", img_path, region_index, exc)
                        error_count += 1
                        region_index += 1
                        continue

                    # Match against known people centroids
                    best_person_id = None
                    best_similarity = 0.0
                    for person_id, cent in centroids.items():
                        sim = cosine_similarity(embedding, cent["blob"])
                        if sim > best_similarity:
                            best_similarity = sim
                            best_person_id = person_id

                    matched_person_id = None
                    matched_similarity = None
                    if best_person_id is not None and best_similarity >= config.face_similarity_threshold:
                        matched_person_id = best_person_id
                        matched_similarity = best_similarity
                        faces_matched += 1
                        old = centroids[best_person_id]
                        new_blob, new_count = update_centroid(old["blob"], old["count"], embedding)
                        centroids[best_person_id] = {"blob": new_blob, "count": new_count}
                        update_person_centroid(kb_conn, best_person_id, new_blob, new_count, kind="face")
                    elif config.unknown_face_clusters:
                        best_cluster_idx = None
                        best_cluster_sim = 0.0
                        for ci, cl in enumerate(cluster_centroids):
                            sim = cosine_similarity(embedding, cl["blob"])
                            if sim > best_cluster_sim:
                                best_cluster_sim = sim
                                best_cluster_idx = ci

                        if best_cluster_idx is None or best_cluster_sim < config.face_similarity_threshold:
                            cid = upsert_face_cluster(corpus_conn, None, embedding, 1, 0.0)
                            cluster_centroids.append({"id": cid, "blob": embedding, "count": 1})
                            insert_face_cluster_member(corpus_conn, cid, file_id, region_index, None)
                        else:
                            cl = cluster_centroids[best_cluster_idx]
                            new_blob, new_count = update_centroid(cl["blob"], cl["count"], embedding)
                            spread = 1.0 - best_cluster_sim
                            cid = upsert_face_cluster(corpus_conn, cl["id"], new_blob, new_count, spread)
                            cluster_centroids[best_cluster_idx] = {"id": cid, "blob": new_blob, "count": new_count}
                            insert_face_cluster_member(corpus_conn, cid, file_id, region_index, best_cluster_sim)

                    upsert_face_region(
                        corpus_conn,
                        file_id,
                        region_index,
                        json.dumps(bbox),
                        embedding,
                        matched_person_id,
                        matched_similarity,
                    )
                    region_index += 1

            corpus_conn.commit()
            kb_conn.commit()
            if not file_detection_error:
                files_processed += 1
            progress.update(i + 1, total)

        update_pipeline_checkpoint(
            corpus_conn, "face", files_processed, 0, error_count,
            _time.monotonic() - _start,
        )
        corpus_conn.commit()
        progress.done()
    finally:
        corpus_conn.close()
        kb_conn.close()

    return {
        "files_processed": files_processed,
        "faces_detected": faces_detected,
        "faces_matched": faces_matched,
        "errors": error_count,
    }
