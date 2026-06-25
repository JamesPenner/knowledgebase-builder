"""Unit tests for source/file_type filter params on get_pending_* functions."""
import pytest

from src.db.corpus import (
    get_pending_aesthetic_files,
    get_pending_describe_files,
    get_pending_quality_files,
    get_pending_retag_files,
    get_pending_summarize_files,
    get_pending_transcribe_files,
    open_corpus,
)


def _add_source(conn, path: str) -> int:
    from src.db.corpus import add_source
    return add_source(conn, path)


def _add_file(conn, source_id: int, path: str, file_type: str) -> int:
    ext = ".jpg" if file_type == "image" else ".mp4" if file_type == "video" else ".mp3"
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, ?, ?, ?, ?, 1000, 0.0)",
        (source_id, path, path.split("/")[-1], ext, file_type),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]


def _mark_description_done(conn, file_id: int) -> None:
    conn.execute(
        "INSERT INTO descriptions (file_id, pass1_status, model, processed_at)"
        " VALUES (?, 'done', 'test', datetime('now'))",
        (file_id,),
    )
    conn.commit()


@pytest.fixture
def scoped_corpus(tmp_path):
    """Corpus with 2 sources and 4 files for filter testing."""
    conn = open_corpus(tmp_path / "corpus.db")
    src1 = _add_source(conn, "/photos/2024")
    src2 = _add_source(conn, "/photos/2023")
    # source 1: 2 images + 1 video
    img1 = _add_file(conn, src1, "/photos/2024/a.jpg", "image")
    img2 = _add_file(conn, src1, "/photos/2024/b.jpg", "image")
    vid1 = _add_file(conn, src1, "/photos/2024/c.mp4", "video")
    # source 2: 1 image
    img3 = _add_file(conn, src2, "/photos/2023/d.jpg", "image")
    return conn, src1, src2, img1, img2, vid1, img3


# ---------------------------------------------------------------------------
# describe filters
# ---------------------------------------------------------------------------

def test_pending_describe_no_filter(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    rows = get_pending_describe_files(conn)
    assert len(rows) == 4


def test_pending_describe_filter_by_source(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    rows = get_pending_describe_files(conn, source_id=src1)
    assert len(rows) == 3
    paths = {r["path"] for r in rows}
    assert "/photos/2023/d.jpg" not in paths


def test_pending_describe_filter_by_type_image(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    rows = get_pending_describe_files(conn, file_type="image")
    assert len(rows) == 3
    assert all(r["file_type"] == "image" for r in rows)


def test_pending_describe_filter_by_type_video(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    rows = get_pending_describe_files(conn, file_type="video")
    assert len(rows) == 1
    assert rows[0]["file_type"] == "video"


def test_pending_describe_combined_filter(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    rows = get_pending_describe_files(conn, source_id=src1, file_type="image")
    assert len(rows) == 2
    paths = {r["path"] for r in rows}
    assert "/photos/2024/a.jpg" in paths
    assert "/photos/2024/b.jpg" in paths


def test_pending_describe_excludes_done(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    _mark_description_done(conn, img1)
    rows = get_pending_describe_files(conn, source_id=src1)
    # img1 is done, img2 and vid1 still pending
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# transcribe filters
# ---------------------------------------------------------------------------

def test_pending_transcribe_filter_by_source(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    # only video/audio files are transcribable
    rows = get_pending_transcribe_files(conn)
    assert len(rows) == 1  # only vid1

    rows_src1 = get_pending_transcribe_files(conn, source_id=src1)
    assert len(rows_src1) == 1

    rows_src2 = get_pending_transcribe_files(conn, source_id=src2)
    assert len(rows_src2) == 0


def test_pending_transcribe_filter_by_type(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    rows = get_pending_transcribe_files(conn, file_type="video")
    assert len(rows) == 1
    rows_audio = get_pending_transcribe_files(conn, file_type="audio")
    assert len(rows_audio) == 0


# ---------------------------------------------------------------------------
# quality filters  (file_type stored as 'images' plural in quality stage)
# ---------------------------------------------------------------------------

def test_pending_quality_filter_by_source(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src1 = _add_source(conn, "/q1")
    src2 = _add_source(conn, "/q2")
    # quality stage queries file_type IN ('images', 'video')
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/q1/a.jpg', 'a.jpg', '.jpg', 'images', 1000, 0.0)",
        (src1,),
    )
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/q2/b.jpg', 'b.jpg', '.jpg', 'images', 1000, 0.0)",
        (src2,),
    )
    conn.commit()
    rows_all = get_pending_quality_files(conn)
    assert len(rows_all) == 2
    rows_src1 = get_pending_quality_files(conn, source_id=src1)
    assert len(rows_src1) == 1


def test_pending_quality_filter_by_type(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src1 = _add_source(conn, "/q1")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/q1/a.jpg', 'a.jpg', '.jpg', 'images', 1000, 0.0)",
        (src1,),
    )
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/q1/b.mp4', 'b.mp4', '.mp4', 'video', 1000, 0.0)",
        (src1,),
    )
    conn.commit()
    rows_img = get_pending_quality_files(conn, file_type="images")
    assert len(rows_img) == 1
    rows_vid = get_pending_quality_files(conn, file_type="video")
    assert len(rows_vid) == 1


# ---------------------------------------------------------------------------
# aesthetic filters
# ---------------------------------------------------------------------------

def test_pending_aesthetic_filter_by_source(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src1 = _add_source(conn, "/a1")
    src2 = _add_source(conn, "/a2")
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/a1/x.jpg', 'x.jpg', '.jpg', 'image', 1000, 0.0)",
        (src1,),
    )
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, '/a2/y.jpg', 'y.jpg', '.jpg', 'image', 1000, 0.0)",
        (src2,),
    )
    conn.commit()
    rows_all = get_pending_aesthetic_files(conn, "nima_mobilenet")
    assert len(rows_all) == 2
    rows_src1 = get_pending_aesthetic_files(conn, "nima_mobilenet", source_id=src1)
    assert len(rows_src1) == 1


# ---------------------------------------------------------------------------
# summarize filters
# ---------------------------------------------------------------------------

def test_pending_summarize_filter_by_source(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    # mark img1 and img3 as having done descriptions (eligible for summarize)
    _mark_description_done(conn, img1)
    _mark_description_done(conn, img3)

    rows_all = get_pending_summarize_files(conn)
    assert len(rows_all) == 2

    rows_src1 = get_pending_summarize_files(conn, source_id=src1)
    assert len(rows_src1) == 1  # only img1

    rows_src2 = get_pending_summarize_files(conn, source_id=src2)
    assert len(rows_src2) == 1  # only img3


# ---------------------------------------------------------------------------
# retag filters
# ---------------------------------------------------------------------------

def test_pending_retag_filter_by_source(scoped_corpus):
    conn, src1, src2, img1, img2, vid1, img3 = scoped_corpus
    rows_all = get_pending_retag_files(conn)
    assert len(rows_all) == 4

    rows_src1 = get_pending_retag_files(conn, source_id=src1)
    assert len(rows_src1) == 3

    rows_src2 = get_pending_retag_files(conn, source_id=src2)
    assert len(rows_src2) == 1
