"""Unit tests for transcribe stage helpers (no model, no DB)."""
from src.stages.transcribe import _AUDIO_EXTS, _VIDEO_EXTS


def test_audio_ext_set_contains_common_types():
    assert ".mp3" in _AUDIO_EXTS
    assert ".wav" in _AUDIO_EXTS
    assert ".m4a" in _AUDIO_EXTS
    assert ".mp4" not in _AUDIO_EXTS
    assert ".jpg" not in _AUDIO_EXTS


def test_video_ext_set_contains_common_types():
    assert ".mp4" in _VIDEO_EXTS
    assert ".mov" in _VIDEO_EXTS
    assert ".mp3" not in _VIDEO_EXTS


def test_audio_and_video_exts_are_disjoint():
    assert _AUDIO_EXTS.isdisjoint(_VIDEO_EXTS)


def test_route_logic_image_excluded():
    """Images should not appear in pending transcribe query — verify via ext sets."""
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
    for ext in image_exts:
        assert ext not in _AUDIO_EXTS
        assert ext not in _VIDEO_EXTS


def test_route_logic_audio_included():
    for ext in [".mp3", ".wav", ".flac"]:
        assert ext in _AUDIO_EXTS


def test_route_logic_video_included():
    for ext in [".mp4", ".mkv", ".avi"]:
        assert ext in _VIDEO_EXTS
