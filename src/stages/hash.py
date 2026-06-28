import hashlib
import json
import math
import threading
import time
from pathlib import Path

from src.config import Config
from src.db.corpus import (
    get_files_without_hash,
    get_videos_without_frame_hash,
    open_corpus,
    update_file_sha256,
    update_pipeline_checkpoint,
    upsert_file_hash,
    upsert_video_hash,
)
from src.pipeline.progress import ProgressReporter

_BATCH_SIZE = 100


def run_hash(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    conn = open_corpus(corpus_path)
    files = get_files_without_hash(conn)
    video_files = get_videos_without_frame_hash(conn)
    total = len(files) + len(video_files)
    start = time.monotonic()

    if not files and not video_files:
        _assign_canonical_ids(conn)
        update_pipeline_checkpoint(conn, "hash", files_processed=0)
        conn.close()
        progress.done()
        return

    # Pass 1 — SHA256 for all files; image hashes for image files
    for i, file_row in enumerate(files):
        if cancel_event.is_set():
            break

        file_path = Path(file_row["path"])
        try:
            data = file_path.read_bytes()
        except OSError:
            progress.update(i + 1, total)
            continue

        sha256 = hashlib.sha256(data).hexdigest()
        update_file_sha256(conn, file_row["id"], sha256)

        if file_row["file_type"] == "images":
            _hash_image(conn, file_row["id"], file_path)

        if (i + 1) % _BATCH_SIZE == 0:
            conn.commit()

        progress.update(i + 1, total)

    conn.commit()
    _assign_canonical_ids(conn)

    # Pass 2 — video frame hashes (handles backfill of pre-existing corpora)
    offset = len(files)
    for j, file_row in enumerate(video_files):
        if cancel_event.is_set():
            break
        _hash_video(conn, file_row["id"], Path(file_row["path"]), config)
        if (j + 1) % _BATCH_SIZE == 0:
            conn.commit()
        progress.update(offset + j + 1, total)

    conn.commit()

    duration = time.monotonic() - start
    update_pipeline_checkpoint(
        conn,
        "hash",
        files_processed=total,
        duration_seconds=duration,
    )
    conn.close()
    progress.done()


def _hash_image(conn, file_id: int, file_path: Path) -> None:
    try:
        import imagehash
        from PIL import Image

        with Image.open(file_path) as img:
            img.load()
            pixel_data = img.tobytes()
            sha256_content = hashlib.sha256(pixel_data).hexdigest()
            phash = str(imagehash.phash(img))
            dhash = str(imagehash.dhash(img))
        upsert_file_hash(conn, file_id, sha256_content, phash, dhash)
    except Exception:
        pass


def _hash_video(conn, file_id: int, file_path: Path, config: Config) -> None:
    try:
        import io
        import imagehash
        from PIL import Image
        from src.media.frameset import prepare_visual
        from src.stages.video import make_collage

        frameset = prepare_visual(file_path, config, max_frames=config.describe_frames or 9)
        if frameset is None:
            return

        all_frames = frameset.frames + frameset.rejected
        if not all_frames:
            return

        # Per-frame pHashes — already computed in prepare_visual; recompute for any None
        frame_phashes: list[str] = []
        for frame in all_frames:
            if frame.phash is not None:
                frame_phashes.append(frame.phash)
            else:
                with Image.open(io.BytesIO(frame.jpeg_bytes)) as img:
                    frame_phashes.append(str(imagehash.phash(img)))

        # Collage pHash — reuse frame bytes, no second ffmpeg call
        cols = math.ceil(math.sqrt(len(all_frames)))
        collage_bytes = make_collage([f.jpeg_bytes for f in all_frames], cols)
        collage_phash: str | None = None
        if collage_bytes:
            with Image.open(io.BytesIO(collage_bytes)) as img:
                collage_phash = str(imagehash.phash(img))

        upsert_video_hash(conn, file_id, collage_phash, json.dumps(frame_phashes))
    except Exception:
        pass


def _assign_canonical_ids(conn) -> None:
    conn.execute(
        """
        UPDATE files
        SET canonical_id = (
            SELECT MIN(id) FROM files f2
            WHERE f2.sha256 = files.sha256 AND f2.id != files.id
        )
        WHERE sha256 IS NOT NULL
          AND id != (SELECT MIN(id) FROM files f2 WHERE f2.sha256 = files.sha256)
        """
    )
    conn.commit()
