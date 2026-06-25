import logging
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

_MIN_DURATION_S = 1.0
_EMBEDDING_DIM = 256


class ModelLoadError(Exception):
    pass


def embed_voice(wav_path: Path, model_name: str = "resemblyzer") -> tuple[bytes, int] | tuple[None, None]:
    """Compute a 256D d-vector speaker embedding from a 16 kHz mono WAV file.

    Returns (embedding_bytes, duration_ms) or (None, None) if the audio is too
    short or cannot be read.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise ModelLoadError(f"numpy not installed: {exc}") from exc
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
    except ImportError as exc:
        raise ModelLoadError(f"resemblyzer not installed: {exc}") from exc

    try:
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:
        logger.debug("Could not load audio from %s: %s", wav_path, exc)
        return None, None

    duration_s = len(audio) / sr
    if duration_s < _MIN_DURATION_S:
        return None, None

    duration_ms = int(duration_s * 1000)
    wav = preprocess_wav(audio, source_sr=sr)
    encoder = VoiceEncoder()
    embedding = encoder.embed_utterance(wav)  # shape (256,)

    norm = float(np.linalg.norm(embedding))
    if norm > 0:
        embedding = embedding / norm

    return embedding.astype(np.float32).tobytes(), duration_ms


def cosine_similarity_voice(a: bytes, b: bytes) -> float:
    """Cosine similarity between two 256D float32 voice embeddings stored as bytes."""
    import numpy as np
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def update_voice_centroid(
    old_blob: bytes | None,
    old_count: int,
    new_embedding: bytes,
) -> tuple[bytes, int]:
    """Incremental running mean centroid update (same algorithm as face stage).

    Returns (new_centroid_blob, new_count).
    """
    import numpy as np
    new_vec = np.frombuffer(new_embedding, dtype=np.float32).copy()
    if old_blob is None or old_count == 0:
        new_count = 1
        centroid = new_vec
    else:
        old_vec = np.frombuffer(old_blob, dtype=np.float32).copy()
        new_count = old_count + 1
        centroid = (old_vec * old_count + new_vec) / new_count
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    return centroid.astype(np.float32).tobytes(), new_count


def run_voice(corpus_path, kb_path, config, progress, cancel) -> dict:
    """Embed speaker voice from pending audio/video files and match to known people."""
    from src.db.corpus import (
        get_files_without_voice_embedding,
        get_has_speech,
        open_corpus,
        set_has_speech,
        upsert_voice_embedding,
    )
    from src.db.kb import (
        get_people_with_voice_centroids,
        open_kb,
        update_voice_centroid as _db_update_voice_centroid,
    )
    from src.media.audiotrack import prepare_audio

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    files_processed = 0
    files_matched = 0
    files_skipped = 0
    error_count = 0

    try:
        people_rows = get_people_with_voice_centroids(kb_conn)
        centroids: dict[int, dict] = {
            row["id"]: {
                "blob": bytes(row["voice_centroid"]),
                "count": row["voice_samples"],
            }
            for row in people_rows
        }

        pending = get_files_without_voice_embedding(corpus_conn)
        total = len(pending)
        progress.update(0, total, "Embedding voices…")

        for i, row in enumerate(pending):
            if cancel.is_set():
                break

            file_path = Path(row["path"])
            file_id = row["id"]

            # Skip files already known to be silent
            if get_has_speech(corpus_conn, file_id) is False:
                files_skipped += 1
                progress.update(i + 1, total)
                continue

            try:
                with prepare_audio(file_path, config) as track:
                    if track is None:
                        files_skipped += 1
                        progress.update(i + 1, total)
                        continue

                    if track.has_speech is not None:
                        set_has_speech(corpus_conn, file_id, track.has_speech)
                        corpus_conn.commit()

                    if track.has_clipping:
                        logger.warning("voice: clipping detected in %s", file_path)

                    if track.has_speech is False:
                        files_skipped += 1
                        progress.update(i + 1, total)
                        continue

                    try:
                        embedding_bytes, duration_ms = embed_voice(track.wav_path, config.voice_model)
                    except ModelLoadError:
                        raise
                    except Exception as exc:
                        logger.warning("Voice embedding failed for %s: %s", file_path, exc)
                        error_count += 1
                        progress.update(i + 1, total)
                        continue

                    if embedding_bytes is None:
                        files_skipped += 1
                        progress.update(i + 1, total)
                        continue

                    upsert_voice_embedding(corpus_conn, file_id, embedding_bytes, config.voice_model, duration_ms)

                    # Match against known people centroids
                    best_person_id = None
                    best_similarity = 0.0
                    for person_id, cent in centroids.items():
                        sim = cosine_similarity_voice(embedding_bytes, cent["blob"])
                        if sim > best_similarity:
                            best_similarity = sim
                            best_person_id = person_id

                    if best_person_id is not None and best_similarity >= config.voice_similarity_threshold:
                        files_matched += 1
                        old = centroids[best_person_id]
                        new_blob, new_count = update_voice_centroid(old["blob"], old["count"], embedding_bytes)
                        centroids[best_person_id] = {"blob": new_blob, "count": new_count}
                        _db_update_voice_centroid(kb_conn, best_person_id, new_blob, new_count)

                    corpus_conn.commit()
                    kb_conn.commit()
                    files_processed += 1

            except ModelLoadError:
                raise
            except Exception as exc:
                logger.warning("Voice: error processing %s: %s", file_path, exc)
                error_count += 1

            progress.update(i + 1, total)

        progress.done()
    finally:
        corpus_conn.close()
        kb_conn.close()

    return {
        "files_processed": files_processed,
        "files_matched": files_matched,
        "files_skipped": files_skipped,
        "errors": error_count,
    }


# ---------------------------------------------------------------------------
# Diarization functions (KB.P17)
# ---------------------------------------------------------------------------

def diarize_audio(wav_path: Path, config) -> list[dict]:
    """Run pyannote speaker diarization on a pre-extracted WAV file.

    Returns list of {start_ms, end_ms, speaker_label} dicts, filtered to
    segments >= config.voice_diarization_min_segment_ms. Returns [] on error.
    Raises ModelLoadError if pyannote.audio is not importable.
    """
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise ModelLoadError(f"pyannote.audio not installed: {exc}") from exc

    try:
        pipeline = Pipeline.from_pretrained(config.diarization_model)
        diarization = pipeline(str(wav_path))
    except Exception as exc:
        logger.warning("Diarization failed for %s: %s", wav_path, exc)
        return []

    min_ms = config.voice_diarization_min_segment_ms
    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        start_ms = int(turn.start * 1000)
        end_ms = int(turn.end * 1000)
        if end_ms - start_ms < min_ms:
            continue
        segments.append({"start_ms": start_ms, "end_ms": end_ms, "speaker_label": speaker})

    return sorted(segments, key=lambda s: s["start_ms"])


def embed_voice_segment(
    wav_path: Path,
    start_ms: int,
    end_ms: int,
    model_name: str = "resemblyzer",
) -> bytes | None:
    """Compute a 256D d-vector for a specific time slice of a 16 kHz mono WAV file.

    Returns None if the slice is too short or contains no audio.
    """
    duration_s = (end_ms - start_ms) / 1000.0
    if duration_s <= 0:
        return None

    try:
        import numpy as np
    except ImportError as exc:
        raise ModelLoadError(f"numpy not installed: {exc}") from exc
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
    except ImportError as exc:
        raise ModelLoadError(f"resemblyzer not installed: {exc}") from exc

    try:
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            start_frame = int(start_ms / 1000.0 * sr)
            n_frames = int(duration_s * sr)
            wf.setpos(start_frame)
            raw = wf.readframes(n_frames)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:
        logger.debug("Could not load audio slice from %s [%d-%d]: %s", wav_path, start_ms, end_ms, exc)
        return None

    if len(audio) / sr < _MIN_DURATION_S:
        return None

    wav = preprocess_wav(audio, source_sr=sr)
    encoder = VoiceEncoder()
    embedding = encoder.embed_utterance(wav)

    norm = float(np.linalg.norm(embedding))
    if norm > 0:
        embedding = embedding / norm

    return embedding.astype(np.float32).tobytes()


def run_voice_diarize(corpus_path, kb_path, config, progress, cancel) -> dict:
    """Diarize audio/video files and match speaker segments to known people."""
    from src.db.corpus import (
        get_files_without_voice_segments,
        get_has_speech,
        get_voice_speaker_clusters,
        open_corpus,
        set_has_speech,
        upsert_voice_segment,
        upsert_voice_speaker_cluster,
    )
    from src.db.kb import (
        get_people_with_voice_centroids,
        open_kb,
        update_voice_centroid as _db_update_voice_centroid,
    )
    from src.media.audiotrack import prepare_audio

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    files_processed = 0
    segments_found = 0
    segments_matched = 0
    error_count = 0

    try:
        people_rows = get_people_with_voice_centroids(kb_conn)
        centroids: dict[int, dict] = {
            row["id"]: {
                "blob": bytes(row["voice_centroid"]),
                "count": row["voice_samples"],
            }
            for row in people_rows
        }

        cluster_rows = get_voice_speaker_clusters(corpus_conn)
        cluster_centroids: list[dict] = [
            {"id": row["id"], "blob": bytes(row["centroid"]), "count": row["member_count"]}
            for row in cluster_rows
        ]

        pending = get_files_without_voice_segments(corpus_conn)
        total = len(pending)
        progress.update(0, total, "Diarizing audio…")

        for i, row in enumerate(pending):
            if cancel.is_set():
                break

            file_path = Path(row["path"])
            file_id = row["id"]

            # Skip files already known to be silent
            if get_has_speech(corpus_conn, file_id) is False:
                progress.update(i + 1, total)
                continue

            try:
                with prepare_audio(file_path, config) as track:
                    if track is None:
                        progress.update(i + 1, total)
                        continue

                    if track.has_speech is not None:
                        set_has_speech(corpus_conn, file_id, track.has_speech)
                        corpus_conn.commit()

                    if track.has_clipping:
                        logger.warning("diarize: clipping detected in %s", file_path)

                    if track.has_speech is False:
                        progress.update(i + 1, total)
                        continue

                    try:
                        segments = diarize_audio(track.wav_path, config)
                    except ModelLoadError:
                        raise
                    except Exception as exc:
                        logger.warning("Diarization error for %s: %s", file_path, exc)
                        error_count += 1
                        progress.update(i + 1, total)
                        continue

                    for seg_idx, seg in enumerate(segments):
                        segments_found += 1
                        start_ms = seg["start_ms"]
                        end_ms = seg["end_ms"]
                        speaker_label = seg["speaker_label"]

                        try:
                            embedding = embed_voice_segment(
                                track.wav_path, start_ms, end_ms, config.voice_model
                            )
                        except ModelLoadError:
                            raise
                        except Exception as exc:
                            logger.warning(
                                "Segment embedding failed %s [%d-%d]: %s",
                                file_path, start_ms, end_ms, exc,
                            )
                            embedding = None

                        matched_person_id = None
                        matched_cluster_id = None
                        matched_similarity = None

                        if embedding is not None:
                            best_person_id = None
                            best_sim = 0.0
                            for person_id, cent in centroids.items():
                                sim = cosine_similarity_voice(embedding, cent["blob"])
                                if sim > best_sim:
                                    best_sim = sim
                                    best_person_id = person_id

                            if best_person_id is not None and best_sim >= config.voice_similarity_threshold:
                                matched_person_id = best_person_id
                                matched_similarity = best_sim
                                segments_matched += 1
                                old = centroids[best_person_id]
                                new_blob, new_count = update_voice_centroid(
                                    old["blob"], old["count"], embedding
                                )
                                centroids[best_person_id] = {"blob": new_blob, "count": new_count}
                                _db_update_voice_centroid(kb_conn, best_person_id, new_blob, new_count)
                            else:
                                best_ci = None
                                best_cluster_sim = 0.0
                                for ci, cl in enumerate(cluster_centroids):
                                    sim = cosine_similarity_voice(embedding, cl["blob"])
                                    if sim > best_cluster_sim:
                                        best_cluster_sim = sim
                                        best_ci = ci

                                if best_ci is None or best_cluster_sim < config.voice_similarity_threshold:
                                    cid = upsert_voice_speaker_cluster(corpus_conn, None, embedding, 1, 0.0)
                                    cluster_centroids.append({"id": cid, "blob": embedding, "count": 1})
                                    matched_cluster_id = cid
                                else:
                                    cl = cluster_centroids[best_ci]
                                    new_blob, new_count = update_voice_centroid(
                                        cl["blob"], cl["count"], embedding
                                    )
                                    spread = 1.0 - best_cluster_sim
                                    cid = upsert_voice_speaker_cluster(
                                        corpus_conn, cl["id"], new_blob, new_count, spread
                                    )
                                    cluster_centroids[best_ci] = {
                                        "id": cid, "blob": new_blob, "count": new_count
                                    }
                                    matched_cluster_id = cid
                                    matched_similarity = best_cluster_sim

                        upsert_voice_segment(
                            corpus_conn, file_id, seg_idx, start_ms, end_ms, speaker_label,
                            embedding, matched_cluster_id, matched_person_id, matched_similarity,
                        )

                    corpus_conn.commit()
                    kb_conn.commit()
                    files_processed += 1

            except ModelLoadError:
                raise
            except Exception as exc:
                logger.warning("Diarize: error processing %s: %s", file_path, exc)
                error_count += 1

            progress.update(i + 1, total)

        progress.done()
    finally:
        corpus_conn.close()
        kb_conn.close()

    return {
        "files_processed": files_processed,
        "segments_found": segments_found,
        "segments_matched": segments_matched,
        "errors": error_count,
    }
