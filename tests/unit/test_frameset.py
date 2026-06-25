"""Unit tests for src/media/frameset.py — no ffmpeg, no filesystem."""
import dataclasses
import io

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jpeg(brightness: int = 128, size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image
    arr = np.full((*size, 3), brightness, dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _make_frame(brightness: float = 128.0, passed: bool = True, phash: str | None = None):
    from src.media.frameset import Frame
    return Frame(
        jpeg_bytes=b"",
        width=64,
        height=64,
        timestamp_ms=None,
        scene_id=None,
        brightness=brightness,
        sharpness=100.0,
        phash=phash,
        passed_quality=passed,
        enhanced=False,
    )


# ---------------------------------------------------------------------------
# Dataclass structure
# ---------------------------------------------------------------------------

def test_frame_dataclass_fields():
    from src.media.frameset import Frame
    names = {f.name for f in dataclasses.fields(Frame)}
    expected = {
        "jpeg_bytes", "width", "height", "timestamp_ms", "scene_id",
        "brightness", "sharpness", "phash", "passed_quality", "enhanced",
    }
    assert names == expected


# ---------------------------------------------------------------------------
# FrameSet.best_frame
# ---------------------------------------------------------------------------

def test_frameset_best_frame_prefers_passed_quality():
    from src.media.frameset import FrameSet
    from pathlib import Path

    passed_frame = _make_frame(brightness=50.0, passed=True)
    failed_frame = _make_frame(brightness=200.0, passed=False)
    fs = FrameSet(
        file_path=Path("dummy.jpg"),
        file_type="image",
        frames=[passed_frame, failed_frame],
        rejected=[],
    )
    assert fs.best_frame is passed_frame


def test_frameset_best_frame_falls_back_to_brightness():
    from src.media.frameset import FrameSet
    from pathlib import Path

    dim_frame = _make_frame(brightness=50.0, passed=False)
    bright_frame = _make_frame(brightness=200.0, passed=False)
    fs = FrameSet(
        file_path=Path("dummy.jpg"),
        file_type="image",
        frames=[dim_frame, bright_frame],
        rejected=[],
    )
    assert fs.best_frame is bright_frame


# ---------------------------------------------------------------------------
# VisualProfile defaults
# ---------------------------------------------------------------------------

def test_visual_profile_defaults():
    from src.media.frameset import DEFAULT, ARCHIVAL, QUICK
    assert DEFAULT.max_px == 1024
    assert DEFAULT.enhance is False
    assert DEFAULT.scene_detection is False
    assert ARCHIVAL.enhance is True
    assert QUICK.max_px == 512
    assert QUICK.max_frames == 3


# ---------------------------------------------------------------------------
# Quality gate guarantee
# ---------------------------------------------------------------------------

def test_quality_gate_guarantee_nonempty_when_all_fail():
    from src.media.frameset import prepare_visual
    from src.config import Config
    import tempfile
    from pathlib import Path

    jpeg = _make_jpeg(brightness=0)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(jpeg)
        p = Path(f.name)

    try:
        cfg = Config(describe_min_frame_brightness=30.0, describe_min_frame_sharpness=0.0)
        result = prepare_visual(p, cfg)
        assert result is not None
        assert len(result.frames) >= 1
    finally:
        p.unlink(missing_ok=True)


def test_quality_gate_guarantee_promotes_best_rejected():
    from src.media.frameset import prepare_visual
    from src.config import Config
    import tempfile
    from pathlib import Path

    jpeg = _make_jpeg(brightness=0)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(jpeg)
        p = Path(f.name)

    try:
        cfg = Config(describe_min_frame_brightness=30.0, describe_min_frame_sharpness=0.0)
        result = prepare_visual(p, cfg)
        assert result is not None
        assert result.frames[0].passed_quality is False
    finally:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# pHash deduplication
# ---------------------------------------------------------------------------

def test_phash_dedup_removes_near_duplicate():
    from src.media.frameset import _dedup_phash

    shared_phash = "a" * 16
    f1 = _make_frame(phash=shared_phash)
    f2 = _make_frame(phash=shared_phash)
    result = _dedup_phash([f1, f2], threshold=10)
    assert len(result) == 1


def test_phash_dedup_keeps_diverse_frames():
    from src.media.frameset import _dedup_phash, _compute_phash
    from PIL import Image

    # Solid black vs. checkerboard: very different DCT coefficients → large pHash distance
    arr_solid = np.zeros((64, 64, 3), dtype=np.uint8)
    arr_check = np.zeros((64, 64, 3), dtype=np.uint8)
    for i in range(64):
        for j in range(64):
            if (i // 8 + j // 8) % 2 == 0:
                arr_check[i, j] = 255

    def _to_jpeg(arr):
        buf = io.BytesIO()
        Image.fromarray(arr, "RGB").save(buf, format="JPEG", quality=95)
        return buf.getvalue()

    ph_solid = _compute_phash(_to_jpeg(arr_solid))
    ph_check = _compute_phash(_to_jpeg(arr_check))

    f1 = _make_frame(phash=ph_solid)
    f2 = _make_frame(phash=ph_check)
    result = _dedup_phash([f1, f2], threshold=10)
    assert len(result) == 2
