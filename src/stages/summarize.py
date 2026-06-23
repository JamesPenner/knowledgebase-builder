"""Stage 3c — Summarize: LLM synthesis of describe + transcribe outputs into per-file summaries."""
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v3"


def _assemble_context(corpus_conn, kb_conn, file_id: int) -> dict:
    """Collect all available inputs for a file."""
    ctx: dict = {
        "description": None,
        "transcript": None,
        "attributed": False,
        "derived_tags": [],
        "entity_names": [],
        "normalized_filename": "",
        "captured_date": "",
        "captured_location": "",
        "vocab_terms": [],
    }

    desc_row = corpus_conn.execute(
        "SELECT description_normalized, description_raw FROM descriptions"
        " WHERE file_id=? AND pass1_status='done'",
        (file_id,),
    ).fetchone()
    if desc_row:
        ctx["description"] = desc_row["description_normalized"] or desc_row["description_raw"]

    seg_rows = corpus_conn.execute(
        "SELECT start_ms, speaker_label, text FROM transcript_segments"
        " WHERE file_id=? ORDER BY start_ms",
        (file_id,),
    ).fetchall()
    if seg_rows:
        has_speaker = any(r["speaker_label"] for r in seg_rows)
        if has_speaker:
            lines = []
            for r in seg_rows:
                label = r["speaker_label"] or "Speaker"
                lines.append(f"{label}: {r['text']}")
            ctx["transcript"] = "\n".join(lines)
            ctx["attributed"] = True
        else:
            ctx["transcript"] = " ".join(r["text"] for r in seg_rows)
    else:
        tr_row = corpus_conn.execute(
            "SELECT transcript_text FROM transcriptions"
            " WHERE file_id=? AND transcribe_status='done'",
            (file_id,),
        ).fetchone()
        if tr_row:
            ctx["transcript"] = tr_row["transcript_text"]

    tag_rows = corpus_conn.execute(
        "SELECT tag FROM file_derived_tags WHERE file_id=?", (file_id,)
    ).fetchall()
    ctx["derived_tags"] = [r["tag"] for r in tag_rows]

    entity_rows = corpus_conn.execute(
        "SELECT matched_value FROM file_entity_matches WHERE file_id=? AND stale=0",
        (file_id,),
    ).fetchall()
    ctx["entity_names"] = list({r["matched_value"] for r in entity_rows})

    file_row = corpus_conn.execute(
        "SELECT filename FROM files WHERE id=?", (file_id,)
    ).fetchone()
    if file_row:
        ctx["normalized_filename"] = file_row["filename"]

    date_row = corpus_conn.execute(
        "SELECT value FROM file_metadata_fields"
        " WHERE file_id=? AND canonical_name='captured_date' LIMIT 1",
        (file_id,),
    ).fetchone()
    if date_row:
        ctx["captured_date"] = date_row["value"]

    geo_row = corpus_conn.execute(
        "SELECT custom_region, state, country FROM file_geolabels WHERE file_id=? LIMIT 1",
        (file_id,),
    ).fetchone()
    if geo_row:
        parts = [p for p in (geo_row["custom_region"], geo_row["state"], geo_row["country"]) if p]
        ctx["captured_location"] = ", ".join(parts)

    if kb_conn is not None:
        vocab_rows = kb_conn.execute(
            "SELECT term FROM vocabulary WHERE source IN ('accepted', 'user') ORDER BY term"
        ).fetchall()
        ctx["vocab_terms"] = [r["term"] for r in vocab_rows]

    return ctx


def _build_system_prompt(focus: str) -> str:
    lines = [
        "You are a metadata summarization assistant. Write a factual, searchable summary "
        "of a media file. Respond with plain text only — no bullet points, no headings, "
        "no explanation outside the summary itself. "
        "Preserve all proper nouns (personal names, place names, event names) exactly as "
        "they appear in the source material — do not paraphrase, normalise, or correct them."
    ]
    if focus:
        lines.append(f"DOMAIN FOCUS: {focus}")
    return "\n".join(lines)


