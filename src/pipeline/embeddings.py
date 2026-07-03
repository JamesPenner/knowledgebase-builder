"""Shared embedding math — cosine similarity and running-mean centroid update."""


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
