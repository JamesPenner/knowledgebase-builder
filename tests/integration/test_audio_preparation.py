"""Integration tests for AudioTrack / prepare_audio — requires ffmpeg."""
import shutil
import struct
import subprocess
import threading
import wave
from pathlib import Path

import pytest

from src.config import Config
from src.db.corpus import get_has_speech, open_corpus, set_has_speech
from src.media.audiotrack import prepare_audio


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
def audio_config(ffmpeg_bin, ffprobe_bin):
    return Config(ffmpeg=ffmpeg_bin, ffprobe=ffprobe_bin)


@pytest.fixture
def video_with_audio(tmp_path, ffmpeg_bin):
    """Short MP4 with a 440 Hz tone — has speech-level audio."""
    path = tmp_path / "audio_video.mp4"
    result = subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=64x64:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k",
            str(path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg failed to generate test video with audio")
    return path


@pytest.fixture
def video_no_audio(tmp_path, ffmpeg_bin):
    """Short MP4 with no audio stream."""
    path = tmp_path / "silent_video.mp4"
    result = subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-an",
            str(path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg failed to generate video without audio")
    return path


@pytest.fixture
def video_silent_audio(tmp_path, ffmpeg_bin):
    """Short MP4 with a completely silent audio track."""
    path = tmp_path / "video_silent_audio.mp4"
    result = subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=64x64:rate=10",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=16000",
            "-t", "3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg failed to generate silent-audio video")
    return path


@pytest.fixture
def sample_image(tmp_path):
    from PIL import Image
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (64, 64), color=(128, 64, 32)).save(path, "JPEG")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPrepareAudioBasic:
    def test_returns_track_for_video_with_audio(self, video_with_audio, audio_config):
        with prepare_audio(video_with_audio, audio_config) as track:
            assert track is not None
            assert track.wav_path.exists()
            assert track.duration_ms > 0
            assert track.sample_rate == 16000

    def test_returns_none_for_image(self, sample_image, audio_config):
        with prepare_audio(sample_image, audio_config) as track:
            assert track is None

    def test_returns_none_for_video_without_audio(self, video_no_audio, audio_config):
        with prepare_audio(video_no_audio, audio_config) as track:
            assert track is None

    def test_returns_none_for_nonexistent_file(self, tmp_path, audio_config):
        with prepare_audio(tmp_path / "missing.mp4", audio_config) as track:
            assert track is None

    def test_never_raises_on_corrupt_input(self, tmp_path, audio_config):
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00\xFF" * 100)
        # Must not raise
        with prepare_audio(corrupt, audio_config) as track:
            assert track is None


class TestVADIntegration:
    def test_silent_audio_has_speech_false(self, video_silent_audio, audio_config):
        with prepare_audio(video_silent_audio, audio_config) as track:
            assert track is not None
            assert track.has_speech is False

    def test_tone_audio_has_speech_true(self, video_with_audio, audio_config):
        with prepare_audio(video_with_audio, audio_config) as track:
            assert track is not None
            assert track.has_speech is True


class TestContextManagerCleanup:
    def test_wav_path_deleted_after_exit(self, video_with_audio, audio_config):
        with prepare_audio(video_with_audio, audio_config) as track:
            assert track is not None
            wav = track.wav_path
            assert wav.exists()
        assert not wav.exists()

    def test_cleans_up_even_when_caller_raises(self, video_with_audio, audio_config):
        wav_ref: list[Path] = []
        try:
            with prepare_audio(video_with_audio, audio_config) as track:
                assert track is not None
                wav_ref.append(track.wav_path)
                raise RuntimeError("deliberate")
        except RuntimeError:
            pass
        assert wav_ref and not wav_ref[0].exists()


class TestSegmentExtraction:
    def test_segment_duration_approx(self, video_with_audio, audio_config):
        with prepare_audio(video_with_audio, audio_config, segment_start_ms=500, segment_end_ms=2500) as track:
            assert track is not None
            # Allow ±300 ms tolerance for codec alignment
            assert abs(track.duration_ms - 2000) < 300


class TestHasSpeechPersistence:
    def test_has_speech_stored_in_db(self, tmp_path, video_silent_audio, audio_config):
        corpus_path = tmp_path / "corpus.db"
        conn = open_corpus(corpus_path)
        # Insert a minimal file row
        conn.execute(
            "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)",
            (str(tmp_path),),
        )
        conn.execute(
            """INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)
               VALUES (1, ?, 'v.mp4', '.mp4', 'video', 1000, 0.0)""",
            (str(video_silent_audio),),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM files WHERE path = ?", (str(video_silent_audio),)).fetchone()
        file_id = row["id"]

        with prepare_audio(video_silent_audio, audio_config) as track:
            assert track is not None
            if track.has_speech is not None:
                set_has_speech(conn, file_id, track.has_speech)
                conn.commit()

        assert get_has_speech(conn, file_id) is False
        conn.close()

    def test_has_speech_false_skips_in_transcribe(self, tmp_path, audio_config):
        """A file with has_speech=False in DB causes transcribe stage to record 'skipped'."""
        from src.db.corpus import add_source, upsert_file
        from src.pipeline.progress import NullProgressReporter
        from src.stages.transcribe import run_transcribe

        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"

        # Create a corpus with one silent audio file
        conn = open_corpus(corpus_path)
        source_id = add_source(conn, str(tmp_path / "media"))
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        fake_audio = media_dir / "silent.wav"
        # Write a minimal valid WAV (100 zero samples)
        with wave.open(str(fake_audio), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("<100h", *([0] * 100)))
        file_id = upsert_file(
            conn, source_id, str(fake_audio), "silent.wav", ".wav", "audio",
            fake_audio.stat().st_size, fake_audio.stat().st_mtime,
        )
        set_has_speech(conn, file_id, False)
        conn.commit()
        conn.close()

        config = Config(
            ffmpeg=audio_config.ffmpeg,
            ffprobe=audio_config.ffprobe,
            audio_model="dummy",
            whisper_cli="dummy_cli",
        )

        # run_transcribe should skip (not try to spawn whisper_cli)
        import unittest.mock as mock
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            run_transcribe(corpus_path, kb_path, config, NullProgressReporter(), threading.Event())
            # subprocess.run should NOT have been called for whisper transcription
            for call in mock_run.call_args_list:
                args = call[0][0] if call[0] else call[1].get("args", [])
                assert "dummy_cli" not in (args or []), (
                    "whisper_cli was invoked despite has_speech=False"
                )

        # Verify the file was recorded as skipped
        conn2 = open_corpus(corpus_path)
        row = conn2.execute(
            "SELECT transcribe_status FROM transcriptions WHERE file_id = ?", (file_id,)
        ).fetchone()
        assert row is not None
        assert row["transcribe_status"] == "skipped"
        conn2.close()
