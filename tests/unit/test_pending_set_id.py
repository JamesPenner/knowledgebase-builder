"""Tests for CorpusFilterSpec scope on get_pending_* functions (replaces set_id tests)."""
from src.db.corpus import (
    add_source,
    get_pending_aesthetic_files,
    get_pending_describe_files,
    get_pending_quality_files,
    get_pending_retag_files,
    get_pending_summarize_files,
    get_pending_transcribe_files,
    open_corpus,
    upsert_file,
)
from src.pipeline.filter_spec import CorpusFilterSpec


def _open(tmp_path):
    return open_corpus(tmp_path / "corpus.db")


def _add_file(conn, src_id, name, file_type="images"):
    return upsert_file(conn, src_id, f"/src/{name}", name, ".jpg", file_type, 1000, 0.0)


def _setup_images(tmp_path, n: int = 4):
    conn = _open(tmp_path)
    src = add_source(conn, "/src")
    fids = [_add_file(conn, src, f"f{i}.jpg", "images") for i in range(n)]
    conn.commit()
    return conn, src, fids


def test_get_pending_describe_no_scope(tmp_path):
    conn, src, fids = _setup_images(tmp_path, 4)
    rows = get_pending_describe_files(conn)
    assert len(rows) == 4


def test_get_pending_describe_scope_by_source(tmp_path):
    conn = _open(tmp_path)
    src1 = add_source(conn, "/src1")
    src2 = add_source(conn, "/src2")
    fids1 = [_add_file(conn, src1, f"a{i}.jpg") for i in range(2)]
    _fids2 = [_add_file(conn, src2, f"b{i}.jpg") for i in range(3)]
    conn.commit()
    rows = get_pending_describe_files(conn, scope=CorpusFilterSpec(source_id=src1))
    assert {r["id"] for r in rows} == set(fids1)


def test_get_pending_describe_scope_by_folder(tmp_path):
    conn = _open(tmp_path)
    src = add_source(conn, "/src")
    fid1 = upsert_file(conn, src, "/src/sub/a.jpg", "a.jpg", ".jpg", "images", 1000, 0)
    _fid2 = upsert_file(conn, src, "/src/other/b.jpg", "b.jpg", ".jpg", "images", 1001, 0)
    conn.commit()
    rows = get_pending_describe_files(conn, scope=CorpusFilterSpec(folder_prefix="/src/sub"))
    assert {r["id"] for r in rows} == {fid1}


def test_get_pending_transcribe_scope_by_type(tmp_path):
    conn = _open(tmp_path)
    src = add_source(conn, "/src")
    a_id = upsert_file(conn, src, "/src/a.mp3", "a.mp3", ".mp3", "audio", 1000, 0.0)
    _b_id = upsert_file(conn, src, "/src/b.mp4", "b.mp4", ".mp4", "video", 1001, 0.0)
    conn.commit()
    rows = get_pending_transcribe_files(conn, scope=CorpusFilterSpec(file_type="audio"))
    assert {r["id"] for r in rows} == {a_id}


def test_get_pending_quality_scope_by_source(tmp_path):
    conn = _open(tmp_path)
    src1 = add_source(conn, "/q1")
    src2 = add_source(conn, "/q2")
    fids1 = [upsert_file(conn, src1, f"/q1/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000, 0) for i in range(3)]
    _fids2 = [upsert_file(conn, src2, f"/q2/g{i}.jpg", f"g{i}.jpg", ".jpg", "images", 1001, 0) for i in range(2)]
    conn.commit()
    rows = get_pending_quality_files(conn, scope=CorpusFilterSpec(source_id=src1))
    assert {r["id"] for r in rows} == set(fids1)


def test_get_pending_retag_scope_by_source(tmp_path):
    conn = _open(tmp_path)
    src1 = add_source(conn, "/r1")
    src2 = add_source(conn, "/r2")
    fids1 = [_add_file(conn, src1, f"r{i}.jpg") for i in range(2)]
    _fids2 = [_add_file(conn, src2, f"s{i}.jpg") for i in range(3)]
    conn.commit()
    rows = get_pending_retag_files(conn, scope=CorpusFilterSpec(source_id=src1))
    assert {r["id"] for r in rows} == set(fids1)


def test_get_pending_retag_null_scope_returns_all(tmp_path):
    conn, src, fids = _setup_images(tmp_path, 3)
    rows = get_pending_retag_files(conn)
    assert len(rows) == 3


def test_get_pending_aesthetic_scope_by_source(tmp_path):
    conn = _open(tmp_path)
    src1 = add_source(conn, "/a1")
    src2 = add_source(conn, "/a2")
    fid1 = upsert_file(conn, src1, "/a1/x.jpg", "x.jpg", ".jpg", "images", 1000, 0.0)
    _fid2 = upsert_file(conn, src2, "/a2/y.jpg", "y.jpg", ".jpg", "images", 1001, 0.0)
    conn.commit()
    rows = get_pending_aesthetic_files(conn, "nima_mobilenet", scope=CorpusFilterSpec(source_id=src1))
    assert isinstance(rows, list)
    assert {r["id"] for r in rows} == {fid1}


def test_get_pending_summarize_scope_by_source(tmp_path):
    conn = _open(tmp_path)
    src1 = add_source(conn, "/s1")
    src2 = add_source(conn, "/s2")
    fid1 = _add_file(conn, src1, "p1.jpg")
    fid2 = _add_file(conn, src2, "p2.jpg")
    conn.commit()
    for fid in [fid1, fid2]:
        conn.execute(
            "INSERT OR IGNORE INTO descriptions (file_id, description_raw, model, pass1_status)"
            " VALUES (?, 'some text', 'dummy', 'done')",
            (fid,)
        )
    conn.commit()
    rows = get_pending_summarize_files(conn, scope=CorpusFilterSpec(source_id=src1))
    assert {r["id"] for r in rows} == {fid1}
