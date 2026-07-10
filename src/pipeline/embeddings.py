"""Shared embedding math — cosine similarity and running-mean centroid update."""
from collections.abc import Mapping, Sequence


def cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine similarity between two float32 embeddings stored as raw bytes."""
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
    """Incremental running-mean centroid update. Returns (new_centroid_blob, new_count)."""
    import numpy as np
    new_vec = np.frombuffer(new_embedding, dtype=np.float32).copy()
    if old_blob is None or old_count == 0:
        new_count = 1
        centroid = new_vec
    else:
        old_vec = np.frombuffer(old_blob, dtype=np.float32).copy()
        new_count = old_count + 1
        centroid = (old_vec * old_count + new_vec) / new_count
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    return centroid.astype(np.float32).tobytes(), new_count


def mean_similarity_to_centroid(
    centroid: bytes | None,
    embeddings: Sequence[bytes],
) -> float | None:
    """Mean cosine similarity of each embedding to the centroid. None if no centroid or no embeddings."""
    if centroid is None or not embeddings:
        return None
    return sum(cosine_similarity(centroid, e) for e in embeddings) / len(embeddings)


def classify_centroid_status(
    cluster_count: int,
    mean_similarity: float | None,
    *,
    min_clusters: int,
    min_similarity: float,
) -> str:
    """Classify a person's centroid reliability as "reliable" / "needs_more_samples" / "too_few_samples"."""
    if cluster_count <= 0:
        return "too_few_samples"
    if cluster_count < min_clusters or mean_similarity is None or mean_similarity < min_similarity:
        return "needs_more_samples"
    return "reliable"


def rank_clusters_by_similarity(
    clusters: Sequence[Mapping],
    people: Sequence[Mapping],
    centroid_col: str,
) -> list[dict]:
    """Annotate each cluster with the best-matching person (by cosine similarity), sorted best-first.

    `clusters` rows must have a "centroid" key; `people` rows must have "id", "preferred_name",
    and `centroid_col`. Clusters with no centroid, or with no people to compare against, sort last
    (stable, preserving their relative input order).
    """
    annotated = []
    for cluster in clusters:
        row = dict(cluster)
        best_person_id = None
        best_person_name = None
        best_similarity = None
        cluster_centroid = row.get("centroid")
        if cluster_centroid is not None:
            for person in people:
                person_centroid = person[centroid_col]
                if person_centroid is None:
                    continue
                sim = cosine_similarity(bytes(cluster_centroid), bytes(person_centroid))
                if best_similarity is None or sim > best_similarity:
                    best_similarity = sim
                    best_person_id = person["id"]
                    best_person_name = person["preferred_name"]
        row["best_person_id"] = best_person_id
        row["best_person_name"] = best_person_name
        row["best_similarity"] = best_similarity
        annotated.append(row)
    annotated.sort(key=lambda r: r["best_similarity"] if r["best_similarity"] is not None else -1.0, reverse=True)
    return annotated
