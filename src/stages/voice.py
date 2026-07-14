import logging
import threading
import wave
import warnings
from pathlib import Path

from src.config import Config
from src.llm.session import ModelLoadError  # noqa: F401 — re-exported for callers
from src.pipeline.embeddings import cosine_similarity, update_centroid
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_MIN_DURATION_S = 1.5  # resemblyzer's GE2E training window
_EMBEDDING_DIM = 256


def _build_voice_encoder():
    """Construct a resemblyzer VoiceEncoder for reuse across a run's files.

    Raises ModelLoadError if resemblyzer is not installed.
    """
    try:
        from resemblyzer import VoiceEncoder
    except ImportError as exc:
        raise ModelLoadError(f"resemblyzer not installed: {exc}") from exc
    return VoiceEncoder(verbose=False)


def embed_voice(
    wav_path: Path,
    model_name: str = "resemblyzer",
    encoder=None,
) -> tuple[bytes, int] | tuple[None, None]:
    """Compute a 256D d-vector speaker embedding from a 16 kHz mono WAV file.

    Returns (embedding_bytes, duration_ms) or (None, None) if the audio is too
    short or cannot be read. `encoder`, if provided, is reused instead of
    constructing a new VoiceEncoder (KB.AN2 — avoids reloading per file).
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
    if encoder is None:
        encoder = VoiceEncoder(verbose=False)
    embedding = encoder.embed_utterance(wav)  # shape (256,)

    norm = float(np.linalg.norm(embedding))
    if norm > 0:
        embedding = embedding / norm

    return embedding.astype(np.float32).tobytes(), duration_ms


def run_voice(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> dict:
    """Embed speaker voice from pending audio/video files and match to known people."""
    import time as _time
    from src.db.corpus import (
        get_files_without_voice_embedding,
        get_has_speech,
        open_corpus,
        set_has_speech,
        set_voice_checked,
        update_pipeline_checkpoint,
        upsert_voice_embedding,
    )
    from src.db.kb import (
        get_people_with_centroids,
        open_kb,
        update_person_centroid as _db_update_person_centroid,
    )
    from src.media.audiotrack import prepare_audio
    from src.pipeline.knowledge_gates import get_enabled_categories, report_stage_skipped, stage_is_enabled

    kb_conn = open_kb(kb_path)
    enabled_categories = get_enabled_categories(kb_conn)
    if not stage_is_enabled("voice", enabled_categories):
        result = report_stage_skipped(progress, "voice", enabled_categories)
        kb_conn.close()
        return result

    corpus_conn = open_corpus(corpus_path)

    files_processed = 0
    files_matched = 0
    files_skipped = 0
    error_count = 0
    _start = _time.monotonic()

    try:
        people_rows = get_people_with_centroids(kb_conn, "voice")
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

        encoder = None  # built lazily on first use, then reused for the whole run (KB.AN2)

        for i, row in enumerate(pending):
            if cancel_event.is_set():
                break

            file_path = Path(row["path"])
            file_id = row["id"]

            # Skip files already known to be silent
            if get_has_speech(corpus_conn, file_id) is False:
                files_skipped += 1
                set_voice_checked(corpus_conn, file_id)
                corpus_conn.commit()
                progress.update(i + 1, total)
                continue

            try:
                with prepare_audio(file_path, config) as track:
                    if track is None:
                        files_skipped += 1
                        set_voice_checked(corpus_conn, file_id)
                        corpus_conn.commit()
                        progress.update(i + 1, total)
                        continue

                    if track.has_speech is not None:
                        set_has_speech(corpus_conn, file_id, track.has_speech)
                        corpus_conn.commit()

                    if track.has_clipping:
                        logger.warning("voice: clipping detected in %s", file_path)

                    if track.has_speech is False:
                        files_skipped += 1
                        set_voice_checked(corpus_conn, file_id)
                        corpus_conn.commit()
                        progress.update(i + 1, total)
                        continue

                    try:
                        if encoder is None:
                            encoder = _build_voice_encoder()
                        embedding_bytes, duration_ms = embed_voice(track.wav_path, config.voice_model, encoder=encoder)
                    except ModelLoadError:
                        raise
                    except Exception as exc:
                        logger.warning("Voice embedding failed for %s: %s", file_path, exc)
                        error_count += 1
                        set_voice_checked(corpus_conn, file_id)
                        corpus_conn.commit()
                        progress.update(i + 1, total)
                        continue

                    if embedding_bytes is None:
                        files_skipped += 1
                        set_voice_checked(corpus_conn, file_id)
                        corpus_conn.commit()
                        progress.update(i + 1, total)
                        continue

                    upsert_voice_embedding(corpus_conn, file_id, embedding_bytes, config.voice_model, duration_ms)

                    # Match against known people centroids
                    best_person_id = None
                    best_similarity = 0.0
                    for person_id, cent in centroids.items():
                        sim = cosine_similarity(embedding_bytes, cent["blob"])
                        if sim > best_similarity:
                            best_similarity = sim
                            best_person_id = person_id

                    if best_person_id is not None and best_similarity >= config.voice_similarity_threshold:
                        files_matched += 1
                        old = centroids[best_person_id]
                        new_blob, new_count = update_centroid(old["blob"], old["count"], embedding_bytes)
                        centroids[best_person_id] = {"blob": new_blob, "count": new_count}
                        _db_update_person_centroid(kb_conn, best_person_id, new_blob, new_count, kind="voice")

                    set_voice_checked(corpus_conn, file_id)
                    corpus_conn.commit()
                    kb_conn.commit()
                    files_processed += 1

            except ModelLoadError:
                raise
            except Exception as exc:
                logger.warning("Voice: error processing %s: %s", file_path, exc)
                error_count += 1
                set_voice_checked(corpus_conn, file_id)
                corpus_conn.commit()

            progress.update(i + 1, total)

        update_pipeline_checkpoint(
            corpus_conn, "voice", files_processed, files_skipped,
            error_count, _time.monotonic() - _start,
        )
        corpus_conn.commit()
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

def _load_diarization_pipeline(config):
    """Load a pyannote diarization Pipeline for reuse across a run's files.

    Raises ModelLoadError if pyannote.audio is not installed or the model
    fails to load (auth, network, or missing local cache) — loading now
    happens once per run (KB.AN2) rather than being silently retried per
    file, so a load failure surfaces clearly instead of degrading every
    file to zero segments.
    """
    try:
        with warnings.catch_warnings():
            # Confirmed-harmless: torchaudio's torchcodec backend probe, moot
            # here since we always pass a preloaded waveform dict rather than
            # a file path (KB.AN2 Criterion E). Scoped narrowly by message so
            # genuinely new warnings from this import still surface.
            warnings.filterwarnings("ignore", message=r"(?i).*torchcodec.*", category=UserWarning)
            from pyannote.audio import Pipeline
    except ImportError as exc:
        raise ModelLoadError(f"pyannote.audio not installed: {exc}") from exc

    import os
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or None
    # When no token is set, use the local cache; avoids auth failures for already-downloaded models.
    prev_offline = os.environ.get("HF_HUB_OFFLINE")
    if not hf_token:
        os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        pipeline = Pipeline.from_pretrained(config.diarization_model, token=hf_token)
    except Exception as exc:
        raise ModelLoadError(f"Could not load diarization model {config.diarization_model}: {exc}") from exc
    finally:
        if not hf_token:
            if prev_offline is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = prev_offline

    try:
        import torch_directml as _dml
        _device = _dml.device()
        pipeline.to(_device)
        logger.info("diarize_audio: using DirectML GPU (%s)", _device)
    except (ImportError, Exception) as _dml_exc:
        logger.info("diarize_audio: running on CPU (%s)", _dml_exc)

    return pipeline


def diarize_audio(wav_path: Path, config, pipeline=None) -> list[dict]:
    """Run pyannote speaker diarization on a pre-extracted WAV file.

    Returns list of {start_ms, end_ms, speaker_label} dicts, filtered to
    segments >= config.voice_diarization_min_segment_ms. Returns [] if the
    pipeline errors while processing this file. Raises ModelLoadError if
    pyannote.audio is not importable, or if loading a pipeline fails (only
    reached when `pipeline` is not provided). `pipeline`, if provided, is
    reused instead of loading a fresh one (KB.AN2 — was loaded fresh per
    file, the dominant per-file cost).
    """
    if pipeline is None:
        pipeline = _load_diarization_pipeline(config)

    try:
        import numpy as np
        import torch
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        waveform = torch.tensor(samples).unsqueeze(0)
        audio_input = {"waveform": waveform, "sample_rate": sr}

        import time as _t
        _dur_s = audio_input["waveform"].shape[-1] / audio_input["sample_rate"]
        logger.info("diarize_audio: running pipeline on %.1fs of audio (%s)", _dur_s, wav_path.name)
        _t0 = _t.monotonic()
        result = pipeline(audio_input)
        logger.info("diarize_audio: pipeline finished in %.1fs", _t.monotonic() - _t0)
        # pyannote 4.x returns DiarizeOutput(speaker_diarization=Annotation, …)
        # older versions returned the Annotation directly
        diarization = result.speaker_diarization if hasattr(result, "speaker_diarization") else result
    except Exception as exc:
        logger.warning("Diarization failed for %s: %s", wav_path, exc, exc_info=True)
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


def _read_wav_slice(wav_path: Path, start_ms: int, end_ms: int):
    """Read raw float32 samples in [-1, 1] for a time slice of a 16 kHz mono WAV file.

    Returns (samples, sample_rate), or (None, None) if the slice is empty or the
    file cannot be read.
    """
    duration_s = (end_ms - start_ms) / 1000.0
    if duration_s <= 0:
        return None, None
    try:
        import numpy as np
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            start_frame = int(start_ms / 1000.0 * sr)
            n_frames = int(duration_s * sr)
            wf.setpos(start_frame)
            raw = wf.readframes(n_frames)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:
        logger.debug("Could not load audio slice from %s [%d-%d]: %s", wav_path, start_ms, end_ms, exc)
        return None, None
    return audio, sr


def embed_pooled_voice_segments(
    wav_path: Path,
    spans: list[tuple[int, int]],
    model_name: str = "resemblyzer",
    encoder=None,
) -> tuple[bytes, int] | tuple[None, None]:
    """Compute a single 256D d-vector from audio concatenated across multiple time spans.

    Pools all turns sharing one local diarization speaker label into a single
    resemblyzer call, instead of embedding each turn independently. Returns
    (embedding_bytes, pooled_duration_ms) or (None, None) if the pooled audio
    is empty or shorter than `_MIN_DURATION_S`. The duration is reported so
    callers can apply duration-weighted match confidence (KB.AN2 Criterion D)
    — a pool close to the floor is noisier than several seconds of clean
    speech. `encoder`, if provided, is reused instead of constructing a new
    VoiceEncoder (KB.AN2 — avoids reloading per label).
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise ModelLoadError(f"numpy not installed: {exc}") from exc

    chunks = []
    sr = None
    for start_ms, end_ms in spans:
        audio, chunk_sr = _read_wav_slice(wav_path, start_ms, end_ms)
        if audio is None:
            continue
        sr = chunk_sr
        chunks.append(audio)

    if not chunks:
        return None, None

    pooled = np.concatenate(chunks)
    duration_s = len(pooled) / sr
    if duration_s < _MIN_DURATION_S:
        return None, None

    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
    except ImportError as exc:
        raise ModelLoadError(f"resemblyzer not installed: {exc}") from exc

    wav = preprocess_wav(pooled, source_sr=sr)
    if encoder is None:
        encoder = VoiceEncoder(verbose=False)
    embedding = encoder.embed_utterance(wav)

    norm = float(np.linalg.norm(embedding))
    if norm > 0:
        embedding = embedding / norm

    return embedding.astype(np.float32).tobytes(), int(duration_s * 1000)