def _build_user_prompt(ctx: dict, target_words: int) -> str:
    context_lines = []
    if ctx.get("normalized_filename"):
        context_lines.append(f"File: {ctx['normalized_filename']}")
    if ctx.get("captured_date"):
        context_lines.append(f"Date: {ctx['captured_date']}")
    if ctx.get("captured_location"):
        context_lines.append(f"Location: {ctx['captured_location']}")
    if ctx.get("derived_tags"):
        context_lines.append(f"Tags: {', '.join(ctx['derived_tags'])}")
    if ctx.get("vocab_terms"):
        context_lines.append(
            f"Relevant vocabulary (use where genuinely present): {', '.join(ctx['vocab_terms'])}"
        )
    context_block = "\n".join(context_lines)

    description = ctx.get("description")
    transcript = ctx.get("transcript")
    attributed = ctx.get("attributed", False)
    transcript_label = "Attributed transcript" if attributed else "Transcript"

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


def _call_llm(llm, system: str, user: str, max_tokens: int = 512) -> str:
    try:
        output = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return output["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return ""


def _summarize_chunks(llm, chunks: list[str], focus: str) -> str:
    system = _build_system_prompt(focus)
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        user = (
            f"Transcript segment {i + 1} of {len(chunks)}:\n{chunk}\n\n"
            "Write a brief factual summary of this segment."
        )
        summary = _call_llm(llm, system, user, max_tokens=256)
        if summary:
            chunk_summaries.append(summary)

    if not chunk_summaries:
        return ""

    combined = "\n\n".join(f"Segment {i + 1}: {s}" for i, s in enumerate(chunk_summaries))
    user = (
        f"Segment summaries:\n{combined}\n\n"
        "Synthesise the above into a single coherent paragraph summary."
    )
    return _call_llm(llm, system, user, max_tokens=512)


def run_summarize(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    from src.db.corpus import (
        get_pending_summarize_files,
        open_corpus,
        update_pipeline_checkpoint,
        upsert_file_summary,
    )
    from src.db.kb import open_kb

    if not config.text_model:
        logger.warning("Summarize: no text_model configured — stage skipped")
        return

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            logger.error("Summarize: llama_cpp not installed — stage skipped")
            return

        from llama_cpp import Llama

        llm = Llama(
            model_path=config.text_model,
            n_gpu_layers=config.text_gpu_layers,
            n_ctx=config.summarize_max_transcript_tokens + 4096,
            verbose=False,
        )

        pending = get_pending_summarize_files(corpus_conn)
        total = len(pending)
        processed = skipped = errors = 0

        for i, row in enumerate(pending):
            if cancel_event.is_set():
                break

            file_id = row["id"]
            progress.update(i, total, f"Summarize: {i + 1}/{total}")

            ctx = _assemble_context(corpus_conn, kb_conn, file_id)

            if not ctx["description"] and not ctx["transcript"]:
                upsert_file_summary(
                    corpus_conn, file_id, None, config.text_model, _PROMPT_VERSION, "skipped"
                )
                skipped += 1
            else:
                transcript = ctx["transcript"]
                if transcript:
                    word_count = len(transcript.split())
                    if word_count * 1.3 > config.summarize_max_transcript_tokens:
                        chunks = _chunk_transcript(transcript, config.summarize_max_transcript_tokens)
                        ctx["transcript"] = _summarize_chunks(llm, chunks, config.focus)

                system = _build_system_prompt(config.focus)
                user = _build_user_prompt(ctx, config.summarize_target_words)
                summary_text = _call_llm(llm, system, user)
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

            if (processed + skipped + errors) % 10 == 0:
                corpus_conn.commit()

        corpus_conn.commit()

        if not cancel_event.is_set():
            update_pipeline_checkpoint(corpus_conn, "summarize", processed, skipped, errors)
            corpus_conn.commit()
            progress.done()

    finally:
        corpus_conn.close()
        kb_conn.close()
