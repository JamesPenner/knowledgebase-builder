"""Stage 5 — Retag: text-only LLM re-tags descriptions against vocabulary."""
import json
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

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


def _build_prompt(
    description: str,
    vocab_terms: list[str],
    derived_tags: list[str],
    focus: str,
) -> str:
    parts = []
    if focus:
        parts.append(f"DOMAIN FOCUS: {focus}")
    parts.append(f"VOCABULARY:\n{', '.join(vocab_terms) if vocab_terms else '(none)'}")
    if derived_tags:
        parts.append(f"CONFIRMED TAGS (already assigned):\n{', '.join(derived_tags)}")
    parts.append(f"DESCRIPTION:\n{description}")
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
) -> None:
    from src.db.corpus import (
        get_pending_retag_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_retag_output,
    )
    from src.db.kb import get_vocabulary_terms, open_kb

    if not config.text_model:
        logger.warning("Retag: no text_model configured — stage skipped")
        return

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        import llama_cpp  # noqa: F401 — lazy import; validates model is available
    except ImportError:
        logger.error("Retag: llama_cpp not installed — stage skipped")
        corpus_conn.close()
        kb_conn.close()
        return

    try:
        vocab_rows = get_vocabulary_terms(kb_conn)
        vocab_terms = [r["term"] for r in vocab_rows]

        pending = get_pending_retag_files(corpus_conn)
        total = len(pending)
        processed = skipped = errors = 0

        import llama_cpp as _llama

        try:
            llm = _llama.Llama(
                model_path=config.text_model,
                n_gpu_layers=config.text_gpu_layers,
                verbose=False,
            )
        except Exception as exc:
            logger.error("Retag: failed to load text model %s: %s", config.text_model, exc)
            update_pipeline_checkpoint(corpus_conn, "retag", 0, 0, 1)
            return

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

            desc_row = corpus_conn.execute(
                "SELECT description_normalized, description_raw FROM descriptions WHERE file_id=?",
                (file_id,),
            ).fetchone()

            if not desc_row:
                upsert_retag_output(corpus_conn, file_id, "[]", None, "[]", config.text_model, "skipped")
                skipped += 1
                if (skipped + processed + errors) % 10 == 0:
                    corpus_conn.commit()
                continue

            description = desc_row["description_normalized"] or desc_row["description_raw"] or ""
            if not description.strip():
                upsert_retag_output(corpus_conn, file_id, "[]", None, "[]", config.text_model, "skipped")
                skipped += 1
                if (skipped + processed + errors) % 10 == 0:
                    corpus_conn.commit()
                continue

            derived = [
                r["tag"]
                for r in corpus_conn.execute(
                    "SELECT tag FROM file_derived_tags WHERE file_id=?", (file_id,)
                ).fetchall()
            ]

            prompt = _build_prompt(description, vocab_terms, derived, config.focus)
            full_prompt = f"<s>[INST] <<SYS>>\n{_SYSTEM_PROMPT}\n<</SYS>>\n\n{prompt} [/INST]"

            try:
                output = llm(full_prompt, max_tokens=512, temperature=0.1, stop=["</s>"])
                raw_text = output["choices"][0]["text"].strip()
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

        if not cancel_event.is_set():
            update_pipeline_checkpoint(corpus_conn, "retag", processed, skipped, errors)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()
        kb_conn.close()
