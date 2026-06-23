"""Stage 3a — Describe: vision model generates descriptions for images and videos."""
import base64
import io
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
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".m2ts"}


class ModelLoadError(Exception):
    pass


# ---------------------------------------------------------------------------
# Multimodal chat handler
# ---------------------------------------------------------------------------

_CHAT_HANDLER_MAP = {
    "qwen2_vl":  "Qwen25VLChatHandler",
    "gemma3":    "Llava16ChatHandler",
    "moondream": "MoondreamChatHandler",
    "llava15":   "Llava15ChatHandler",
    "llava16":   "Llava16ChatHandler",
    "llava":     "Llava15ChatHandler",
}

_AUTODETECT_PATTERNS = [
    ("qwen2",     "qwen2_vl"),
    ("moondream", "moondream"),
    ("gemma",     "gemma3"),
]


def _resolve_chat_format(mmproj_path: str, model_path: str) -> str:
    """Infer chat format from mmproj and model filenames; falls back to 'llava'."""
    for src in (Path(mmproj_path).name.lower(), Path(model_path).name.lower()):
        for pattern, fmt in _AUTODETECT_PATTERNS:
            if pattern in src:
                return fmt
    return "llava"


def _make_chat_handler(mmproj_path: str, chat_format: str, model_path: str = ""):
    """Return a llama_cpp chat handler for the given mmproj file."""
    from llama_cpp import llama_chat_format as _fmt

    if not chat_format:
        chat_format = _resolve_chat_format(mmproj_path, model_path)

    handler_name = _CHAT_HANDLER_MAP.get(chat_format, "Llava15ChatHandler")
    handler_cls = getattr(_fmt, handler_name, None)
    if handler_cls is None:
        available = [x for x in dir(_fmt) if "ChatHandler" in x]
        raise ModelLoadError(
            f"Chat handler '{handler_name}' not found in installed llama_cpp. "
            f"Available handlers: {available}. "
            f"Set models.vision_chat_format in config.yaml to one of: {list(_CHAT_HANDLER_MAP)}"
        )
    return handler_cls(clip_model_path=mmproj_path, verbose=False)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_describe_prompt(captured_fields: list[dict], derived_tags: list[str], focus: str) -> str:
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
        return "\n".join(parts) + "\n\n" + _BASE_PROMPT
    return _BASE_PROMPT


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
# File context retrieval
# ---------------------------------------------------------------------------

