"""Unit tests for the get_video_frames generic frame-extraction helper."""
from unittest.mock import MagicMock, patch


def _make_config(n_frames=5, phash_threshold=10, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    cfg = MagicMock()
    cfg.describe_frames = n_frames
    cfg.phash_threshold = phash_threshold
    cfg.ffmpeg = ffmpeg
    cfg.ffprobe = ffprobe
    return cfg


def test_uniform_mode_samples_evenly():
    """Uniform mode produces n_frames evenly-spaced timestamps."""
    from pathlib import Path
    from src.stages.video import get_video_frames

    duration_ms = 10_000  # 10 seconds
    n = 4
    config = _make_config(n_frames=n)

    captured_timestamps = []

    def fake_extract(file_path, ffmpeg, timestamps_ms):
        captured_timestamps.extend(timestamps_ms)
        return [b"frame"] * len(timestamps_ms)

    with (
        patch("src.stages.video._get_duration_ms", return_value=duration_ms),
        patch("src.stages.video._extract_frames", side_effect=fake_extract),
    ):
        frames = get_video_frames(Path("dummy.mp4"), config, mode="uniform", n_frames=n)

    assert len(frames) == n
    assert len(captured_timestamps) == n
    interval = duration_ms // (n + 1)
    expected = [interval * (i + 1) for i in range(n)]
    assert captured_timestamps == expected


def test_interval_mode_derives_count_from_duration():
    """Interval mode produces one frame every interval_seconds."""
    from pathlib import Path
    from src.stages.video import get_video_frames

    config = _make_config()

    captured_timestamps = []

    def fake_extract(file_path, ffmpeg, timestamps_ms):
        captured_timestamps.extend(timestamps_ms)
        return [b"frame"] * len(timestamps_ms)

    with (
        patch("src.stages.video._get_duration_ms", return_value=30_000),
        patch("src.stages.video._extract_frames", side_effect=fake_extract),
    ):
        frames = get_video_frames(Path("dummy.mp4"), config, mode="interval", interval_seconds=10.0)

    assert len(frames) == 3
    assert captured_timestamps == [10_000, 20_000, 30_000]


def test_scene_mode_calls_select_scene_frames():
    """Scene mode passes extracted frames through _select_scene_frames."""
    from pathlib import Path
    from src.stages.video import get_video_frames

    config = _make_config(phash_threshold=10)
    raw_frames = [b"frame_a", b"frame_b", b"frame_c"]

    with (
        patch("src.stages.video._get_duration_ms", return_value=10_000),
        patch("src.stages.video._extract_frames", return_value=raw_frames),
        patch("src.stages.video._select_scene_frames", return_value=[b"frame_a", b"frame_c"]) as mock_scene,
    ):
        frames = get_video_frames(Path("dummy.mp4"), config, mode="scene")

    mock_scene.assert_called_once_with(raw_frames, config.phash_threshold)
    assert frames == [b"frame_a", b"frame_c"]
