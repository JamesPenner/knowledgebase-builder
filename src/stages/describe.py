"""Stage 3a — Describe: vision model generates descriptions for images and videos."""
import logging
import re
import threading
import time
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10
_BASE_PROMPT = (
    "Describe this image in detail. Focus on the subjects, setting, "
    "activity, and any visible text or identifiable objects."
)
_AGGREGATE_INSTRUCTION = (
    "Using the frame descriptions above, write a single cohesive description of the video.\n\n"
    "Rules:\n"
    "- Prioritise details and themes that appear consistently across multiple frames — "
    "repeated elements are more reliable than single-frame observations.\n"
    "- If a detail appears in only one frame and conflicts with what other frames show "
    "(e.g. a different clothing colour, a person not seen elsewhere), omit it rather than "
    "including potentially hallucinated content.\n"
    "- Where frame descriptions agree or reinforce each other, describe those elements "
    "with confidence.\n"
    "- Focus on the overall content, activity, and setting."
)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".m2ts"}


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_describe_prompt(
    captured_fields: list[dict],
    derived_tags: list[str],
    focus: str,
    base_prompt: str = _BASE_PROMPT,
) -> str:
    parts = []
    if focus:
        parts.append(f"DOMAIN FOCUS: {focus}")
    for field in captured_fields:
        value_type = field.get("value_type") or "text"
        value = field.get("value") or ""
        if not value:
            continue
        if value_type == "date":
            parts.append(f"The filename indicates this was filmed on {value}.")
        elif value_type == "time":
            parts.append(f"Recorded at {value}.")
        elif value_type == "code":
            parts.append(f"Project code: {value}.")
        elif value_type not in ("numeric",):
            parts.append(str(value))
    if derived_tags:
        parts.append(f"Confirmed context: {', '.join(derived_tags)}.")
    if parts:
        return "\n".join(parts) + "\n\n" + base_prompt
    return base_prompt


# ---------------------------------------------------------------------------
# Description normalization
# ---------------------------------------------------------------------------

def _normalize_description(raw: str, kb_conn) -> str:
    rules = kb_conn.execute(
        "SELECT pattern, replacement FROM substitute_rules"
        " WHERE applies_to IN ('description', 'both') ORDER BY id"
    ).fetchall()
    result = raw
    for rule in rules:
        try:
            result = re.sub(rule["pattern"], rule["replacement"], result)
        except re.error:
            pass
    return result


# ---------------------------------------------------------------------------
# Frame inference (moved from video.py)
# ---------------------------------------------------------------------------

def _describe_frame(jpeg_bytes: bytes, session, system: str, user: str) -> str:
    import io as _io
    from PIL import Image as _Image
    with _Image.open(_io.BytesIO(jpeg_bytes)) as _img:
        _img = _img.convert("RGB")
        if max(_img.size) > 512:
            _img.thumbnail((512, 512), _Image.LANCZOS)
        _buf = _io.BytesIO()
        _img.save(_buf, format="JPEG", quality=85)
        jpeg_bytes = _buf.getvalue()
    return session.generate(system, user, images=[jpeg_bytes], max_tokens=256, temperature=0.1).lstrip(": \n")


def _aggregate_descriptions(
    frame_descriptions: list[str],
    focus: str,
    session,
    instruction: str = _AGGREGATE_INSTRUCTION,
) -> str:
    parts = []
    if focus:
        parts.append(f"DOMAIN FOCUS: {focus}")
    parts.append("The following are descriptions of sequential frames from a video:")
    for i, desc in enumerate(frame_descriptions, 1):
        parts.append(f"Frame {i}: {desc}")
    parts.append(instruction)
    prompt = "\n\n".join(parts)
    return session.generate("", prompt, max_tokens=512, temperature=0.1).lstrip(": \n")


# ---------------------------------------------------------------------------
# Pipeline stage entry point
# ---------------------------------------------------------------------------

