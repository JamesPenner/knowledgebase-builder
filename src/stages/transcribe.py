"""Stage 3b — Transcribe: Whisper audio transcription for audio and video files."""
import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".m2ts"}


class ModelLoadError(Exception):
    pass


def _extract_audio(file_path: Path, ffmpeg: str) -> tuple[Path | None, int | None]:
    """Extract audio stream to a temporary wav file. Returns (wav_path, duration_ms) or (None, None)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        result = subprocess.run(
            [
                ffmpeg, "-v", "quiet",
                "-i", str(file_path),
                "-vn",                      # drop video
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                str(tmp_path),
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
            tmp_path.unlink(missing_ok=True)
            return None, None

        # Estimate duration from file size: 16kHz * 2 bytes * 1 channel
        size = tmp_path.stat().st_size
        duration_ms = int((size / (16000 * 2)) * 1000)
        return tmp_path, duration_ms

    except Exception as exc:
        logger.warning("transcribe: audio extraction failed for %s: %s", file_path, exc)
        tmp_path.unlink(missing_ok=True)
        return None, None


def _transcribe_audio(wav_path: Path, model, language: str = "auto") -> tuple[str, str, list[dict]]:
    """Transcribe a wav file. Returns (transcript_text, detected_language, segments)."""
    lang_arg = None if language == "auto" else language
    kwargs: dict = {}
    if lang_arg:
        kwargs["language"] = lang_arg

    raw_segments = model.transcribe(str(wav_path), **kwargs)

    texts: list[str] = []
    segments: list[dict] = []
    detected_lang = "und"

    for seg in raw_segments:
        text = getattr(seg, "text", "") or ""
        text = text.strip()
        if not text:
            continue

        # t0/t1 are in Whisper centisecond units (1/100 s); convert to ms
        t0 = getattr(seg, "t0", 0) or 0
        t1 = getattr(seg, "t1", 0) or 0
        start_ms = int(t0 * 10)
        end_ms = int(t1 * 10)

        avg_logprob = getattr(seg, "p", None)
        if avg_logprob is not None:
            try:
                avg_logprob = float(avg_logprob)
            except (TypeError, ValueError):
                avg_logprob = None

        texts.append(text)
        segments.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "avg_logprob": avg_logprob,
        })

    # Attempt to get detected language from model internals (model-dependent)
    try:
        detected_lang = model.lang or "und"
    except AttributeError:
        pass

    return " ".join(texts), detected_lang, segments


# ---------------------------------------------------------------------------
# Pipeline stage entry point
# ---------------------------------------------------------------------------

def run_transcribe(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    from src.db.corpus import (
        delete_transcript_segments_for_file,
        get_pending_transcribe_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_transcript_segment,
        upsert_transcription,
    )

    if not config.audio_model:
        logger.warning("Transcribe: no audio_model configured — stage skipped")
        return

    try:
        import pywhispercpp  # noqa: F401
    except ImportError:
        logger.error("Transcribe: pywhispercpp not installed — stage skipped")
        return

    corpus_conn = open_corpus(corpus_path)

    try:
        from pywhispercpp.model import Model as WhisperModel

        try:
            model = WhisperModel(
                model=config.audio_model,
                n_gpu_layers=config.audio_gpu_layers,
            )
        except Exception as exc:
            raise ModelLoadError(
                f"Whisper model failed to load: {exc}\n"
                f"This is usually caused by insufficient VRAM or an invalid model path.\n"
                f"Try reducing 'audio_gpu_layers' in config.yaml, "
                f"or set it to 0 to run on CPU (slower but works on any machine)."
            ) from exc

        pending = get_pending_transcribe_files(corpus_conn)
        total = len(pending)
        processed = skipped = errors = 0
        start = time.monotonic()

        batch_tx: list[tuple] = []  # (file_id, text, lang, duration_ms, model, status)
        batch_segs: list[tuple[int, list[dict]]] = []  # (file_id, segments)

        def _flush_batch() -> None:
            for seg_file_id, segs in batch_segs:
                delete_transcript_segments_for_file(corpus_conn, seg_file_id)
                for seg in segs:
                    upsert_transcript_segment(
                        corpus_conn,
                        seg_file_id,
                        seg["start_ms"],
                        seg["end_ms"],
                        seg["text"],
                        seg["avg_logprob"],
                    )
            for args in batch_tx:
                upsert_transcription(corpus_conn, *args)
            if batch_tx or batch_segs:
                corpus_conn.commit()
            batch_tx.clear()
            batch_segs.clear()

        for i, file_row in enumerate(pending):
            if cancel_event.is_set():
                break

            progress.update(i, total, f"Transcribe: {i + 1}/{total}")

            file_id = file_row["id"]
            file_path = Path(file_row["path"])
            file_type = file_row["file_type"] or ""
            ext = file_path.suffix.lower()

            is_audio = file_type == "audio" or (not file_type and ext in _AUDIO_EXTS)
            is_video = file_type == "video" or (not file_type and ext in _VIDEO_EXTS)

            wav_path: Path | None = None
            duration_ms: int | None = None
            owned_tmp = False

            try:
                if is_audio:
                    # Audio files: extract to wav if not already
                    if ext == ".wav":
                        wav_path = file_path
                        size = file_path.stat().st_size
                        duration_ms = int((size / (16000 * 2)) * 1000)
                    else:
                        wav_path, duration_ms = _extract_audio(file_path, config.ffmpeg)
                        owned_tmp = wav_path is not None
                        if wav_path is None:
                            batch_tx.append((file_id, None, None, None, config.audio_model, "failed"))
                            errors += 1
                            if len(batch_tx) >= _BATCH_SIZE:
                                _flush_batch()
                            continue

                elif is_video:
                    wav_path, duration_ms = _extract_audio(file_path, config.ffmpeg)
                    owned_tmp = wav_path is not None
                    if wav_path is None:
                        # No audio stream in video
                        batch_tx.append((file_id, None, None, None, config.audio_model, "no_audio"))
                        skipped += 1
                        if len(batch_tx) >= _BATCH_SIZE:
                            _flush_batch()
                        continue
                else:
                    continue

                transcript_text, detected_lang, segments = _transcribe_audio(wav_path, model)

                batch_tx.append((
                    file_id,
                    transcript_text or None,
                    detected_lang or None,
                    duration_ms,
                    config.audio_model,
                    "done",
                ))
                if segments:
                    batch_segs.append((file_id, segments))
                processed += 1

            except Exception as exc:
                logger.warning("Transcribe: file_id=%d path=%s failed: %s", file_id, file_path, exc)
                batch_tx.append((file_id, None, None, None, config.audio_model, "failed"))
                errors += 1

            finally:
                if owned_tmp and wav_path is not None:
                    wav_path.unlink(missing_ok=True)

            if len(batch_tx) >= _BATCH_SIZE:
                _flush_batch()

        _flush_batch()

        if not cancel_event.is_set():
            duration = time.monotonic() - start
            update_pipeline_checkpoint(corpus_conn, "transcribe", processed, skipped, errors, duration)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()


# ---------------------------------------------------------------------------
# Stateless single-file variant (used by quick-transcribe)
# ---------------------------------------------------------------------------

def run_transcribe_file(
    path: Path,
    config: Config,
    db=None,
) -> dict | None:
    if not config.audio_model:
        logger.warning("Transcribe: no audio_model configured")
        return None

    try:
        from pywhispercpp.model import Model as WhisperModel
    except ImportError:
        logger.error("Transcribe: pywhispercpp not installed")
        return None

    ext = path.suffix.lower()
    is_audio = ext in _AUDIO_EXTS
    is_video = ext in _VIDEO_EXTS

    if not is_audio and not is_video:
        return None

    try:
        model = WhisperModel(
            model=config.audio_model,
            n_gpu_layers=config.audio_gpu_layers,
        )
    except Exception as exc:
        raise ModelLoadError(
            f"Whisper model failed to load: {exc}\n"
            f"Try reducing 'audio_gpu_layers' in config.yaml."
        ) from exc

    wav_path: Path | None = None
    duration_ms: int | None = None
    owned_tmp = False

    try:
        if is_audio and ext == ".wav":
            wav_path = path
            size = path.stat().st_size
            duration_ms = int((size / (16000 * 2)) * 1000)
        else:
            wav_path, duration_ms = _extract_audio(path, config.ffmpeg)
            owned_tmp = wav_path is not None
            if wav_path is None:
                return None

        transcript_text, detected_lang, _ = _transcribe_audio(wav_path, model)

        import datetime
        return {
            "path": str(path),
            "transcript": transcript_text,
            "language": detected_lang,
            "duration_ms": duration_ms,
            "model": config.audio_model,
            "processed_at": datetime.datetime.now().isoformat(),
        }

    except Exception as exc:
        logger.warning("Transcribe: %s failed: %s", path, exc)
        return None

    finally:
        if owned_tmp and wav_path is not None:
            wav_path.unlink(missing_ok=True)
