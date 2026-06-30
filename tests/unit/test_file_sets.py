import json
import sqlite3
import pytest

from src.db.corpus import (
    add_source,
    count_files_matching,
    create_file_set,
    delete_file_set,
    get_distinct_folders,
    get_file_set,
    get_file_sets,
    open_corpus,
    remove_source,
    resolve_set_as_filter,
    upsert_file,
)
from src.pipeline.filter_spec import CorpusFilterSpec


def _seed(conn: sqlite3.Connection, n: int = 3, source_path: str = "/test/source") -> tuple[int, list[int]]:
    """Insert a source and n files; return (source_id, [file_ids])."""
    src_id = add_source(conn, source_path, "images", True)
    file_ids = []
    for i in range(n):
        fid = upsert_file(conn, src_id, f"{source_path}/f{i}.jpg", f"f{i}.jpg", ".jpg", "images", 1000 + i, 0.0)
        file_ids.append(fid)
    conn.commit()
    return src_id, file_ids


# ---------------------------------------------------------------------------
# CorpusFilterSpec.to_sql_fragment
# ---------------------------------------------------------------------------

def test_empty_spec_returns_empty_fragment():
    spec = CorpusFilterSpec()
    frag, params = spec.to_sql_fragment()
    assert frag == ""
    assert params == []


def test_spec_source_id():
    spec = CorpusFilterSpec(source_id=3)
    frag, params = spec.to_sql_fragment()
    assert "f.source_id = ?" in frag
    assert params == [3]


def test_spec_file_type():
    spec = CorpusFilterSpec(file_type="images")
    frag, params = spec.to_sql_fragment()
    assert "f.file_type = ?" in frag
    assert params == ["images"]


def test_spec_folder_prefix_both_separators():
    spec = CorpusFilterSpec(folder_prefix="/photos/Italy")
    frag, params = spec.to_sql_fragment()
    assert "f.path LIKE ?" in frag
    assert any("/photos/Italy/%" in p for p in params)
    assert any("/photos/Italy\\%" in p for p in params)


def test_spec_date_from_and_to():
    spec = CorpusFilterSpec(date_from="2023-01-01", date_to="2023-12-31")
    frag, params = spec.to_sql_fragment()
    assert "date(f.mtime, 'unixepoch') >= ?" in frag
    assert "date(f.mtime, 'unixepoch') <= ?" in frag
    assert "2023-01-01" in params
    assert "2023-12-31" in params


def test_spec_name_pattern_glob():
    spec = CorpusFilterSpec(name_pattern="IMG_*.jpg")
    frag, params = spec.to_sql_fragment()
    assert "f.filename LIKE ?" in frag
    # _ is a SQL wildcard so literal _ in the pattern must be escaped
    assert params[0] == r"IMG\_%.jpg"


def test_spec_name_pattern_escapes_sql_special_chars():
    spec = CorpusFilterSpec(name_pattern="50%_off*.jpg")
    frag, params = spec.to_sql_fragment()
    # % → \%, _ → \_, * → %
    assert r"\%" in params[0]
    assert r"\_" in params[0]


def test_spec_combined_multiple_filters():
    spec = CorpusFilterSpec(source_id=1, file_type="video", date_from="2024-01-01")
    frag, params = spec.to_sql_fragment()
    assert "f.source_id = ?" in frag
    assert "f.file_type = ?" in frag
    assert "date(f.mtime, 'unixepoch') >= ?" in frag
    assert len(params) == 3


# ---------------------------------------------------------------------------
# CorpusFilterSpec.summary / is_empty
# ---------------------------------------------------------------------------

def test_spec_summary_empty():
    assert CorpusFilterSpec().summary() == "All files"


def test_spec_summary_with_type():
    assert "Images" in CorpusFilterSpec(file_type="images").summary()


def test_spec_summary_date_range():
    s = CorpusFilterSpec(date_from="2023-01-01", date_to="2023-06-30").summary()
    assert "2023-01-01" in s
    assert "2023-06-30" in s


def test_spec_summary_with_folder():
    s = CorpusFilterSpec(folder_prefix="/photos/Italy").summary()
    assert "Italy" in s


def test_spec_is_empty():
    assert CorpusFilterSpec().is_empty()
    assert not CorpusFilterSpec(source_id=1).is_empty()
    assert not CorpusFilterSpec(folder_prefix="/sub").is_empty()
    assert not CorpusFilterSpec(name_pattern="IMG_*").is_empty()


# ---------------------------------------------------------------------------
# count_files_matching
# ---------------------------------------------------------------------------

