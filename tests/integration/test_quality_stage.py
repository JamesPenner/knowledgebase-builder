"""Integration tests for the technical quality metrics stage."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_config():
    cfg = MagicMock()
    cfg.describe_frames = 3
    cfg.phash_threshold = 10
    cfg.ffmpeg = "ffmpeg"
    cfg.ffprobe = "ffprobe"
    cfg.describe_min_frame_brightness = 30.0
    cfg.describe_min_frame_sharpness = 0.0
    cfg.visual_profile = "default"
    cfg.debug_frames_dir = ""
    return cfg


def _seed_source(conn, path="/photos"):
    from src.db.corpus import add_source
    return add_source(conn, path)


def _seed_file(conn, source_id, path, fname, file_type):
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, ?, ?, '.jpg', ?, 1000, 0.0)",
        (source_id, path, fname, file_type),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]


def _make_synthetic_png(path: Path, value: int = 128) -> None:
    import numpy as np
    from PIL import Image
    arr = np.full((64, 64), value, dtype=np.uint8)
    Image.fromarray(arr, mode="L").save(path)


@pytest.fixture()
def corpus_db(tmp_path):
    from src.db.corpus import open_corpus
    return open_corpus(tmp_path / "corpus.db")


@pytest.fixture()
def kb_db(tmp_path):
    from src.db.kb import open_kb
    return open_kb(tmp_path / "knowledge.db")


def test_run_quality_scores_images(tmp_path, corpus_db, kb_db):
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.quality import run_quality

    source_id = _seed_source(corpus_db)
    img_a = tmp_path / "a.jpg"
    img_b = tmp_path / "b.jpg"
    _make_synthetic_png(img_a, 100)
    _make_synthetic_png(img_b, 200)

    _seed_file(corpus_db, source_id, str(img_a), "a.jpg", "images")
    _seed_file(corpus_db, source_id, str(img_b), "b.jpg", "images")

    corpus_db.close()
    kb_db.close()

    result = run_quality(
        tmp_path / "corpus.db", tmp_path / "knowledge.db",
        _make_config(), NullProgressReporter(), make_cancel_event(),
    )

    assert result["scored"] == 2
    assert result["errors"] == 0

    from src.db.corpus import open_corpus
    conn = open_corpus(tmp_path / "corpus.db")
    count = conn.execute("SELECT COUNT(*) FROM file_quality").fetchone()[0]
    assert count == 2
    conn.close()


def test_run_quality_skips_already_scored(tmp_path, corpus_db, kb_db):
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.quality import run_quality

    source_id = _seed_source(corpus_db)
    img = tmp_path / "c.jpg"
    _make_synthetic_png(img, 128)
    _seed_file(corpus_db, source_id, str(img), "c.jpg", "images")
    corpus_db.close()
    kb_db.close()

    kwargs = dict(
        corpus_path=tmp_path / "corpus.db",
        kb_path=tmp_path / "knowledge.db",
        config=_make_config(),
        progress=NullProgressReporter(),
        cancel_event=make_cancel_event(),
    )

    r1 = run_quality(**kwargs)
    r2 = run_quality(**kwargs)

    assert r1["scored"] == 1
    assert r2["scored"] == 0  # already scored — pending query returns nothing


def test_run_quality_skips_audio(tmp_path, corpus_db, kb_db):
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.quality import run_quality

    source_id = _seed_source(corpus_db)
    conn = corpus_db
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/audio.mp3', 'audio.mp3', '.mp3', 'audio', 500, 0.0)",
        (source_id,),
    )
    conn.commit()
    conn.close()
    kb_db.close()

    result = run_quality(
        tmp_path / "corpus.db", tmp_path / "knowledge.db",
        _make_config(), NullProgressReporter(), make_cancel_event(),
    )

    assert result["scored"] == 0


def test_run_quality_checkpoint_written(tmp_path, corpus_db, kb_db):
    from src.pipeline.cancel import make_cancel_event
    from src.pipeline.progress import NullProgressReporter
    from src.stages.quality import run_quality

    source_id = _seed_source(corpus_db)
    img = tmp_path / "d.jpg"
    _make_synthetic_png(img, 80)
    _seed_file(corpus_db, source_id, str(img), "d.jpg", "images")
    corpus_db.close()
    kb_db.close()

    run_quality(
        tmp_path / "corpus.db", tmp_path / "knowledge.db",
        _make_config(), NullProgressReporter(), make_cancel_event(),
    )

    from src.db.corpus import open_corpus
    conn = open_corpus(tmp_path / "corpus.db")
    row = conn.execute(
        "SELECT * FROM pipeline_checkpoints WHERE stage='quality'"
    ).fetchone()
    assert row is not None
    assert row["files_processed"] == 1
    conn.close()