def run_describe(
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
        delete_video_frames_for_file,
        get_pending_describe_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_description,
    )
    from src.db.kb import load_stage_prompt, open_kb
    from src.llm.session import ModelLoadError, VisionSession
    from src.text.context import build_file_context

    if not config.vision_model:
        logger.warning("Describe: no vision_model configured — stage skipped")
        return

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        base_prompt = load_stage_prompt(kb_conn, "describe", "system", default=_BASE_PROMPT)
        aggregate_instruction = load_stage_prompt(
            kb_conn, "describe", "aggregate", default=_AGGREGATE_INSTRUCTION
        )

        pending = get_pending_describe_files(corpus_conn, source_id=source_id, file_type=file_type)
        total = len(pending)

        if total == 0:
            logger.info("Describe: no pending files — stage skipped")
            progress.done()
            return

        progress.set_message(f"Loading vision model… ({total} files pending)", total=total)

        processed = skipped = errors = 0
        start = time.monotonic()

        with VisionSession(
            config.vision_model,
            mmproj_path=config.vision_mmproj,
            chat_format=config.vision_chat_format,
            n_gpu_layers=config.vision_gpu_layers,
            n_ctx=32768,
            max_retries=config.deep_seek_max_iter if config.deep_seek else 0,
        ) as session:
            batch: list[tuple] = []

            def _flush_batch() -> None:
                for args in batch:
                    upsert_description(corpus_conn, *args)
                if batch:
                    corpus_conn.commit()
                batch.clear()

            for i, file_row in enumerate(pending):
                if cancel_event.is_set():
                    break

                progress.update(i, total, f"Describe: {i + 1}/{total} — {Path(file_row['path']).name}")

                file_id = file_row["id"]
                file_path = Path(file_row["path"])
                file_type = file_row["file_type"] or ""
                ext = (file_row["ext"] or "").lower()

                is_video = file_type in ("video", "videos") or (not file_type and ext in _VIDEO_EXTS)
                is_visual = (
                    file_type in ("image", "images", "video", "videos")
                    or (not file_type and ext in _IMAGE_EXTS | _VIDEO_EXTS)
                )

                if not is_visual:
                    batch.append((file_id, None, None, config.vision_model, "skipped"))
                    skipped += 1
                    if len(batch) >= _BATCH_SIZE:
                        _flush_batch()
                    continue

                ctx = build_file_context(corpus_conn, kb_conn, file_id)
                prompt = _build_describe_prompt(
                    ctx.captured_fields, ctx.derived_tags, config.focus, base_prompt=base_prompt
                )

                try:
                    from src.media.frameset import prepare_visual

                    if is_video:
                        delete_video_frames_for_file(corpus_conn, file_id)

                    frameset = prepare_visual(file_path, config)
                    if frameset is None:
                        logger.warning("Describe: prepare_visual returned None for %s", file_path)
                        batch.append((file_id, None, None, config.vision_model, "failed"))
                        errors += 1
                        if len(batch) >= _BATCH_SIZE:
                            _flush_batch()
                        continue

                    frame_descriptions: list[str] = []
                    for frame_index, frame in enumerate(frameset.frames):
                        try:
                            desc = _describe_frame(frame.jpeg_bytes, session, "", prompt or _BASE_PROMPT)
                            frame_descriptions.append(desc)
                        except Exception as exc:
                            logger.warning("Describe: frame %d failed for %s: %s", frame_index, file_path, exc)

                        if frameset.file_type == "video":
                            from src.db.corpus import insert_video_frame
                            insert_video_frame(
                                corpus_conn, file_id, frame_index,
                                frame.timestamp_ms, frame.phash,
                                frame_descriptions[-1] if frame_descriptions else None,
                                config.vision_model,
                            )

                    if not frame_descriptions:
                        logger.warning("Describe: %s produced no descriptions — marked failed", file_path)
                        batch.append((file_id, None, None, config.vision_model, "failed"))
                        errors += 1
                        if len(batch) >= _BATCH_SIZE:
                            _flush_batch()
                        continue

                    if len(frame_descriptions) == 1:
                        description_raw = frame_descriptions[0]
                    else:
                        try:
                            description_raw = _aggregate_descriptions(
                                frame_descriptions, config.focus, session,
                                instruction=aggregate_instruction,
                            )
                        except Exception as exc:
                            logger.warning("Describe: aggregation failed for %s: %s", file_path, exc)
                            description_raw = " | ".join(frame_descriptions)

                    description_raw = description_raw.lstrip(": \n")
                    if not description_raw:
                        logger.warning("Describe: file_id=%d path=%s returned empty description", file_id, file_path)
                    description_normalized = _normalize_description(description_raw, kb_conn)
                    batch.append((
                        file_id,
                        description_raw,
                        description_normalized,
                        config.vision_model,
                        "done",
                    ))
                    processed += 1

                except Exception as exc:
                    logger.warning("Describe: file_id=%d path=%s failed: %s", file_id, file_path, exc)
                    batch.append((file_id, None, None, config.vision_model, "failed"))
                    errors += 1

                if len(batch) >= _BATCH_SIZE:
                    _flush_batch()

            _flush_batch()

        if not cancel_event.is_set():
            duration = time.monotonic() - start
            update_pipeline_checkpoint(corpus_conn, "describe", processed, skipped, errors, duration)
            corpus_conn.commit()
            progress.done()

    except ModelLoadError:
        raise

    finally:
        corpus_conn.close()
        kb_conn.close()


