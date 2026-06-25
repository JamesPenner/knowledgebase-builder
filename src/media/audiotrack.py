"""Shared audio preparation layer — ffmpeg extraction, VAD, clipping, normalisation."""
import contextlib
import logging
import math
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_INT16_CLIP_THRESHOLD = 32700
_INT16_MAX = 32768.0


@dataclass
class AudioProfile:
    name: str
    normalise: bool = False
    vad: bool = True


DEFAULT = AudioProfile("default", normalise=False, vad=True)
ARCHIVAL = AudioProfile("archival", normalise=True, vad=True)


@dataclass
class AudioTrack:
    file_path: Path
    wav_path: Path
    sample_rate: int
    duration_ms: int
    has_speech: bool | None
    peak_db: float | None
    has_clipping: bool
    normalised: bool
    segment_start_ms: int | None
    segment_end_ms: int | None
    owned: bool


# ---------------------------------------------------------------------------
# Internal signal processing helpers — accept numpy arrays, testable standalone
# ---------------------------------------------------------------------------

def _compute_has_speech(samples: Any, sample_rate: int, threshold_dbfs: float) -> bool:
    """Return True if any 100 ms window exceeds the RMS dBFS threshold."""
    import numpy as np
    window_size = max(1, int(sample_rate * 0.1))
    floats = samples.astype(np.float64)
    for i in range(0, len(floats), window_size):
        window = floats[i : i + window_size]
        if len(window) == 0:
            continue
        rms = float(np.sqrt(np.mean(window ** 2)))
        if rms > 0:
            rms_dbfs = 20.0 * math.log10(rms / _INT16_MAX)
            if rms_dbfs > threshold_dbfs:
                return True
    return False


def _compute_peak_and_clipping(samples: Any, sample_rate: int = 16000) -> tuple[float | None, bool]:
    """Return (peak_db, has_clipping).

    Clipping is True if any 100 ms block has a peak amplitude >= _INT16_CLIP_THRESHOLD.
    """
    import numpy as np
    if len(samples) == 0:
        return None, False
    abs_samples = np.abs(samples.astype(np.int32))
    peak = int(np.max(abs_samples))
    peak_db = 20.0 * math.log10(peak / _INT16_MAX) if peak > 0 else -96.0
    window_size = max(1, int(sample_rate * 0.1))
    has_clipping = False
    for i in range(0, len(abs_samples), window_size):
        if int(np.max(abs_samples[i : i + window_size])) >= _INT16_CLIP_THRESHOLD:
            has_clipping = True
            break
    return peak_db, has_clipping


def _normalise_samples(samples: Any) -> Any:
    """Peak-normalise int16 samples to near INT16_MAX. Returns int16 array."""
    import numpy as np
    abs_max = float(np.max(np.abs(samples.astype(np.int32))))
    if abs_max == 0:
        return samples
    scale = (_INT16_MAX - 1.0) / abs_max
    return np.clip(samples.astype(np.float64) * scale, -32768.0, 32767.0).astype(np.int16)


# ---------------------------------------------------------------------------
# WAV I/O helpers
# ---------------------------------------------------------------------------

def _read_wav_samples(wav_path: Path) -> tuple[Any, int]:
    import numpy as np
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).copy(), sr


def _write_wav_samples(wav_path: Path, samples: Any, sample_rate: int) -> None:
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# ---------------------------------------------------------------------------

def _probe_has_audio(file_path: Path, ffprobe: str) -> bool:
    """Return True if the file has at least one audio stream."""
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet",
                "-show_streams", "-select_streams", "a",
                "-print_format", "compact",
                str(file_path),
            ],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _extract_wav(
    file_path: Path,
    ffmpeg: str,
    out_dir: Path,
    segment_start_ms: int | None,
    segment_end_ms: int | None,
) -> Path | None:
    """Extract to 16 kHz mono WAV in out_dir. Returns path or None on failure."""
    out_path = out_dir / "audio.wav"
    cmd = [ffmpeg, "-v", "quiet", "-y"]
    if segment_start_ms is not None:
        cmd += ["-ss", str(segment_start_ms / 1000.0)]
    cmd += ["-i", str(file_path)]
    if segment_end_ms is not None:
        start_s = (segment_start_ms or 0) / 1000.0
        duration_s = segment_end_ms / 1000.0 - start_s
        cmd += ["-t", str(duration_s)]
    cmd += ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(out_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            return None
        return out_path
    except Exception as exc:
        logger.warning("audiotrack: ffmpeg extraction failed for %s: %s", file_path, exc)
        return None


# ---------------------------------------------------------------------------
# Core build function
# ---------------------------------------------------------------------------

def _build_track(
    file_path: Path,
    config,
    tmpdir: Path,
    *,
    profile: AudioProfile | None,
    normalise: bool | None,
    vad: bool | None,
    segment_start_ms: int | None,
    segment_end_ms: int | None,
) -> "AudioTrack | None":
    resolved = profile or DEFAULT
    do_normalise = normalise if normalise is not None else resolved.normalise
    do_vad = vad if vad is not None else resolved.vad

    if not _probe_has_audio(file_path, config.ffprobe):
        return None

    wav_path = _extract_wav(file_path, config.ffmpeg, tmpdir, segment_start_ms, segment_end_ms)
    if wav_path is None:
        return None

    samples, sample_rate = _read_wav_samples(wav_path)
    duration_ms = int(len(samples) / sample_rate * 1000)

    peak_db, has_clipping = _compute_peak_and_clipping(samples, sample_rate)

    normalised = False
    if do_normalise:
        samples = _normalise_samples(samples)
        normalised = True
        _write_wav_samples(wav_path, samples, sample_rate)

    has_speech: bool | None = None
    if do_vad:
        threshold = getattr(config, "vad_silence_threshold", -50.0)
        has_speech = _compute_has_speech(samples, sample_rate, threshold)

    return AudioTrack(
        file_path=file_path,
        wav_path=wav_path,
        sample_rate=sample_rate,
        duration_ms=duration_ms,
        has_speech=has_speech,
        peak_db=peak_db,
        has_clipping=has_clipping,
        normalised=normalised,
        segment_start_ms=segment_start_ms,
        segment_end_ms=segment_end_ms,
        owned=True,
    )


# ---------------------------------------------------------------------------
# Public context manager
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def prepare_audio(
    file_path: Path,
    config,
    *,
    profile: AudioProfile | None = None,
    normalise: bool | None = None,
    vad: bool | None = None,
    segment_start_ms: int | None = None,
    segment_end_ms: int | None = None,
) -> Iterator["AudioTrack | None"]:
    """Context manager that extracts, analyses, and optionally normalises audio.

    Yields an AudioTrack on success, or None if the file has no audio stream
    or any unrecoverable error occurs. The temporary WAV file is deleted on exit.
    """
    tmpdir = tempfile.TemporaryDirectory()
    try:
        track: AudioTrack | None = None
        try:
            track = _build_track(
                file_path, config, Path(tmpdir.name),
                profile=profile, normalise=normalise, vad=vad,
                segment_start_ms=segment_start_ms, segment_end_ms=segment_end_ms,
            )
        except Exception as exc:
            logger.warning("prepare_audio: %s: %s", file_path, exc)
        yield track
    finally:
        tmpdir.cleanup()
