"""Integration tests for FrameSet / prepare_visual — requires PIL; video tests require ffmpeg."""
import io
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.config import Config
from src.media.frameset import FrameSet, prepare_visual


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ffmpeg_bin():
    ffmpeg = shutil.which("ffmpeg") or (
        "tools/ffmpeg.exe" if Path("tools/ffmpeg.exe").exists() else None
    )
    if ffmpeg is None:
        pytest.skip("ffmpeg not found")
    return ffmpeg


@pytest.fixture
def ffprobe_bin():
    ffprobe = shutil.which("ffprobe") or (
        "tools/ffprobe.exe" if Path("tools/ffprobe.exe").exists() else None
    )
    if ffprobe is None:
        pytest.skip("ffprobe not found")
    return ffprobe


@pytest.fixture
def visual_config(ffmpeg_bin, ffprobe_bin):
    return Config(ffmpeg=ffmpeg_bin, ffprobe=ffprobe_bin)


@pytest.fixture
def short_video(tmp_path, ffmpeg_bin):
    """Synthetic 3-second MP4 using testsrc."""
    path = tmp_path / "test.mp4"
    result = subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=64x64:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg failed to generate test video")
    return path


def _make_jpeg(brightness: int = 128, size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image
    arr = np.full((*size, 3), brightness, dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Image tests
# ---------------------------------------------------------------------------

def test_prepare_visual_image_returns_single_frame(tmp_path):
    p = tmp_path / "test.jpg"
    p.write_bytes(_make_jpeg(128))
    cfg = Config()
    result = prepare_visual(p, cfg)
    assert result is not None
    assert isinstance(result, FrameSet)
    assert result.file_type == "image"
    assert len(result.frames) == 1


def test_prepare_visual_image_exif_transpose(tmp_path):
    from PIL import Image
    # Create a 100×200 image (portrait), attach EXIF orientation 6 (90° CW rotation)
    # After exif_transpose, the resulting frame should be 200×100 (landscape)
    arr = np.zeros((200, 100, 3), dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    exif = img.getexif()
    exif[274] = 6  # Orientation: 90° CW
    p = tmp_path / "rotated.jpg"
    img.save(str(p), exif=exif.tobytes())

    cfg = Config()
    result = prepare_visual(p, cfg)
    assert result is not None
    frame = result.frames[0]
    # Post-transpose: width > height (landscape)
    assert frame.width > frame.height


def test_prepare_visual_image_resize(tmp_path):
    from PIL import Image
    # 2000×2000 image; with max_px=512 the longer dimension should be ≤512
    arr = np.full((2000, 2000, 3), 128, dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    p = tmp_path / "large.jpg"
    img.save(str(p))

    cfg = Config()
    result = prepare_visual(p, cfg, max_px=512)
    assert result is not None
    frame = result.frames[0]
    assert frame.width <= 512
    assert frame.height <= 512


def test_prepare_visual_returns_none_for_audio(tmp_path):
    p = tmp_path / "audio.mp3"
    p.write_bytes(b"\x00" * 64)
    cfg = Config()
    result = prepare_visual(p, cfg)
    assert result is None


def test_prepare_visual_never_raises_on_corrupt_file(tmp_path):
    p = tmp_path / "corrupt.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)  # truncated JPEG
    cfg = Config()
    result = prepare_visual(p, cfg)
    assert result is None


def test_prepare_visual_dark_image_passed_quality_false(tmp_path):
    p = tmp_path / "black.jpg"
    p.write_bytes(_make_jpeg(brightness=0))
    cfg = Config(describe_min_frame_brightness=30.0, describe_min_frame_sharpness=0.0)
    result = prepare_visual(p, cfg)
    assert result is not None
    assert len(result.frames) == 1
    assert result.frames[0].passed_quality is False


# ---------------------------------------------------------------------------
# Video tests (require ffmpeg)
# ---------------------------------------------------------------------------

def test_prepare_visual_video_returns_multiple_frames(short_video, visual_config):
    result = prepare_visual(short_video, visual_config, max_frames=3)
    assert result is not None
    assert isinstance(result, FrameSet)
    assert result.file_type == "video"
    assert len(result.frames) + len(result.rejected) > 0
