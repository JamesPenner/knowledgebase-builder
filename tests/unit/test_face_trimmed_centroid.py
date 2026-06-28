"""Unit tests for KB.X1 compute_trimmed_centroid."""
import numpy as np

from src.stages.face import compute_trimmed_centroid


def _make_embedding(vec) -> bytes:
    v = np.array(vec, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if norm > 0:
        v = v / norm
    return v.tobytes()


def _rand_embedding(seed: int = 0, dim: int = 512) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def test_compute_trimmed_centroid_returns_none_for_empty():
    assert compute_trimmed_centroid([]) is None


def test_compute_trimmed_centroid_single_embedding():
    emb = _rand_embedding(0)
    result = compute_trimmed_centroid([emb])
    assert result is not None
    centroid_blob, retained, spread = result
    assert retained >= 1
    assert spread >= 0.0
    # centroid should be L2-normalised
    c = np.frombuffer(centroid_blob, dtype=np.float32)
    assert abs(float(np.linalg.norm(c)) - 1.0) < 1e-5


def test_compute_trimmed_centroid_excludes_outlier():
    # 9 similar embeddings all pointing in the same direction
    base = np.zeros(512, dtype=np.float32)
    base[0] = 1.0
    tight = [_make_embedding(base + np.random.default_rng(i).standard_normal(512) * 0.05)
             for i in range(9)]
    # 1 outlier pointing in the opposite direction
    outlier_vec = np.zeros(512, dtype=np.float32)
    outlier_vec[0] = -1.0
    outlier = [_make_embedding(outlier_vec)]

    result = compute_trimmed_centroid(tight + outlier, trim_fraction=0.10)
    assert result is not None
    centroid_blob, retained, spread = result
    # Outlier should be excluded; centroid should still point towards base direction
    c = np.frombuffer(centroid_blob, dtype=np.float32)
    assert c[0] > 0.5  # centroid still aligned with the cluster


def test_compute_trimmed_centroid_spread_low_for_tight_cluster():
    base = np.zeros(512, dtype=np.float32)
    base[0] = 1.0
    embeddings = [_make_embedding(base + np.random.default_rng(i).standard_normal(512) * 0.02)
                  for i in range(10)]
    result = compute_trimmed_centroid(embeddings)
    assert result is not None
    _, _, spread = result
    assert spread < 0.15


def test_compute_trimmed_centroid_spread_high_for_diverse_set():
    # Nearly orthogonal embeddings (one per axis)
    embeddings = []
    for i in range(10):
        v = np.zeros(512, dtype=np.float32)
        v[i] = 1.0
        embeddings.append(v.tobytes())
    result = compute_trimmed_centroid(embeddings)
    assert result is not None
    _, _, spread = result
    assert spread > 0.5
