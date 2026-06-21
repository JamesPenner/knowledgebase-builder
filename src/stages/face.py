import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ModelLoadError(Exception):
    pass


def detect_faces(img_path: Path, detection_model_path: str) -> list[dict]:
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
        from PIL import Image
    except ImportError as exc:
        raise ModelLoadError(f"Pillow not installed: {exc}") from exc

    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        session = ort.InferenceSession(str(model_file), providers=providers)
    except Exception as exc:
        raise ModelLoadError(f"Could not load face detection model: {exc}") from exc

    img = Image.open(img_path).convert("RGB")
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

    for score, bbox in zip(scores, bboxes):
        if float(score) < score_threshold:
            continue
        x1 = float(bbox[0]) * scale_x
        y1 = float(bbox[1]) * scale_y
        x2 = float(bbox[2]) * scale_x
        y2 = float(bbox[3]) * scale_y
        faces.append({"bbox": [x1, y1, x2, y2], "score": float(score)})

    return faces


def embed_face(img_path: Path, bbox: list, embedding_model_path: str) -> bytes:
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
        from PIL import Image
    except ImportError as exc:
        raise ModelLoadError(f"Pillow not installed: {exc}") from exc

    providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        session = ort.InferenceSession(str(model_file), providers=providers)
    except Exception as exc:
        raise ModelLoadError(f"Could not load face embedding model: {exc}") from exc

    img = Image.open(img_path).convert("RGB")
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


def cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine similarity between two 512D float32 embeddings stored as bytes."""
    import numpy as np
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def update_centroid(
    old_blob: bytes | None,
    old_count: int,
    new_embedding: bytes,
) -> tuple[bytes, int]:
    """Incremental running mean centroid update.

    Returns (new_centroid_blob, new_count).
    """
    import numpy as np
    new_vec = np.frombuffer(new_embedding, dtype=np.float32).copy()
    if old_blob is None or old_count == 0:
        new_count = 1
        centroid = new_vec
    else:
        old_vec = np.frombuffer(old_blob, dtype=np.float32).copy()
        new_count = old_count + 1
        centroid = (old_vec * old_count + new_vec) / new_count
    # Re-normalise
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    return centroid.astype(np.float32).tobytes(), new_count


def run_face(corpus_path, kb_path, config, progress, cancel) -> dict:
    """Detect faces in pending image files and match to known people."""
    from src.db.corpus import (
        get_face_clusters,
        get_files_without_face_regions,
        insert_face_cluster_member,
        open_corpus,
        upsert_face_cluster,
        upsert_face_region,
    )
    from src.db.kb import (
        get_people_with_centroids,
        open_kb,
        update_face_centroid,
    )

    if not config.face_detection_model:
        raise ModelLoadError("face_detection_model is not configured")
    if not config.face_embedding_model:
        raise ModelLoadError("face_embedding_model is not configured")

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    files_processed = 0
    faces_detected = 0
    faces_matched = 0
    error_count = 0

    try:
        # Pre-load people centroids into memory {person_id: {"blob": ..., "count": ..., "name": ...}}
        people_rows = get_people_with_centroids(kb_conn)
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
            if cancel.is_set():
                break

            img_path = Path(row["path"])
            file_id = row["id"]

            try:
                faces = detect_faces(img_path, config.face_detection_model)
            except ModelLoadError:
                raise
            except Exception as exc:
                logger.warning("Face detection failed for %s: %s", img_path, exc)
                error_count += 1
                progress.update(i + 1, total)
                continue

            for region_index, face in enumerate(faces):
                faces_detected += 1
                bbox = face["bbox"]
                try:
                    embedding = embed_face(img_path, bbox, config.face_embedding_model)
                except ModelLoadError:
                    raise
                except Exception as exc:
                    logger.warning("Face embedding failed for %s face %d: %s", img_path, region_index, exc)
                    error_count += 1
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
                    # Update centroid in memory
                    old = centroids[best_person_id]
                    new_blob, new_count = update_centroid(old["blob"], old["count"], embedding)
                    centroids[best_person_id] = {"blob": new_blob, "count": new_count}
                    update_face_centroid(kb_conn, best_person_id, new_blob, new_count)
                elif config.unknown_face_clusters:
                    # Assign to nearest cluster or create new one
                    best_cluster_idx = None
                    best_cluster_sim = 0.0
                    for ci, cl in enumerate(cluster_centroids):
                        sim = cosine_similarity(embedding, cl["blob"])
                        if sim > best_cluster_sim:
                            best_cluster_sim = sim
                            best_cluster_idx = ci

                    if best_cluster_idx is None or best_cluster_sim < config.face_similarity_threshold:
                        # New cluster
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

            corpus_conn.commit()
            kb_conn.commit()
            files_processed += 1
            progress.update(i + 1, total)

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
