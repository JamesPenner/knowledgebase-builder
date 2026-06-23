"""Unit tests for transcribe stage helpers (no model, no DB)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.stages.transcribe import _AUDIO_EXTS, _VIDEO_EXTS, _transcribe_with_cli


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


# ---------------------------------------------------------------------------
# _transcribe_with_cli
# ---------------------------------------------------------------------------

def _make_cli_json(language: str, segments: list[dict]) -> dict:
    return {
        "result": {"language": language},
        "transcription": [
            {
                "offsets": {"from": s["start_ms"], "to": s["end_ms"]},
                "text": s["text"],
            }
            for s in segments
        ],
    }


def _run_cli_with_mock_output(tmp_path, json_payload: dict, returncode: int = 0):
    """Patch subprocess.run to write json_payload to the expected output file."""
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF")

    def fake_run(cmd, **kwargs):
        # Extract -of value and write the JSON there
        of_idx = cmd.index("-of")
        out_prefix = cmd[of_idx + 1]
        Path(out_prefix + ".json").write_text(json.dumps(json_payload), encoding="utf-8")
        result = MagicMock()
        result.returncode = returncode
        result.stderr = b""
        return result

    with patch("src.stages.transcribe.subprocess.run", side_effect=fake_run):
        return _transcribe_with_cli(wav, "whisper-cli.exe", "model.bin")


def test_transcribe_with_cli_returns_text_and_language(tmp_path):
    payload = _make_cli_json("en", [
        {"start_ms": 0, "end_ms": 1500, "text": " Hello world."},
        {"start_ms": 1500, "end_ms": 3000, "text": " How are you?"},
    ])
    text, lang, segments = _run_cli_with_mock_output(tmp_path, payload)
    assert lang == "en"
    assert "Hello world." in text
    assert "How are you?" in text


def test_transcribe_with_cli_returns_segments(tmp_path):
    payload = _make_cli_json("en", [
        {"start_ms": 0, "end_ms": 2000, "text": " First segment."},
    ])
    _, _, segments = _run_cli_with_mock_output(tmp_path, payload)
    assert len(segments) == 1
    assert segments[0]["start_ms"] == 0
    assert segments[0]["end_ms"] == 2000
    assert segments[0]["text"] == "First segment."
    assert segments[0]["avg_logprob"] is None


def test_transcribe_with_cli_skips_empty_segments(tmp_path):
    payload = _make_cli_json("en", [
        {"start_ms": 0, "end_ms": 500, "text": ""},
        {"start_ms": 500, "end_ms": 1500, "text": " Real text."},
    ])
    text, _, segments = _run_cli_with_mock_output(tmp_path, payload)
    assert len(segments) == 1
    assert "Real text." in text


def test_transcribe_with_cli_und_on_missing_language(tmp_path):
    payload = {"result": {}, "transcription": [{"offsets": {"from": 0, "to": 1000}, "text": " Hi"}]}
    _, lang, _ = _run_cli_with_mock_output(tmp_path, payload)
    assert lang == "und"


def test_transcribe_with_cli_raises_on_missing_json(tmp_path):
    import pytest
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF")

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = b"some error"

    with patch("src.stages.transcribe.subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="whisper-cli produced no JSON output"):
            _transcribe_with_cli(wav, "whisper-cli.exe", "model.bin")


def test_transcribe_with_cli_passes_no_gpu_flag(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        of_idx = cmd.index("-of")
        out_prefix = cmd[of_idx + 1]
        payload = _make_cli_json("en", [{"start_ms": 0, "end_ms": 1000, "text": " test"}])
        Path(out_prefix + ".json").write_text(json.dumps(payload), encoding="utf-8")
        result = MagicMock()
        result.returncode = 0
        result.stderr = b""
        return result

    with patch("src.stages.transcribe.subprocess.run", side_effect=fake_run):
        _transcribe_with_cli(wav, "whisper-cli.exe", "model.bin", no_gpu=True)

    assert "--no-gpu" in captured["cmd"]


# ---------------------------------------------------------------------------
# _check_whisper_cli health check
# ---------------------------------------------------------------------------

def test_check_whisper_cli_not_configured():
    from src.health import _check_whisper_cli
    from src.config import Config
    cfg = Config()
    result = _check_whisper_cli(cfg)
    assert result.ok is True
    assert "not configured" in result.detail


def test_check_whisper_cli_configured_and_found(tmp_path):
    from src.health import _check_whisper_cli
    from src.config import Config
    import dataclasses
    exe = tmp_path / "whisper-cli.exe"
    exe.write_bytes(b"fake")
    cfg = dataclasses.replace(Config(), whisper_cli=str(exe))
    result = _check_whisper_cli(cfg)
    assert result.ok is True


def test_check_whisper_cli_configured_and_missing(tmp_path):
    from src.health import _check_whisper_cli
    from src.config import Config
    import dataclasses
    cfg = dataclasses.replace(Config(), whisper_cli=str(tmp_path / "missing.exe"))
    result = _check_whisper_cli(cfg)
    assert result.ok is False
    assert "not found" in result.detail