def _find_overlapping_indices(segments: list[dict]) -> set[int]:
    """Indices of turns that overlap in time with a turn from a *different*
    speaker label — cross-talk, possible with the powerset segmentation used
    by `pyannote/speaker-diarization-3.1`. Turns from the *same* label are
    not flagged even if adjacent/overlapping — pooling a speaker's own audio
    with itself doesn't corrupt the identity embedding the way cross-talk does.
    """
    overlapping: set[int] = set()
    for i in range(len(segments)):
        a = segments[i]
        for j in range(i + 1, len(segments)):
            b = segments[j]
            if a["speaker_label"] == b["speaker_label"]:
                continue
            if a["start_ms"] < b["end_ms"] and b["start_ms"] < a["end_ms"]:
                overlapping.add(i)
                overlapping.add(j)
    return overlapping


_DURATION_WEIGHT_FLOOR_S = _MIN_DURATION_S  # shortest possible pooled duration — maximum penalty
_DURATION_WEIGHT_CEILING_S = 5.0            # duration at/beyond which no penalty applies
_DURATION_WEIGHT_MAX_PENALTY = 0.10         # added to the base threshold at the floor duration


def _duration_weighted_threshold(pooled_duration_s: float, base_threshold: float) -> float:
    """Raise the similarity bar for a match built from less pooled audio.

    Short pooled audio produces noisier resemblyzer embeddings, so a match
    from a bare-minimum-duration pool must clear a higher bar than one built
    from several seconds of clean speech. Linear ramp from
    `base_threshold + _DURATION_WEIGHT_MAX_PENALTY` at `_DURATION_WEIGHT_FLOOR_S`
    down to `base_threshold` at `_DURATION_WEIGHT_CEILING_S` and beyond
    (KB.AN2 Criterion D).
    """
    if pooled_duration_s >= _DURATION_WEIGHT_CEILING_S:
        return base_threshold
    span = _DURATION_WEIGHT_CEILING_S - _DURATION_WEIGHT_FLOOR_S
    frac = max(0.0, pooled_duration_s - _DURATION_WEIGHT_FLOOR_S) / span
    penalty = _DURATION_WEIGHT_MAX_PENALTY * (1.0 - frac)
    return min(base_threshold + penalty, 1.0)