def test_count_files_matching_no_filter(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _seed(conn, 4)
    assert count_files_matching(conn, CorpusFilterSpec()) == 4


def test_count_files_matching_by_source(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src1, _ = _seed(conn, 2, "/a")
    src2, _ = _seed(conn, 3, "/b")
    assert count_files_matching(conn, CorpusFilterSpec(source_id=src1)) == 2
    assert count_files_matching(conn, CorpusFilterSpec(source_id=src2)) == 3


def test_count_files_matching_by_file_type(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id = add_source(conn, "/x", "all", True)
    upsert_file(conn, src_id, "/x/a.jpg", "a.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src_id, "/x/b.mp4", "b.mp4", ".mp4", "video", 1001, 0)
    conn.commit()
    assert count_files_matching(conn, CorpusFilterSpec(file_type="images")) == 1
    assert count_files_matching(conn, CorpusFilterSpec(file_type="video")) == 1


def test_count_files_matching_by_name_pattern(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id = add_source(conn, "/x", "images", True)
    upsert_file(conn, src_id, "/x/IMG_001.jpg", "IMG_001.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src_id, "/x/photo.jpg", "photo.jpg", ".jpg", "images", 1001, 0)
    conn.commit()
    assert count_files_matching(conn, CorpusFilterSpec(name_pattern="IMG_*")) == 1


# ---------------------------------------------------------------------------
# get_distinct_folders
# ---------------------------------------------------------------------------

def test_get_distinct_folders_basic(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id = add_source(conn, "/photos", "images", True)
    upsert_file(conn, src_id, "/photos/2023/a.jpg", "a.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src_id, "/photos/2023/b.jpg", "b.jpg", ".jpg", "images", 1001, 0)
    upsert_file(conn, src_id, "/photos/2024/c.jpg", "c.jpg", ".jpg", "images", 1002, 0)
    conn.commit()
    folders = get_distinct_folders(conn)
    assert "/photos/2023" in folders
    assert "/photos/2024" in folders
    assert len(folders) == 2


def test_get_distinct_folders_filtered_by_source(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src1 = add_source(conn, "/a", "images", True)
    src2 = add_source(conn, "/b", "images", True)
    upsert_file(conn, src1, "/a/sub/f.jpg", "f.jpg", ".jpg", "images", 1000, 0)
    upsert_file(conn, src2, "/b/sub/g.jpg", "g.jpg", ".jpg", "images", 1001, 0)
    conn.commit()
    folders = get_distinct_folders(conn, source_id=src1)
    assert any("/a/" in f for f in folders)
    assert not any("/b/" in f for f in folders)


# ---------------------------------------------------------------------------
# create_file_set / get_file_sets / get_file_set
# ---------------------------------------------------------------------------

def test_create_file_set_returns_id(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _seed(conn)
    spec = CorpusFilterSpec(file_type="images")
    set_id = create_file_set(conn, "vacation", "Summer 2024", spec)
    assert isinstance(set_id, int)
    assert set_id > 0


def test_get_file_sets_empty(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    assert get_file_sets(conn) == []


def test_get_file_sets_returns_live_file_count(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id, _ = _seed(conn, 4)
    create_file_set(conn, "s1", "", CorpusFilterSpec(source_id=src_id))
    create_file_set(conn, "s2", "", CorpusFilterSpec(file_type="video"))
    sets = get_file_sets(conn)
    by_name = {s["name"]: s for s in sets}
    assert by_name["s1"]["file_count"] == 4
    assert by_name["s2"]["file_count"] == 0  # no video files seeded


def test_get_file_set_single(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _seed(conn)
    spec = CorpusFilterSpec(file_type="images", name_pattern="f*")
    set_id = create_file_set(conn, "test_set", "desc", spec)
    row = get_file_set(conn, set_id)
    assert row is not None
    assert row["name"] == "test_set"
    assert row["file_type"] == "images"
    assert row["name_pattern"] == "f*"


def test_get_file_set_missing(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    assert get_file_set(conn, 9999) is None


# ---------------------------------------------------------------------------
# resolve_set_as_filter
# ---------------------------------------------------------------------------

def test_resolve_set_as_filter(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _seed(conn)
    spec = CorpusFilterSpec(source_id=1, file_type="images")
    set_id = create_file_set(conn, "my_set", "", spec)
    resolved = resolve_set_as_filter(conn, set_id)
    assert isinstance(resolved, CorpusFilterSpec)
    assert resolved.source_id == 1
    assert resolved.file_type == "images"


# ---------------------------------------------------------------------------
# delete_file_set
# ---------------------------------------------------------------------------

def test_delete_file_set(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _seed(conn)
    set_id = create_file_set(conn, "to_delete", "", CorpusFilterSpec())
    delete_file_set(conn, set_id)
    assert get_file_sets(conn) == []


def test_create_file_set_duplicate_name_raises(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    _seed(conn)
    create_file_set(conn, "dup", "", CorpusFilterSpec())
    with pytest.raises(sqlite3.IntegrityError):
        create_file_set(conn, "dup", "other desc", CorpusFilterSpec())


# ---------------------------------------------------------------------------
# add_source helpers (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_add_source_stores_filters_json(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    filters = {"glob": "2024-*", "count_limit": 50}
    src_id = add_source(conn, "/photos", "images", True, filters)
    row = conn.execute("SELECT filters_json FROM sources WHERE id=?", (src_id,)).fetchone()
    stored = json.loads(row["filters_json"])
    assert stored["glob"] == "2024-*"
    assert stored["count_limit"] == 50


def test_add_source_no_filters_defaults_empty(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id = add_source(conn, "/photos", "all", True)
    row = conn.execute("SELECT filters_json FROM sources WHERE id=?", (src_id,)).fetchone()
    assert json.loads(row["filters_json"]) == {}


# ---------------------------------------------------------------------------
# remove_source (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_remove_source_soft_delete(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id, _ = _seed(conn)
    deleted = remove_source(conn, src_id, cascade=False)
    assert deleted == 0
    row = conn.execute("SELECT removed_at FROM sources WHERE id=?", (src_id,)).fetchone()
    assert row["removed_at"] is not None
    count = conn.execute("SELECT COUNT(*) FROM files WHERE source_id=?", (src_id,)).fetchone()[0]
    assert count == 3


def test_remove_source_cascade_deletes_files(tmp_path):
    conn = open_corpus(tmp_path / "corpus.db")
    src_id, file_ids = _seed(conn, 4)
    deleted = remove_source(conn, src_id, cascade=True)
    assert deleted == 4
    count = conn.execute("SELECT COUNT(*) FROM files WHERE source_id=?", (src_id,)).fetchone()[0]
    assert count == 0
