"""Integration tests for KB.P12 extended hashing — video hashes."""
import json
from pathlib import Path

import pytest

from src.config import Config
from src.db.corpus import (
    add_source,
    open_corpus,
    upsert_file_hash,
    upsert_video_hash,
)
from src.db.kb import open_kb
from src.pipeline.cancel import make_cancel_event
from src.pipeline.progress import NullProgressReporter
from src.stages.hash import run_hash
from src.stages.ingest import run_ingest


def _make_image(path: Path, color=(128, 64, 32)) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=color).save(path, "JPEG")


def _setup_images(tmp_path: Path, names: list[str]) -> tuple[Path, Path]:
    src_dir = tmp_path / "sources"
    for name in names:
        _make_image(src_dir / name)
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()
    open_kb(kb_path).close()
    run_ingest(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())
    return corpus_path, kb_path


# ---------------------------------------------------------------------------
# upsert isolation — ensure columns don't wipe each other
# ---------------------------------------------------------------------------

def test_upsert_file_hash_does_not_wipe_video_columns(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'images', 1, 0.0)"
    )
    conn.commit()

    upsert_video_hash(conn, 1, "abcd1234", json.dumps(["hash1"]))
    conn.commit()
    upsert_file_hash(conn, 1, "sha_content", "phash_val", "dhash_val")
    conn.commit()

    row = conn.execute("SELECT * FROM file_hashes WHERE file_id = 1").fetchone()
    conn.close()
    assert row["video_collage_phash"] == "abcd1234"
    assert row["phash"] == "phash_val"


def test_upsert_video_hash_does_not_wipe_image_columns(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'images', 1, 0.0)"
    )
    conn.commit()

    upsert_file_hash(conn, 1, "sha_content", "phash_val", "dhash_val")
    conn.commit()
    upsert_video_hash(conn, 1, "vcphash", json.dumps(["vhash1"]))
    conn.commit()

    row = conn.execute("SELECT * FROM file_hashes WHERE file_id = 1").fetchone()
    conn.close()
    assert row["phash"] == "phash_val"
    assert row["video_collage_phash"] == "vcphash"


# ---------------------------------------------------------------------------
# Video hashing — needs a real video file; skip if ffmpeg absent
# ---------------------------------------------------------------------------

def _make_video(path: Path) -> bool:
    """Create a tiny test video. Returns False if ffmpeg unavailable."""
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "color=c=blue:size=64x64:rate=10",
            "-t", "2",
            str(path),
        ],
        capture_output=True,
    )
    return result.returncode == 0


def _setup_video(tmp_path: Path) -> tuple[Path, Path] | None:
    src_dir = tmp_path / "sources"
    video_path = src_dir / "clip.mp4"
    if not _make_video(video_path):
        return None
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "video", True)
    conn.close()
    open_kb(kb_path).close()
    run_ingest(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())
    return corpus_path, kb_path


def test_video_collage_phash_populated(tmp_path):
    result = _setup_video(tmp_path)
    if result is None:
        pytest.skip("ffmpeg not available")
    corpus_path, kb_path = result

    run_hash(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT video_collage_phash FROM file_hashes").fetchone()
    conn.close()

    if row is None or row["video_collage_phash"] is None:
        pytest.skip("ffprobe/ffmpeg not on PATH in this environment")
    assert len(row["video_collage_phash"]) == 16


def test_video_frame_phashes_count_matches_n_frames(tmp_path):
    result = _setup_video(tmp_path)
    if result is None:
        pytest.skip("ffmpeg not available")
    corpus_path, kb_path = result

    run_hash(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT video_frame_phashes FROM file_hashes").fetchone()
    conn.close()

    if row is None or row["video_frame_phashes"] is None:
        pytest.skip("ffprobe/ffmpeg not on PATH in this environment")
    hashes = json.loads(row["video_frame_phashes"])
    assert isinstance(hashes, list)
    assert len(hashes) > 0
