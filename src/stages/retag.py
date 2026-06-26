"""Stage 5 — Retag: text-only LLM re-tags descriptions against vocabulary."""
import json
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter
from src.text.context import FileContext

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a metadata tagging assistant. Given a description and a controlled vocabulary, \
identify which vocabulary terms apply to this content. You may also propose new terms \
that would be good additions to the vocabulary.

Respond with valid JSON only — no markdown, no explanation. Use this exact schema:
{"tags": ["term1", "term2"], "refined_description": "...", "new_terms_proposed": ["term3"]}

Rules:
- tags: only use terms from the vocabulary list; include all that genuinely apply
- refined_description: correct obvious errors; keep it factual; do not change meaning
- new_terms_proposed: terms you consider valuable that are not in the vocabulary; leave empty if none
"""


def _build_prompt(ctx: FileContext, focus: str) -> str:
    parts = []
    if focus:
        parts.append(f"DOMAIN FOCUS: {focus}")
    parts.append(f"VOCABULARY:\n{', '.join(ctx.vocab_terms) if ctx.vocab_terms else '(none)'}")
    if ctx.derived_tags:
        parts.append(f"CONFIRMED TAGS (already assigned):\n{', '.join(ctx.derived_tags)}")
    parts.append(f"DESCRIPTION:\n{ctx.description}")
    parts.append("JSON RESPONSE:")
    return "\n\n".join(parts)


def _parse_llm_response(raw: str) -> tuple[list[str], str, list[str]]:
    try:
        data = json.loads(raw)
        tags = [str(t) for t in (data.get("tags") or [])]
        refined = str(data.get("refined_description") or "")
        new_terms = [str(t) for t in (data.get("new_terms_proposed") or [])]
        return tags, refined, new_terms
    except (json.JSONDecodeError, AttributeError):
        return [], "", []


def run_retag(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    *,
    source_id: int | None = None,
    set_id: int | None = None,
) -> None:
    from src.db.corpus import (
        get_pending_retag_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_retag_output,
    )
    from src.db.kb import load_stage_prompt, open_kb
    from src.llm.session import ModelLoadError, TextSession
    from src.text.context import build_file_context

    if not config.text_model:
        logger.warning("Retag: no text_model configured — stage skipped")
        return

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        system_prompt = load_stage_prompt(kb_conn, "retag", "system", default=_SYSTEM_PROMPT)

        pending = get_pending_retag_files(corpus_conn, source_id=source_id, set_id=set_id)
        total = len(pending)
        processed = skipped = errors = 0

        try:
            with TextSession(
                config.text_model,
                n_gpu_layers=config.text_gpu_layers,
                max_retries=config.deep_seek_max_iter if config.deep_seek else 0,
            ) as session:
                batch: list[tuple] = []

                def _flush_batch() -> None:
                    for args in batch:
                        upsert_retag_output(corpus_conn, *args)
                    if batch:
                        corpus_conn.commit()
                    batch.clear()

                for i, row in enumerate(pending):
                    if cancel_event.is_set():
                        break

                    progress.update(i, total, f"Retag: {i + 1}/{total}")

                    file_id = row["id"]
                    ctx = build_file_context(corpus_conn, kb_conn, file_id)

                    if not ctx.description or not ctx.description.strip():
                        upsert_retag_output(corpus_conn, file_id, "[]", None, "[]", config.text_model, "skipped")
                        skipped += 1
                        if (skipped + processed + errors) % 10 == 0:
                            corpus_conn.commit()
                        continue

                    prompt = _build_prompt(ctx, config.focus)

                    try:
                        raw_text = session.generate(system_prompt, prompt)
                        tags, refined, new_terms = _parse_llm_response(raw_text)
                        batch.append((
                            file_id,
                            json.dumps(tags),
                            refined or None,
                            json.dumps(new_terms),
                            config.text_model,
                            "done",
                        ))
                        processed += 1
                    except Exception as exc:
                        logger.warning("Retag: file_id=%d failed: %s", file_id, exc)
                        batch.append((file_id, "[]", None, "[]", config.text_model, "failed"))
                        errors += 1

                    if len(batch) >= 10:
                        _flush_batch()

                _flush_batch()

        except ModelLoadError as exc:
            logger.error("Retag: failed to load text model %s: %s", config.text_model, exc)
            update_pipeline_checkpoint(corpus_conn, "retag", 0, 0, 1)
            return

        if not cancel_event.is_set():
            update_pipeline_checkpoint(corpus_conn, "retag", processed, skipped, errors)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()
        kb_conn.close()