def _get_file_context(corpus_conn, kb_conn, file_id: int) -> tuple[list[dict], list[str]]:
    captured_rows = corpus_conn.execute(
        "SELECT field_name, value FROM file_captured_fields WHERE file_id = ? AND value IS NOT NULL",
        (file_id,),
    ).fetchall()

    fields_with_type: list[dict] = []
    for row in captured_rows:
        rule = kb_conn.execute(
            "SELECT value_type FROM capture_rules WHERE extract_as = ? LIMIT 1",
            (row["field_name"],),
        ).fetchone()
        value_type = rule["value_type"] if rule else "text"
        fields_with_type.append({
            "field_name": row["field_name"],
            "value": row["value"],
            "value_type": value_type,
        })

    derived = corpus_conn.execute(
        "SELECT tag FROM file_derived_tags WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    return fields_with_type, [r["tag"] for r in derived]


# ---------------------------------------------------------------------------
# Image inference
# ---------------------------------------------------------------------------

def _describe_image(file_path: Path, model, prompt: str) -> str:
    from PIL import Image

    with Image.open(file_path) as img:
        img = img.convert("RGB")
        if max(img.size) > 512:
            img.thumbnail((512, 512), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        image_bytes = buf.getvalue()

    b64 = base64.b64encode(image_bytes).decode()
    output = model.create_chat_completion(
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        max_tokens=512,
        temperature=0.1,
    )
    raw = output["choices"][0]["message"]["content"]
    return (raw or "").strip()


# ---------------------------------------------------------------------------
# Pipeline stage entry point
# ---------------------------------------------------------------------------

def run_describe(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    from src.db.corpus import (
        delete_video_frames_for_file,
        get_pending_describe_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_description,
    )
    from src.db.kb import open_kb

    if not config.vision_model:
        logger.warning("Describe: no vision_model configured — stage skipped")
        return

    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        logger.error("Describe: llama_cpp not installed — stage skipped")
        return

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        import llama_cpp as _llama

        pending = get_pending_describe_files(corpus_conn)
        total = len(pending)

        if total == 0:
            logger.info("Describe: no pending files — stage skipped")
            progress.done()
            return

        progress.set_message(f"Loading vision model… ({total} files pending)", total=total)

        try:
            _load_kwargs: dict = dict(
                model_path=config.vision_model,
                n_gpu_layers=config.vision_gpu_layers,
                n_ctx=32768,   # Qwen2.5-VL visual tokens need >> 4096; use recommended 32k
                verbose=False,
            )
            if config.vision_mmproj:
                _load_kwargs["chat_handler"] = _make_chat_handler(
                    config.vision_mmproj, config.vision_chat_format, config.vision_model
                )
            model = _llama.Llama(**_load_kwargs)
        except Exception as exc:
            raise ModelLoadError(
                f"Vision model failed to load: {exc}\n"
                f"This is usually caused by insufficient VRAM.\n"
                f"Try reducing 'vision_gpu_layers' in config.yaml, "
                f"or set it to 0 to run on CPU (slower but works on any machine)."
            ) from exc

        processed = skipped = errors = 0
        start = time.monotonic()

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

            # Determine routing by file_type first, ext as fallback
            # DB stores "images"/"video" (ingest normalises to those values)
            is_image = file_type in ("image", "images") or (not file_type and ext in _IMAGE_EXTS)
            is_video = file_type in ("video", "videos") or (not file_type and ext in _VIDEO_EXTS)

            if not is_image and not is_video:
                # audio or unknown — no visual content
                batch.append((file_id, None, None, config.vision_model, "skipped"))
                skipped += 1
                if len(batch) >= _BATCH_SIZE:
                    _flush_batch()
                continue

            captured_fields, derived_tags = _get_file_context(corpus_conn, kb_conn, file_id)
            prompt = _build_describe_prompt(captured_fields, derived_tags, config.focus)

            try:
                if is_image:
                    description_raw = _describe_image(file_path, model, prompt)
                else:
                    from src.stages.video import describe_video
                    delete_video_frames_for_file(corpus_conn, file_id)
                    description_raw = describe_video(
                        file_path, file_id, model, config,
                        conn=corpus_conn,
                        prompt=prompt,
                    )
                    if not description_raw:
                        # ffprobe/ffmpeg failed or extracted no frames
                        logger.warning("Describe: video %s produced no frames — marked failed", file_path)
                        batch.append((file_id, None, None, config.vision_model, "failed"))
                        errors += 1
                        if len(batch) >= _BATCH_SIZE:
                            _flush_batch()
                        continue

                # Strip leading colon artifact that Qwen2.5-VL sometimes prepends
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
) -> str | None:
    if not config.vision_model:
        logger.warning("Describe: no vision_model configured")
        return None

    try:
        import llama_cpp as _llama
    except ImportError:
        logger.error("Describe: llama_cpp not installed")
        return None

    ext = path.suffix.lower()
    is_image = ext in _IMAGE_EXTS  # single-file path uses ext only (no DB file_type)
    is_video = ext in _VIDEO_EXTS

    if not is_image and not is_video:
        return None

    try:
        _load_kwargs: dict = dict(
            model_path=config.vision_model,
            n_gpu_layers=config.vision_gpu_layers,
            n_ctx=32768,
            verbose=False,
        )
        if config.vision_mmproj:
            _load_kwargs["chat_handler"] = _make_chat_handler(
                config.vision_mmproj, config.vision_chat_format, config.vision_model
            )
        model = _llama.Llama(**_load_kwargs)
    except Exception as exc:
        raise ModelLoadError(
            f"Vision model failed to load: {exc}\n"
            f"Try reducing 'vision_gpu_layers' in config.yaml."
        ) from exc

    prompt = _build_describe_prompt([], [], focus)

    try:
        if is_image:
            return _describe_image(path, model, prompt)
        else:
            from src.stages.video import describe_video
            return describe_video(path, None, model, config, conn=None, prompt=prompt)
    except Exception as exc:
        logger.warning("Describe: %s failed: %s", path, exc)
        return None
