"""Stage: attribute speaker labels to transcript segments via time-overlap matching."""
import logging
import threading
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)


def _resolve_label(
    vs_row,
    people_map: dict[int, str],
    cluster_map: dict[int, str],
) -> str:
    """Priority: confirmed person name > cluster label > raw pyannote label."""
    person_id = vs_row["person_id"]
    if person_id is not None and person_id in people_map:
        return people_map[person_id]
    cluster_id = vs_row["cluster_id"]
    if cluster_id is not None and cluster_id in cluster_map:
        label = cluster_map[cluster_id]
        if label:
            return label
    return vs_row["speaker_label"]


def _best_overlap(ts_start, ts_end, voice_segments) -> object | None:
    """Return the voice segment row with the greatest ms overlap; None if no overlap."""
    if ts_start is None or ts_end is None:
        return None
    best_row = None
    best_overlap = 0
    for vs in voice_segments:
        overlap = max(0, min(ts_end, vs["end_ms"]) - max(ts_start, vs["start_ms"]))
        if overlap > best_overlap:
            best_overlap = overlap
            best_row = vs
    return best_row


def run_attribute_speakers(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> dict:
    """Attribute speaker labels to transcript segments via time-overlap matching."""
    import time as _time
    from src.db.corpus import (
        get_files_pending_speaker_attribution,
        get_voice_segments_for_file,
        get_voice_speaker_clusters,
        open_corpus,
        update_pipeline_checkpoint,
        set_transcript_segment_speaker,
    )
    from src.db.kb import get_all_people, open_kb
    from src.pipeline.knowledge_gates import get_enabled_categories, report_stage_skipped, stage_is_enabled

    kb_conn = open_kb(kb_path)
    enabled_categories = get_enabled_categories(kb_conn)
    if not stage_is_enabled("attribute_speakers", enabled_categories):
        result = report_stage_skipped(progress, "attribute_speakers", enabled_categories)
        kb_conn.close()
        return result

    corpus_conn = open_corpus(corpus_path)

    files_processed = 0
    segments_attributed = 0
    segments_skipped = 0
    error_count = 0
    _start = _time.monotonic()

    try:
        people_map: dict[int, str] = {
            r["id"]: r["preferred_name"] for r in get_all_people(kb_conn)
        }
        cluster_map: dict[int, str] = {
            r["id"]: (r["label"] or "") for r in get_voice_speaker_clusters(corpus_conn)
        }

        pending = get_files_pending_speaker_attribution(corpus_conn)
        total = len(pending)
        progress.update(0, total, "Attributing speakers…")

        for i, file_row in enumerate(pending):
            if cancel_event.is_set():
                break

            file_id = file_row["id"]
            try:
                voice_segs = get_voice_segments_for_file(corpus_conn, file_id)
                ts_rows = corpus_conn.execute(
                    "SELECT id, start_ms, end_ms FROM transcript_segments "
                    "WHERE file_id = ? AND speaker_label IS NULL",
                    (file_id,),
                ).fetchall()

                for ts in ts_rows:
                    best = _best_overlap(ts["start_ms"], ts["end_ms"], voice_segs)
                    if best is None:
                        segments_skipped += 1
                        continue
                    label = _resolve_label(best, people_map, cluster_map)
                    set_transcript_segment_speaker(corpus_conn, ts["id"], label)
                    segments_attributed += 1

                corpus_conn.commit()
                files_processed += 1
            except Exception:
                logger.exception("Error attributing speakers for file_id=%d", file_id)
                error_count += 1

            progress.update(i + 1, total, "Attributing speakers…")

        update_pipeline_checkpoint(
            corpus_conn, "attribute_speakers", files_processed, segments_skipped,
            error_count, _time.monotonic() - _start,
        )
        corpus_conn.commit()
        progress.done()
    finally:
        corpus_conn.close()
        kb_conn.close()

    return {
        "files_processed": files_processed,
        "segments_attributed": segments_attributed,
        "segments_skipped": segments_skipped,
        "errors": error_count,
    }
