"""Stage 3c — Summarize: LLM synthesis of describe + transcribe outputs into per-file summaries."""
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter
from src.text.context import FileContext

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v3"
_SUMMARIZE_BASE = (
    "You are a metadata summarization assistant. Write a factual, searchable summary "
    "of a media file. Respond with plain text only — no bullet points, no headings, "
    "no explanation outside the summary itself. "
    "Preserve all proper nouns (personal names, place names, event names) exactly as "
    "they appear in the source material — do not paraphrase, normalise, or correct them."
)


def _build_system_prompt(focus: str, base: str = _SUMMARIZE_BASE) -> str:
    if focus:
        return base + f"\nDOMAIN FOCUS: {focus}"
    return base


def _build_user_prompt(ctx: FileContext, target_words: int) -> str:
    context_lines = []
    if ctx.filename:
        context_lines.append(f"File: {ctx.filename}")
    if ctx.metadata_date:
        context_lines.append(f"Date: {ctx.metadata_date}")
    if ctx.metadata_location:
        context_lines.append(f"Location: {ctx.metadata_location}")
    if ctx.derived_tags:
        context_lines.append(f"Tags: {', '.join(ctx.derived_tags)}")
    if ctx.vocab_terms:
        context_lines.append(
            f"Relevant vocabulary (use where genuinely present): {', '.join(ctx.vocab_terms)}"
        )
    context_block = "\n".join(context_lines)

    description = ctx.description
    transcript = ctx.transcript
    transcript_label = "Attributed transcript" if ctx.transcript_attributed else "Transcript"

    if description and transcript:
        return (
            f"{context_block}\n\n"
            f"Visual description (inferred from video frames):\n{description}\n\n"
            f"{transcript_label} (authoritative record of spoken content):\n{transcript}\n\n"
            f"Write a {target_words}-word summary. Use the visual description for setting "
            f"and scene context. Use the transcript as the definitive record of what was "
            f"said. Combine them into a single coherent summary. "
            f"If the visual description and transcript describe clearly different scenes or "
            f"contradict each other, note the discrepancy explicitly rather than reconciling them."
        )
    elif description:
        return (
            f"{context_block}\n\n"
            f"Visual description:\n{description}\n\n"
            f"Write a {target_words}-word summary for use as searchable metadata. "
            f"Describe only what is directly visible; do not infer activity, narrative, "
            f"or context beyond what can be observed."
        )
    else:
        return (
            f"{context_block}\n\n"
            f"{transcript_label}:\n{transcript}\n\n"
            f"Write a {target_words}-word summary for use as searchable metadata."
        )


def _chunk_transcript(
    transcript: str, max_tokens: int, overlap_ratio: float = 0.1
) -> list[str]:
    words = transcript.split()
    if len(words) <= max_tokens:
        return [transcript]

    overlap = max(1, round(max_tokens * overlap_ratio))
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_tokens, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap
    return chunks


def _summarize_chunks(session, chunks: list[str], system: str) -> str:
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        user = (
            f"Transcript segment {i + 1} of {len(chunks)}:\n{chunk}\n\n"
            "Write a brief factual summary of this segment."
        )
        summary = session.generate(system, user, max_tokens=256, temperature=0.2)
        if summary:
            chunk_summaries.append(summary)

    if not chunk_summaries:
        return ""

    combined = "\n\n".join(f"Segment {i + 1}: {s}" for i, s in enumerate(chunk_summaries))
    user = (
        f"Segment summaries:\n{combined}\n\n"
        "Synthesise the above into a single coherent paragraph summary."
    )
    return session.generate(system, user, max_tokens=512, temperature=0.2)


def run_summarize(
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
        get_pending_summarize_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_file_summary,
    )
    from src.db.kb import load_stage_prompt, open_kb
    from src.llm.session import ModelLoadError, TextSession
    from src.text.context import build_file_context

    if not config.text_model:
        logger.warning("Summarize: no text_model configured — stage skipped")
        return

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        base_system = load_stage_prompt(kb_conn, "summarize", "system", default=_SUMMARIZE_BASE)
        system = _build_system_prompt(config.focus, base=base_system)

        try:
            with TextSession(
                config.text_model,
                n_gpu_layers=config.text_gpu_layers,
                n_ctx=config.summarize_max_transcript_tokens + 4096,
                max_retries=config.deep_seek_max_iter if config.deep_seek else 0,
            ) as session:
                pending = get_pending_summarize_files(corpus_conn, source_id=source_id, set_id=set_id)
                total = len(pending)
                processed = skipped = errors = 0

                for i, row in enumerate(pending):
                    if cancel_event.is_set():
                        break

                    file_id = row["id"]
                    progress.update(i, total, f"Summarize: {i + 1}/{total}")

                    ctx = build_file_context(corpus_conn, kb_conn, file_id)

                    if not ctx.description and not ctx.transcript:
                        upsert_file_summary(
                            corpus_conn, file_id, None, config.text_model, _PROMPT_VERSION, "skipped"
                        )
                        skipped += 1
                    else:
                        try:
                            if ctx.transcript:
                                word_count = len(ctx.transcript.split())
                                if word_count * 1.3 > config.summarize_max_transcript_tokens:
                                    chunks = _chunk_transcript(ctx.transcript, config.summarize_max_transcript_tokens)
                                    ctx.transcript = _summarize_chunks(session, chunks, system)

                            user = _build_user_prompt(ctx, config.summarize_target_words)
                            summary_text = session.generate(system, user, temperature=0.2)
                            status = "done" if summary_text else "failed"
                            upsert_file_summary(
                                corpus_conn,
                                file_id,
                                summary_text or None,
                                config.text_model,
                                _PROMPT_VERSION,
                                status,
                            )
                            if status == "done":
                                processed += 1
                            else:
                                errors += 1
                        except Exception as exc:
                            logger.warning("Summarize: file_id=%d failed: %s", file_id, exc)
                            upsert_file_summary(
                                corpus_conn, file_id, None, config.text_model, _PROMPT_VERSION, "failed"
                            )
                            errors += 1

                    if (processed + skipped + errors) % 10 == 0:
                        corpus_conn.commit()

                corpus_conn.commit()

        except ModelLoadError as exc:
            logger.error("Summarize: failed to load text model: %s", exc)
            return

        if not cancel_event.is_set():
            update_pipeline_checkpoint(corpus_conn, "summarize", processed, skipped, errors)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()
        kb_conn.close()