# ---------------------------------------------------------------------------
# Stateless single-file variant (used by quick-describe)
# ---------------------------------------------------------------------------

def run_describe_file(
    path: Path,
    config: Config,
    focus: str = "",
    db=None,
    kb_path: Path | None = None,
) -> str | None:
    from src.llm.session import ModelLoadError, VisionSession

    if not config.vision_model:
        logger.warning("Describe: no vision_model configured")
        return None

    ext = path.suffix.lower()
    if ext not in _IMAGE_EXTS and ext not in _VIDEO_EXTS:
        return None

    base_prompt = _BASE_PROMPT
    aggregate_instruction = _AGGREGATE_INSTRUCTION
    if kb_path is not None:
        try:
            from src.db.kb import load_stage_prompt, open_kb
            _kb_conn = open_kb(kb_path)
            base_prompt = load_stage_prompt(_kb_conn, "describe", "system", default=_BASE_PROMPT)
            aggregate_instruction = load_stage_prompt(
                _kb_conn, "describe", "aggregate", default=_AGGREGATE_INSTRUCTION
            )
            _kb_conn.close()
        except Exception as exc:
            logger.warning("Describe: could not load prompts from KB at %s: %s", kb_path, exc)

    prompt = _build_describe_prompt([], [], focus, base_prompt=base_prompt)

    try:
        from src.media.frameset import prepare_visual

        with VisionSession(
            config.vision_model,
            mmproj_path=config.vision_mmproj,
            chat_format=config.vision_chat_format,
            n_gpu_layers=config.vision_gpu_layers,
            n_ctx=32768,
            max_retries=config.deep_seek_max_iter if config.deep_seek else 0,
        ) as session:
            frameset = prepare_visual(path, config)
            if frameset is None:
                return None

            frame_descriptions: list[str] = []
            for frame in frameset.frames:
                try:
                    desc = _describe_frame(frame.jpeg_bytes, session, "", prompt or _BASE_PROMPT)
                    frame_descriptions.append(desc)
                except Exception as exc:
                    logger.warning("Describe: quick frame failed for %s: %s", path, exc)

            if not frame_descriptions:
                return None
            if len(frame_descriptions) == 1:
                return frame_descriptions[0]
            try:
                return _aggregate_descriptions(
                    frame_descriptions, focus, session,
                    instruction=aggregate_instruction,
                )
            except Exception as exc:
                logger.warning("Describe: quick aggregation failed for %s: %s", path, exc)
                return " | ".join(frame_descriptions)
    except ModelLoadError:
        raise
    except Exception as exc:
        logger.warning("Describe: %s failed: %s", path, exc)
        return None
