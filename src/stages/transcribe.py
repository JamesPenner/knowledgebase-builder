"""Stage 3b — Transcribe: Whisper audio transcription for audio and video files."""
import logging
import subprocess
import threading
import time
from pathlib import Path

from src.config import Config
from src.media.audiotrack import prepare_audio
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".m2ts"}


class ModelLoadError(Exception):
    pass


def _transcribe_with_cli(
    wav_path: Path,
    whisper_cli: str,
    model_path: str,
    language: str = "auto",
    no_gpu: bool = False,
) -> tuple[str, str, list[dict]]:
    """Transcribe via the whisper-cli binary (Vulkan build). Returns (text, lang, segments)."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_prefix = Path(tmp_dir) / "out"
        cmd = [
            whisper_cli,
            "-m", model_path,
            "-f", str(wav_path),
            "-l", "auto" if language == "auto" else language,
            "-oj",
            "-of", str(out_prefix),
        ]
        if no_gpu:
            cmd.append("--no-gpu")

        result = subprocess.run(cmd, capture_output=True, timeout=None)
        json_path = Path(str(out_prefix) + ".json")
        if not json_path.exists():
            stderr = result.stderr.decode(errors="replace")
            raise RuntimeError(
                f"whisper-cli produced no JSON output (exit {result.returncode}). "
                f"stderr: {stderr[:500]}"
            )
        data = json.loads(json_path.read_text(encoding="utf-8"))

    detected_lang = (data.get("result") or {}).get("language") or "und"
    texts: list[str] = []
    segments: list[dict] = []
    for seg in data.get("transcription") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        offsets = seg.get("offsets") or {}
        start_ms = int(offsets.get("from", 0))
        end_ms = int(offsets.get("to", 0))
        texts.append(text)
        segments.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "avg_logprob": None,
        })

    return " ".join(texts), detected_lang, segments


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
    *,
    source_id: int | None = None,
    file_type: str | None = None,
) -> None:
    from src.db.corpus import (
        delete_transcript_segments_for_file,
        get_has_speech,
        get_pending_transcribe_files,
        open_corpus,
        set_has_speech,
        update_pipeline_checkpoint,
        upsert_transcript_segment,
        upsert_transcription,
    )

    if not config.audio_model:
        logger.warning("Transcribe: no audio_model configured — stage skipped")
        return

    use_cli = bool(config.whisper_cli)

    if not use_cli:
        try:
            import pywhispercpp  # noqa: F401
        except ImportError:
            logger.error("Transcribe: pywhispercpp not installed and whisper_cli not configured — stage skipped")
            return

    corpus_conn = open_corpus(corpus_path)

    try:
        model = None

        pending = get_pending_transcribe_files(corpus_conn, source_id=source_id, file_type=file_type)
        total = len(pending)

        if total == 0:
            logger.info("Transcribe: no pending files — stage skipped")
            progress.done()
            return

        if use_cli:
            progress.set_message(f"Transcribing with whisper-cli… ({total} files pending)", total=total)
        else:
            from pywhispercpp.model import Model as WhisperModel
            progress.set_message(f"Loading Whisper model… ({total} files pending)", total=total)
            ctx_params = {"use_gpu": False} if config.audio_gpu_layers == 0 else None
            try:
                model = WhisperModel(model=config.audio_model, context_params=ctx_params)
            except Exception as exc:
                raise ModelLoadError(
                    f"Whisper model failed to load: {exc}\n"
                    f"This is usually caused by insufficient VRAM or an invalid model path.\n"
                    f"Try setting 'audio_gpu_layers: 0' in config.yaml to run on CPU "
                    f"(slower but works on any machine)."
                ) from exc

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

            progress.update(i, total, f"Transcribe: {i + 1}/{total} — {Path(file_row['path']).name}")

            file_id = file_row["id"]
            file_path = Path(file_row["path"])
            file_type = file_row["file_type"] or ""

            is_video = file_type == "video"

            # Skip files already known to be silent from a previous audio prep run
            if get_has_speech(corpus_conn, file_id) is False:
                batch_tx.append((file_id, None, None, None, config.audio_model, "skipped"))
                skipped += 1
                if len(batch_tx) >= _BATCH_SIZE:
                    _flush_batch()
                continue

            try:
                with prepare_audio(file_path, config) as track:
                    if track is None:
                        if is_video:
                            batch_tx.append((file_id, None, None, None, config.audio_model, "no_audio"))
                            skipped += 1
                        else:
                            batch_tx.append((file_id, None, None, None, config.audio_model, "failed"))
                            errors += 1
                        if len(batch_tx) >= _BATCH_SIZE:
                            _flush_batch()
                        continue

                    if track.has_speech is not None:
                        set_has_speech(corpus_conn, file_id, track.has_speech)

                    if track.has_clipping:
                        logger.warning("transcribe: clipping detected in %s", file_path)

                    if track.has_speech is False:
                        batch_tx.append((file_id, None, None, None, config.audio_model, "skipped"))
                        skipped += 1
                        if len(batch_tx) >= _BATCH_SIZE:
                            _flush_batch()
                        continue

                    if use_cli:
                        transcript_text, detected_lang, segments = _transcribe_with_cli(
                            track.wav_path, config.whisper_cli, config.audio_model,
                            no_gpu=(config.audio_gpu_layers == 0),
                        )
                    else:
                        transcript_text, detected_lang, segments = _transcribe_audio(
                            track.wav_path, model
                        )

                    batch_tx.append((
                        file_id,
                        transcript_text or None,
                        detected_lang or None,
                        track.duration_ms,
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

    use_cli = bool(config.whisper_cli)

    if not use_cli:
        try:
            from pywhispercpp.model import Model as WhisperModel
        except ImportError:
            logger.error("Transcribe: pywhispercpp not installed and whisper_cli not configured")
            return None

    ext = path.suffix.lower()
    is_audio = ext in _AUDIO_EXTS
    is_video = ext in _VIDEO_EXTS

    if not is_audio and not is_video:
        return None

    model = None
    if not use_cli:
        ctx_params = {"use_gpu": False} if config.audio_gpu_layers == 0 else None
        try:
            model = WhisperModel(model=config.audio_model, context_params=ctx_params)
        except Exception as exc:
            raise ModelLoadError(
                f"Whisper model failed to load: {exc}\n"
                f"This is usually caused by insufficient VRAM or an invalid model path.\n"
                f"Try setting 'audio_gpu_layers: 0' in config.yaml to run on CPU "
                f"(slower but works on any machine)."
            ) from exc

    try:
        with prepare_audio(path, config) as track:
            if track is None:
                return None

            if use_cli:
                transcript_text, detected_lang, _ = _transcribe_with_cli(
                    track.wav_path, config.whisper_cli, config.audio_model,
                    no_gpu=(config.audio_gpu_layers == 0),
                )
            else:
                transcript_text, detected_lang, _ = _transcribe_audio(track.wav_path, model)

        import datetime
        return {
            "path": str(path),
            "transcript": transcript_text,
            "language": detected_lang,
            "duration_ms": track.duration_ms,
            "model": config.audio_model,
            "processed_at": datetime.datetime.now().isoformat(),
        }

    except Exception as exc:
        logger.warning("Transcribe: %s failed: %s", path, exc)
        return None
