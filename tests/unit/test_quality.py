"""Unit tests for quality metric computation and DB helpers."""
import numpy as np
import pytest


# ---------------------------------------------------------------------------
# _analyze_frame computation
# ---------------------------------------------------------------------------

def _flat_jpeg(value: int = 128) -> bytes:
    from PIL import Image
    import io
    img = Image.fromarray(np.full((64, 64), value, dtype=np.uint8), mode="L")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _noise_jpeg() -> bytes:
    from PIL import Image
    import io
    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, (64, 64), dtype=np.uint8)
    img = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _color_jpeg(r: int, g: int, b: int) -> bytes:
    from PIL import Image
    import io
    arr = np.full((64, 64, 3), [r, g, b], dtype=np.uint8)
    img = Image.fromarray(arr, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_sharpness_blurry_image_scores_low():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(128))
    assert result["sharpness"] < 10.0


def test_sharpness_noisy_image_scores_high():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_noise_jpeg())
    assert result["sharpness"] > 100.0


def test_exposure_midtone():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(128))
    assert abs(result["exposure"] - 0.5) < 0.05


def test_highlights_clipping():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(255))
    assert result["highlights"] > 0.9


def test_shadows_clipping():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(0))
    assert result["shadows"] > 0.9


def test_luminance_std_dev_flat_image_near_zero():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(128))
    assert result["luminance_std_dev"] < 0.05


def test_luminance_std_dev_noisy_image_high():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_noise_jpeg())
    assert result["luminance_std_dev"] > 0.1


def test_saturation_mean_grayscale_near_zero():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(128))
    assert result["saturation_mean"] < 0.1


def test_saturation_mean_colorful_image():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_color_jpeg(255, 0, 0))  # vivid red
    assert result["saturation_mean"] > 0.5


def test_dominant_hue_grayscale_returns_zero():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(128))
    assert result["dominant_hue"] == pytest.approx(0.0)


def test_dominant_hue_blue_image_near_240():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_color_jpeg(0, 0, 255))  # vivid blue → hue ~240
    assert 200.0 <= result["dominant_hue"] <= 280.0


def test_analyze_frame_returns_all_seven_keys():
    from src.stages.quality import _analyze_frame
    result = _analyze_frame(_flat_jpeg(100))
    expected_keys = {"sharpness", "exposure", "highlights", "shadows",
                     "luminance_std_dev", "saturation_mean", "dominant_hue"}
    assert expected_keys == set(result.keys())


def test_aggregate_frame_metrics_averages():
    from src.stages.quality import _aggregate_frame_metrics
    frames = [
        {"sharpness": 100.0, "exposure": 0.4, "highlights": 0.1, "shadows": 0.05},
        {"sharpness": 200.0, "exposure": 0.6, "highlights": 0.3, "shadows": 0.15},
    ]
    result = _aggregate_frame_metrics(frames)
    assert result["sharpness"] == pytest.approx(150.0)
    assert result["exposure"] == pytest.approx(0.5)
    assert result["highlights"] == pytest.approx(0.2)
    assert result["shadows"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _seed_file(conn, file_type="images"):
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/a.jpg', 'a.jpg', '.jpg', ?, 1, 0.0)",
        (file_type,),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_upsert_quality_score_and_retrieve(tmp_path):
    from src.db.corpus import open_corpus, upsert_quality_score

    conn = open_corpus(tmp_path / "corpus.db")
    file_id = _seed_file(conn)

    upsert_quality_score(conn, file_id, 500.0, 0.45, 0.02, 0.01, 1,
                         luminance_std_dev=0.12, saturation_mean=0.35, dominant_hue=210.0)
    conn.commit()

    row = conn.execute("SELECT * FROM file_quality WHERE file_id = ?", (file_id,)).fetchone()
    assert row is not None
    assert row["sharpness"] == pytest.approx(500.0)
    assert row["exposure"] == pytest.approx(0.45)
    assert row["frame_count"] == 1
    assert row["luminance_std_dev"] == pytest.approx(0.12)
    assert row["saturation_mean"] == pytest.approx(0.35)
    assert row["dominant_hue"] == pytest.approx(210.0)

    # ON CONFLICT update
    upsert_quality_score(conn, file_id, 999.0, 0.5, 0.0, 0.0, 3)
    conn.commit()
    updated = conn.execute("SELECT * FROM file_quality WHERE file_id = ?", (file_id,)).fetchone()
    assert updated["sharpness"] == pytest.approx(999.0)
    assert updated["frame_count"] == 3
    # NULL when not provided on update
    assert updated["luminance_std_dev"] is None
    conn.close()


def test_compute_quality_rank_scores_orders_correctly(tmp_path):
    from src.db.corpus import compute_quality_rank_scores, open_corpus, upsert_quality_score

    conn = open_corpus(tmp_path / "corpus.db")

    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    for i, fname in enumerate(["a.jpg", "b.jpg"]):
        conn.execute(
            "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
            f" VALUES (1, '/{fname}', '{fname}', '.jpg', 'images', 1, 0.0)"
        )
    conn.commit()

    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY id").fetchall()]
    # File 0: blurry (low sharpness, ideal exposure, no clipping)
    upsert_quality_score(conn, ids[0], sharpness=10.0,   exposure=0.5, highlights=0.0, shadows=0.0, frame_count=1)
    # File 1: sharp (high sharpness, ideal exposure, no clipping)
    upsert_quality_score(conn, ids[1], sharpness=1000.0, exposure=0.5, highlights=0.0, shadows=0.0, frame_count=1)
    conn.commit()

    compute_quality_rank_scores(conn)

    rows = {r["file_id"]: r["quality_rank"]
            for r in conn.execute("SELECT file_id, quality_rank FROM file_quality").fetchall()}
    assert rows[ids[1]] > rows[ids[0]], "Sharper file should have higher quality_rank"
    conn.close()
