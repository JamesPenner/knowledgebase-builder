"""Unit tests for the corpus file browser query layer (KB.AK1)."""
import pytest

from src.db.corpus import count_files_for_browser, get_files_for_browser, open_corpus
from src.pipeline.filter_spec import CorpusFilterSpec


@pytest.fixture
def conn(tmp_path):
    return open_corpus(tmp_path / "corpus.db")


def _add_source(conn, path="/src") -> int:
    conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES (?, 'all', 1)", (path,)
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_file(
    conn, source_id, path, *, file_type="images", file_size=100,
    mtime=1_700_000_000, sha256=None, canonical_id=None,
) -> int:
    filename = path.rsplit("/", 1)[-1]
    conn.execute(
        """
        INSERT INTO files (source_id, path, filename, file_type, file_size, mtime, sha256, canonical_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, path, filename, file_type, file_size, mtime, sha256, canonical_id),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _set_description(conn, file_id, status="done"):
    conn.execute(
        "INSERT INTO descriptions (file_id, pass1_status) VALUES (?, ?)"
        " ON CONFLICT(file_id) DO UPDATE SET pass1_status=excluded.pass1_status",
        (file_id, status),
    )
    conn.commit()


def _set_transcript(conn, file_id, status="done"):
    conn.execute(
        "INSERT INTO transcriptions (file_id, transcribe_status) VALUES (?, ?)"
        " ON CONFLICT(file_id) DO UPDATE SET transcribe_status=excluded.transcribe_status",
        (file_id, status),
    )
    conn.commit()


def _set_captured_date(conn, file_id, value):
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value) VALUES (?, 'captured_date', ?)",
        (file_id, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Empty corpus
# ---------------------------------------------------------------------------

def test_empty_corpus_returns_no_files(conn):
    assert get_files_for_browser(conn, CorpusFilterSpec()) == []
    assert count_files_for_browser(conn, CorpusFilterSpec()) == 0


# ---------------------------------------------------------------------------
# Basic listing + joined flags
# ---------------------------------------------------------------------------

def test_lists_files_with_stage_flags_and_source_path(conn):
    src = _add_source(conn, "/photos")
    fid = _add_file(conn, src, "/photos/a.jpg", sha256="abc123")
    _set_description(conn, fid, "done")
    _set_transcript(conn, fid, "pending")
    _set_captured_date(conn, fid, "2023:07:04 10:00:00")

    rows = get_files_for_browser(conn, CorpusFilterSpec())
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == "/photos/a.jpg"
    assert row["source_path"] == "/photos"
    assert row["has_description"] == 1
    assert row["has_transcript"] == 0
    assert row["hashed"] == 1
    assert row["captured_date"] == "2023:07:04 10:00:00"


def test_duplicate_files_are_excluded(conn):
    src = _add_source(conn)
    canonical = _add_file(conn, src, "/src/a.jpg")
    _add_file(conn, src, "/src/a_dup.jpg", canonical_id=canonical)

    rows = get_files_for_browser(conn, CorpusFilterSpec())
    assert [r["path"] for r in rows] == ["/src/a.jpg"]
    assert count_files_for_browser(conn, CorpusFilterSpec()) == 1


# ---------------------------------------------------------------------------
# CorpusFilterSpec dimensions
# ---------------------------------------------------------------------------

def test_filters_by_source_id(conn):
    src1 = _add_source(conn, "/one")
    src2 = _add_source(conn, "/two")
    _add_file(conn, src1, "/one/a.jpg")
    _add_file(conn, src2, "/two/b.jpg")

    rows = get_files_for_browser(conn, CorpusFilterSpec(source_id=src1))
    assert [r["path"] for r in rows] == ["/one/a.jpg"]


def test_filters_by_file_type(conn):
    src = _add_source(conn)
    _add_file(conn, src, "/src/a.jpg", file_type="images")
    _add_file(conn, src, "/src/b.mp4", file_type="video")

    rows = get_files_for_browser(conn, CorpusFilterSpec(file_type="video"))
    assert [r["path"] for r in rows] == ["/src/b.mp4"]


def test_filters_by_folder_prefix(conn):
    src = _add_source(conn)
    _add_file(conn, src, "/src/2023/a.jpg")
    _add_file(conn, src, "/src/2024/b.jpg")

    rows = get_files_for_browser(conn, CorpusFilterSpec(folder_prefix="/src/2023"))
    assert [r["path"] for r in rows] == ["/src/2023/a.jpg"]


def test_filters_by_name_pattern(conn):
    src = _add_source(conn)
    _add_file(conn, src, "/src/IMG_001.jpg")
    _add_file(conn, src, "/src/DSC_001.jpg")

    rows = get_files_for_browser(conn, CorpusFilterSpec(name_pattern="IMG_*"))
    assert [r["path"] for r in rows] == ["/src/IMG_001.jpg"]


def test_filters_by_date_range(conn):
    src = _add_source(conn)
    # 1_600_000_000 -> 2020-09-13, 1_700_000_000 -> 2023-11-14
    _add_file(conn, src, "/src/old.jpg", mtime=1_600_000_000)
    _add_file(conn, src, "/src/new.jpg", mtime=1_700_000_000)

    rows = get_files_for_browser(conn, CorpusFilterSpec(date_from="2023-01-01", date_to="2023-12-31"))
    assert [r["path"] for r in rows] == ["/src/new.jpg"]


def test_combined_filters(conn):
    src1 = _add_source(conn, "/one")
    src2 = _add_source(conn, "/two")
    _add_file(conn, src1, "/one/IMG_001.jpg", file_type="images")
    _add_file(conn, src1, "/one/IMG_002.mp4", file_type="video")
    _add_file(conn, src2, "/two/IMG_001.jpg", file_type="images")

    rows = get_files_for_browser(
        conn, CorpusFilterSpec(source_id=src1, file_type="images", name_pattern="IMG_*")
    )
    assert [r["path"] for r in rows] == ["/one/IMG_001.jpg"]


# ---------------------------------------------------------------------------
# state filter — all 6 values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    ("described", ["/src/described.jpg"]),
    ("not_described", ["/src/other.jpg", "/src/transcribed.jpg", "/src/hashed.jpg"]),
    ("transcribed", ["/src/transcribed.jpg"]),
    ("not_transcribed", ["/src/described.jpg", "/src/other.jpg", "/src/hashed.jpg"]),
    ("hashed", ["/src/hashed.jpg"]),
    ("not_hashed", ["/src/described.jpg", "/src/other.jpg", "/src/transcribed.jpg"]),
])
def test_state_filter(conn, state, expected):
    src = _add_source(conn)
    described = _add_file(conn, src, "/src/described.jpg")
    _set_description(conn, described, "done")
    transcribed = _add_file(conn, src, "/src/transcribed.jpg")
    _set_transcript(conn, transcribed, "done")
    _add_file(conn, src, "/src/hashed.jpg", sha256="deadbeef")
    _add_file(conn, src, "/src/other.jpg")

    rows = get_files_for_browser(conn, CorpusFilterSpec(), state=state)
    assert sorted(r["path"] for r in rows) == sorted(expected)
    assert count_files_for_browser(conn, CorpusFilterSpec(), state=state) == len(expected)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def test_sort_by_file_size_desc(conn):
    src = _add_source(conn)
    _add_file(conn, src, "/src/small.jpg", file_size=10)
    _add_file(conn, src, "/src/big.jpg", file_size=1000)

    rows = get_files_for_browser(conn, CorpusFilterSpec(), sort_by="file_size", sort_order="desc")
    assert [r["path"] for r in rows] == ["/src/big.jpg", "/src/small.jpg"]


def test_sort_by_path_asc_default(conn):
    src = _add_source(conn)
    _add_file(conn, src, "/src/b.jpg")
    _add_file(conn, src, "/src/a.jpg")

    rows = get_files_for_browser(conn, CorpusFilterSpec())
    assert [r["path"] for r in rows] == ["/src/a.jpg", "/src/b.jpg"]


def test_unknown_sort_column_falls_back_to_path(conn):
    src = _add_source(conn)
    _add_file(conn, src, "/src/b.jpg")
    _add_file(conn, src, "/src/a.jpg")

    rows = get_files_for_browser(conn, CorpusFilterSpec(), sort_by="not_a_real_column")
    assert [r["path"] for r in rows] == ["/src/a.jpg", "/src/b.jpg"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_limit_and_offset(conn):
    src = _add_source(conn)
    for i in range(5):
        _add_file(conn, src, f"/src/{i}.jpg")

    page1 = get_files_for_browser(conn, CorpusFilterSpec(), limit=2, offset=0)
    page2 = get_files_for_browser(conn, CorpusFilterSpec(), limit=2, offset=2)
    assert [r["path"] for r in page1] == ["/src/0.jpg", "/src/1.jpg"]
    assert [r["path"] for r in page2] == ["/src/2.jpg", "/src/3.jpg"]
    assert count_files_for_browser(conn, CorpusFilterSpec()) == 5
