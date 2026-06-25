"""Unit tests for src/media/audiotrack.py — no ffmpeg required."""
import numpy as np

from src.media.audiotrack import (
    ARCHIVAL,
    DEFAULT,
    AudioProfile,
    AudioTrack,
    _compute_has_speech,
    _compute_peak_and_clipping,
    _normalise_samples,
)


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------

class TestAudioTrackDataclass:
    def test_fields_accessible(self, tmp_path):
        wav = tmp_path / "a.wav"
        track = AudioTrack(
            file_path=tmp_path / "src.mp4",
            wav_path=wav,
            sample_rate=16000,
            duration_ms=3000,
            has_speech=True,
            peak_db=-12.0,
            has_clipping=False,
            normalised=False,
            segment_start_ms=None,
            segment_end_ms=None,
            owned=True,
        )
        assert track.sample_rate == 16000
        assert track.duration_ms == 3000
        assert track.has_speech is True
        assert track.owned is True


class TestAudioProfile:
    def test_default_profile_values(self):
        assert DEFAULT.name == "default"
        assert DEFAULT.normalise is False
        assert DEFAULT.vad is True

    def test_archival_profile_values(self):
        assert ARCHIVAL.name == "archival"
        assert ARCHIVAL.normalise is True
        assert ARCHIVAL.vad is True

    def test_custom_profile(self):
        p = AudioProfile("quiet", normalise=True, vad=False)
        assert p.name == "quiet"
        assert p.vad is False


# ---------------------------------------------------------------------------
# VAD
# ---------------------------------------------------------------------------

class TestVAD:
    def _silent_samples(self, n=16000):
        return np.zeros(n, dtype=np.int16)

    def _noisy_samples(self, n=16000, amplitude=8192):
        # Sine-like signal at mid amplitude
        t = np.linspace(0, 1, n)
        return (np.sin(2 * np.pi * 440 * t) * amplitude).astype(np.int16)

    def test_silent_returns_false(self):
        samples = self._silent_samples()
        assert _compute_has_speech(samples, 16000, -50.0) is False

    def test_near_zero_returns_false(self):
        # Samples at amplitude 1 → RMS ≈ 0.7, dBFS ≈ -93 — below threshold
        samples = np.ones(16000, dtype=np.int16)
        assert _compute_has_speech(samples, 16000, -50.0) is False

    def test_noisy_returns_true(self):
        samples = self._noisy_samples(amplitude=4096)
        assert _compute_has_speech(samples, 16000, -50.0) is True

    def test_threshold_respected(self):
        # Amplitude ~100 → RMS ≈ 70, dBFS ≈ -53
        samples = self._noisy_samples(amplitude=100)
        assert _compute_has_speech(samples, 16000, -50.0) is False
        # Relax threshold to -60 → should now detect speech
        assert _compute_has_speech(samples, 16000, -60.0) is True

    def test_empty_samples_returns_false(self):
        assert _compute_has_speech(np.array([], dtype=np.int16), 16000, -50.0) is False


# ---------------------------------------------------------------------------
# Clipping detection
# ---------------------------------------------------------------------------

class TestClipping:
    def test_clipping_detected_at_threshold(self):
        samples = np.full(1600, 32700, dtype=np.int16)
        _, has_clipping = _compute_peak_and_clipping(samples, 16000)
        assert has_clipping is True

    def test_clipping_detected_at_int16_max(self):
        samples = np.array([32767], dtype=np.int16)
        _, has_clipping = _compute_peak_and_clipping(samples, 16000)
        assert has_clipping is True

    def test_clean_audio_no_clipping(self):
        samples = np.full(1600, 16383, dtype=np.int16)
        _, has_clipping = _compute_peak_and_clipping(samples, 16000)
        assert has_clipping is False

    def test_peak_db_computed(self):
        # All samples at 32768/2 = 16384 → peak_db ≈ -6 dBFS
        samples = np.full(1600, 16384, dtype=np.int16)
        peak_db, _ = _compute_peak_and_clipping(samples, 16000)
        assert peak_db is not None
        assert -7.0 < peak_db < -5.0

    def test_empty_samples(self):
        peak_db, has_clipping = _compute_peak_and_clipping(np.array([], dtype=np.int16))
        assert peak_db is None
        assert has_clipping is False


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_scales_peak_to_near_int16_max(self):
        samples = np.full(1600, 1000, dtype=np.int16)
        result = _normalise_samples(samples)
        assert result.dtype == np.int16
        assert int(np.max(np.abs(result))) >= 32700

    def test_zero_samples_unchanged(self):
        samples = np.zeros(100, dtype=np.int16)
        result = _normalise_samples(samples)
        assert np.all(result == 0)

    def test_already_loud_not_clipped(self):
        samples = np.full(1600, 32000, dtype=np.int16)
        result = _normalise_samples(samples)
        assert int(np.max(np.abs(result.astype(np.int32)))) <= 32767