def run_voice_diarize(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> dict:
    """Diarize audio/video files and match speaker segments to known people."""
    import time as _time
    from src.db.corpus import (
        get_files_without_voice_segments,
        get_has_speech,
        get_voice_speaker_clusters,
        open_corpus,
        set_has_speech,
        set_voice_diarize_checked,
        update_pipeline_checkpoint,
        upsert_voice_segment,
        upsert_voice_speaker_cluster,
    )
    from src.db.kb import (
        get_people_with_centroids,
        open_kb,
        update_person_centroid as _db_update_person_centroid,
    )
    from src.media.audiotrack import prepare_audio
    from src.pipeline.knowledge_gates import get_enabled_categories, report_stage_skipped, stage_is_enabled

    kb_conn = open_kb(kb_path)
    enabled_categories = get_enabled_categories(kb_conn)
    if not stage_is_enabled("voice_diarize", enabled_categories):
        result = report_stage_skipped(progress, "voice_diarize", enabled_categories)
        kb_conn.close()
        return result

    corpus_conn = open_corpus(corpus_path)

    files_processed = 0
    segments_found = 0
    segments_matched = 0
    error_count = 0
    _start = _time.monotonic()

    try:
        people_rows = get_people_with_centroids(kb_conn, "voice")
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

        # Built lazily on first use, then reused for the whole run (KB.AN2) —
        # avoids a model load when there is no pending work.
        pipeline = None
        encoder = None

        for i, row in enumerate(pending):
            if cancel_event.is_set():
                break

            file_path = Path(row["path"])
            file_id = row["id"]

            # Skip files already known to be silent
            if get_has_speech(corpus_conn, file_id) is False:
                set_voice_diarize_checked(corpus_conn, file_id)
                corpus_conn.commit()
                progress.update(i + 1, total)
                continue

            try:
                with prepare_audio(file_path, config) as track:
                    if track is None:
                        set_voice_diarize_checked(corpus_conn, file_id)
                        corpus_conn.commit()
                        progress.update(i + 1, total)
                        continue

                    if track.has_speech is not None:
                        set_has_speech(corpus_conn, file_id, track.has_speech)
                        corpus_conn.commit()

                    if track.has_clipping:
                        logger.warning("diarize: clipping detected in %s", file_path)

                    if track.has_speech is False:
                        set_voice_diarize_checked(corpus_conn, file_id)
                        corpus_conn.commit()
                        progress.update(i + 1, total)
                        continue

                    try:
                        if pipeline is None:
                            pipeline = _load_diarization_pipeline(config)
                        segments = diarize_audio(track.wav_path, config, pipeline=pipeline)
                    except ModelLoadError:
                        raise
                    except Exception as exc:
                        logger.warning("Diarization error for %s: %s", file_path, exc)
                        error_count += 1
                        set_voice_diarize_checked(corpus_conn, file_id)
                        corpus_conn.commit()
                        progress.update(i + 1, total)
                        continue

                    # Pool turns sharing one local diarization speaker label into a
                    # single resemblyzer call, then propagate the match to every
                    # turn under that label (KB.AN2 — was one call per turn).
                    label_indices: dict[str, list[int]] = {}
                    for idx, seg in enumerate(segments):
                        label_indices.setdefault(seg["speaker_label"], []).append(idx)

                    # Cross-talk turns are excluded from the pooled identity-matching
                    # audio (mixed voices would corrupt the embedding) but still get
                    # their own file_voice_segments row further below, so transcript
                    # speaker-label attribution — which uses the raw label, not the
                    # embedding match — is unaffected (KB.AN2 Criterion C).
                    overlapping_indices = _find_overlapping_indices(segments)

                    label_matches: dict[str, dict] = {}
                    for speaker_label, indices in label_indices.items():
                        pooling_indices = [j for j in indices if j not in overlapping_indices]
                        spans = [(segments[j]["start_ms"], segments[j]["end_ms"]) for j in pooling_indices]

                        try:
                            if encoder is None:
                                encoder = _build_voice_encoder()
                            embedding, pooled_duration_ms = embed_pooled_voice_segments(
                                track.wav_path, spans, config.voice_model, encoder=encoder
                            )
                        except ModelLoadError:
                            raise
                        except Exception as exc:
                            logger.warning(
                                "Pooled segment embedding failed %s label=%s: %s",
                                file_path, speaker_label, exc,
                            )
                            embedding, pooled_duration_ms = None, None

                        matched_person_id = None
                        matched_cluster_id = None
                        matched_similarity = None

                        if embedding is not None:
                            match_threshold = _duration_weighted_threshold(
                                pooled_duration_ms / 1000.0, config.voice_similarity_threshold
                            )
                            best_person_id = None
                            best_sim = 0.0
                            for person_id, cent in centroids.items():
                                sim = cosine_similarity(embedding, cent["blob"])
                                if sim > best_sim:
                                    best_sim = sim
                                    best_person_id = person_id

                            if best_person_id is not None and best_sim >= match_threshold:
                                matched_person_id = best_person_id
                                matched_similarity = best_sim
                                segments_matched += len(indices)
                                old = centroids[best_person_id]
                                new_blob, new_count = update_centroid(
                                    old["blob"], old["count"], embedding
                                )
                                centroids[best_person_id] = {"blob": new_blob, "count": new_count}
                                _db_update_person_centroid(kb_conn, best_person_id, new_blob, new_count, kind="voice")
                            else:
                                best_ci = None
                                best_cluster_sim = 0.0
                                for ci, cl in enumerate(cluster_centroids):
                                    sim = cosine_similarity(embedding, cl["blob"])
                                    if sim > best_cluster_sim:
                                        best_cluster_sim = sim
                                        best_ci = ci

                                if best_ci is None or best_cluster_sim < match_threshold:
                                    cid = upsert_voice_speaker_cluster(corpus_conn, None, embedding, 1, 0.0)
                                    cluster_centroids.append({"id": cid, "blob": embedding, "count": 1})
                                    matched_cluster_id = cid
                                else:
                                    cl = cluster_centroids[best_ci]
                                    new_blob, new_count = update_centroid(
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

                        label_matches[speaker_label] = {
                            "embedding": embedding,
                            "person_id": matched_person_id,
                            "cluster_id": matched_cluster_id,
                            "similarity": matched_similarity,
                        }

                    for seg_idx, seg in enumerate(segments):
                        segments_found += 1
                        match = label_matches[seg["speaker_label"]]
                        upsert_voice_segment(
                            corpus_conn, file_id, seg_idx, seg["start_ms"], seg["end_ms"], seg["speaker_label"],
                            match["embedding"], match["cluster_id"], match["person_id"], match["similarity"],
                        )

                    set_voice_diarize_checked(corpus_conn, file_id)
                    corpus_conn.commit()
                    kb_conn.commit()
                    files_processed += 1

            except ModelLoadError:
                raise
            except Exception as exc:
                logger.warning("Diarize: error processing %s: %s", file_path, exc)
                error_count += 1
                set_voice_diarize_checked(corpus_conn, file_id)
                corpus_conn.commit()

            progress.update(i + 1, total)

        update_pipeline_checkpoint(
            corpus_conn, "voice_diarize", files_processed, 0,
            error_count, _time.monotonic() - _start,
        )
        corpus_conn.commit()
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
