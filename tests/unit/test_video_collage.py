"""Unit tests for make_collage() and get_video_frames mode='collage'."""
import io
import math

from PIL import Image

from src.stages.video import make_collage


def _jpeg(color=(128, 64, 32), size=(320, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def test_make_collage_single_frame_returns_jpeg():
    frames = [_jpeg()]
    result = make_collage(frames, cols=1)
    assert result[:3] == b"\xff\xd8\xff"   # JPEG magic bytes
    img = Image.open(io.BytesIO(result))
    assert img.width == 320 and img.height == 180


def test_make_collage_nine_frames_3x3():
    frames = [_jpeg((i * 20, 0, 0)) for i in range(9)]
    result = make_collage(frames, cols=3)
    img = Image.open(io.BytesIO(result))
    assert img.width == 3 * 320
    assert img.height == 3 * 180


def test_make_collage_six_frames_produces_correct_dimensions():
    frames = [_jpeg() for _ in range(6)]
    cols = math.ceil(math.sqrt(6))   # ceil(2.449) = 3
    rows = math.ceil(6 / cols)       # ceil(2) = 2
    result = make_collage(frames, cols=cols)
    img = Image.open(io.BytesIO(result))
    assert img.width == cols * 320
    assert img.height == rows * 180


def test_make_collage_empty_returns_empty_bytes():
    result = make_collage([], cols=3)
    assert result == b""


def test_make_collage_cells_are_fixed_320x180():
    """Collage pHash must be resolution-independent — cells always 320×180."""
    large_frame_buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), color=(0, 128, 255)).save(large_frame_buf, "JPEG")
    large_frame = large_frame_buf.getvalue()

    result = make_collage([large_frame], cols=1)
    img = Image.open(io.BytesIO(result))
    assert img.width == 320
    assert img.height == 180


def test_make_collage_more_cols_than_frames_clips_to_frame_count():
    frames = [_jpeg() for _ in range(2)]
    result = make_collage(frames, cols=10)   # cols clamped to 2
    img = Image.open(io.BytesIO(result))
    assert img.width == 2 * 320
    assert img.height == 180
