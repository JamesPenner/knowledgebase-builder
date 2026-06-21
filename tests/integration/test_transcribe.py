"""Integration tests for Stage 3b transcribe DB helpers."""
import sqlite3
from pathlib import Path

from src.db.corpus import (
    delete_transcript_segments_for_file,
    get_pending_transcribe_files,
    open_corpus,
    reset_transcribe_to_pending,
    upsert_transcript_segment,
    upsert_transcription,
)


def _add_source(conn: sqlite3.Connection) -> int:
    conn.execute("INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", ("/src",))
    conn.commit()
    return conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]


def _add_file(conn: sqlite3.Connection, source_id: int, path: str, file_type: str, canonical_id=None) -> int:
    ext = Path(path).suffix.lower()
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime, sha256, canonical_id)"
        " VALUES (?, ?, ?, ?, ?, 1000, 0.0, 'abc', ?)",
        (source_id, path, Path(path).name, ext, file_type, canonical_id),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


# ---------------------------------------------------------------------------
# get_pending_transcribe_files
# ---------------------------------------------------------------------------

def test_get_pending_transcribe_returns_audio_and_video(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    _add_file(conn, sid, "/src/audio.mp3", "audio")
    _add_file(conn, sid, "/src/video.mp4", "video")

    rows = get_pending_transcribe_files(conn)
    paths = {r["path"] for r in rows}
    assert "/src/audio.mp3" in paths
    assert "/src/video.mp4" in paths
    conn.close()


def test_get_pending_transcribe_excludes_images(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    _add_file(conn, sid, "/src/photo.jpg", "image")

    rows = get_pending_transcribe_files(conn)
    assert all(r["path"] != "/src/photo.jpg" for r in rows)
    conn.close()


def test_get_pending_transcribe_excludes_done(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid = _add_file(conn, sid, "/src/done.mp3", "audio")
    upsert_transcription(conn, fid, "text", "en", 5000, "model", "done")
    conn.commit()

    rows = get_pending_transcribe_files(conn)
    assert all(r["path"] != "/src/done.mp3" for r in rows)
    conn.close()


def test_get_pending_transcribe_excludes_no_audio(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid = _add_file(conn, sid, "/src/silent.mp4", "video")
    upsert_transcription(conn, fid, None, None, None, "model", "no_audio")
    conn.commit()

    rows = get_pending_transcribe_files(conn)
    assert all(r["path"] != "/src/silent.mp4" for r in rows)
    conn.close()


def test_get_pending_transcribe_includes_failed(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid = _add_file(conn, sid, "/src/fail.mp4", "video")
    upsert_transcription(conn, fid, None, None, None, "model", "failed")
    conn.commit()

    rows = get_pending_transcribe_files(conn)
    paths = {r["path"] for r in rows}
    assert "/src/fail.mp4" in paths
    conn.close()


def test_get_pending_transcribe_excludes_duplicates(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    canonical_id = _add_file(conn, sid, "/src/canonical.mp4", "video")
    _add_file(conn, sid, "/src/dupe.mp4", "video", canonical_id=canonical_id)

    rows = get_pending_transcribe_files(conn)
    paths = {r["path"] for r in rows}
    assert "/src/canonical.mp4" in paths
    assert "/src/dupe.mp4" not in paths
    conn.close()


# ---------------------------------------------------------------------------
# upsert_transcription
# ---------------------------------------------------------------------------

def test_upsert_transcription_idempotent(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid = _add_file(conn, sid, "/src/a.mp3", "audio")

    upsert_transcription(conn, fid, "first transcript", "en", 3000, "model-v1", "done")
    upsert_transcription(conn, fid, "second transcript", "fr", 4000, "model-v2", "done")
    conn.commit()

    rows = conn.execute("SELECT * FROM transcriptions WHERE file_id=?", (fid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["transcript_text"] == "second transcript"
    assert rows[0]["language"] == "fr"
    assert rows[0]["model"] == "model-v2"
    conn.close()


def test_upsert_transcription_no_audio_status(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid = _add_file(conn, sid, "/src/v.mp4", "video")

    upsert_transcription(conn, fid, None, None, None, "model", "no_audio")
    conn.commit()

    row = conn.execute("SELECT transcribe_status FROM transcriptions WHERE file_id=?", (fid,)).fetchone()
    assert row["transcribe_status"] == "no_audio"
    conn.close()


# ---------------------------------------------------------------------------
# upsert_transcript_segment / delete_transcript_segments_for_file
# ---------------------------------------------------------------------------

def test_upsert_transcript_segments_inserted(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid = _add_file(conn, sid, "/src/b.mp3", "audio")

    upsert_transcript_segment(conn, fid, 0, 1000, "Hello", -0.5)
    upsert_transcript_segment(conn, fid, 1000, 2000, "world", -0.3)
    conn.commit()

    rows = conn.execute("SELECT * FROM transcript_segments WHERE file_id=?", (fid,)).fetchall()
    assert len(rows) == 2
    assert rows[0]["text"] == "Hello"
    assert rows[1]["start_ms"] == 1000
    conn.close()


def test_delete_transcript_segments_only_deletes_target(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid1 = _add_file(conn, sid, "/src/a.mp3", "audio")
    fid2 = _add_file(conn, sid, "/src/b.mp3", "audio")

    upsert_transcript_segment(conn, fid1, 0, 500, "segment a", None)
    upsert_transcript_segment(conn, fid2, 0, 500, "segment b", None)
    conn.commit()

    delete_transcript_segments_for_file(conn, fid1)
    conn.commit()

    remaining = conn.execute("SELECT file_id FROM transcript_segments").fetchall()
    assert all(r["file_id"] == fid2 for r in remaining)
    conn.close()


# ---------------------------------------------------------------------------
# reset_transcribe_to_pending
# ---------------------------------------------------------------------------

def test_reset_transcribe_to_pending_all(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid1 = _add_file(conn, sid, "/src/a.mp3", "audio")
    fid2 = _add_file(conn, sid, "/src/b.mp4", "video")

    upsert_transcription(conn, fid1, "t", "en", 1000, "model-a", "done")
    upsert_transcription(conn, fid2, "t", "en", 2000, "model-b", "done")
    conn.commit()

    count = reset_transcribe_to_pending(conn)
    assert count == 2

    statuses = conn.execute("SELECT transcribe_status FROM transcriptions").fetchall()
    assert all(r["transcribe_status"] == "pending" for r in statuses)
    conn.close()


def test_reset_transcribe_to_pending_by_model(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid1 = _add_file(conn, sid, "/src/a.mp3", "audio")
    fid2 = _add_file(conn, sid, "/src/b.mp3", "audio")

    upsert_transcription(conn, fid1, "t", "en", 1000, "model-tiny", "done")
    upsert_transcription(conn, fid2, "t", "en", 2000, "model-base", "done")
    conn.commit()

    count = reset_transcribe_to_pending(conn, model_name="model-tiny")
    assert count == 1

    row1 = conn.execute("SELECT transcribe_status FROM transcriptions WHERE file_id=?", (fid1,)).fetchone()
    row2 = conn.execute("SELECT transcribe_status FROM transcriptions WHERE file_id=?", (fid2,)).fetchone()
    assert row1["transcribe_status"] == "pending"
    assert row2["transcribe_status"] == "done"  # untouched
    conn.close()


def test_reset_transcribe_does_not_touch_images(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    sid = _add_source(conn)
    fid_img = _add_file(conn, sid, "/src/photo.jpg", "image")
    fid_aud = _add_file(conn, sid, "/src/audio.mp3", "audio")

    # Manually insert a transcription row for the image (edge case)
    conn.execute(
        "INSERT INTO transcriptions (file_id, model, transcribe_status) VALUES (?, 'm', 'done')",
        (fid_img,),
    )
    upsert_transcription(conn, fid_aud, "t", "en", 1000, "m", "done")
    conn.commit()

    reset_transcribe_to_pending(conn)

    img_row = conn.execute("SELECT transcribe_status FROM transcriptions WHERE file_id=?", (fid_img,)).fetchone()
    assert img_row["transcribe_status"] == "done"  # image row untouched by reset
    conn.close()
