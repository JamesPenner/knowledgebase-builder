"""Unit tests for KB.P20 Coverage Analytics — get_coverage_per_file."""
import sqlite3


# ---------------------------------------------------------------------------
# In-memory DB
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;

        CREATE TABLE files (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            path     TEXT    NOT NULL,
            file_type TEXT   NOT NULL DEFAULT 'image'
        );
        CREATE TABLE descriptions (
            file_id      INTEGER PRIMARY KEY,
            pass1_status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE file_derived_tags (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            tag     TEXT    NOT NULL
        );
        CREATE TABLE file_entity_matches (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id       INTEGER NOT NULL,
            matched_value TEXT,
            stale         INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE file_metadata_fields (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id        INTEGER NOT NULL,
            canonical_name TEXT    NOT NULL,
            raw_field_name TEXT,
            value          TEXT,
            value_type     TEXT
        );
        CREATE TABLE file_aesthetic (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id    INTEGER NOT NULL,
            model_name TEXT    NOT NULL,
            score      REAL
        );
        CREATE TABLE file_captured_fields (
            file_id    INTEGER NOT NULL,
            field_name TEXT    NOT NULL,
            value      TEXT,
            PRIMARY KEY (file_id, field_name)
        );
        CREATE TABLE file_quality (
            file_id   INTEGER PRIMARY KEY,
            sharpness REAL
        );
        CREATE TABLE transcriptions (
            file_id           INTEGER PRIMARY KEY,
            transcribe_status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE file_face_regions (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL
        );
        CREATE TABLE file_voice_embeddings (
            file_id INTEGER PRIMARY KEY
        );
    """)
    return conn


def _add_file(conn, path="/a.jpg") -> int:
    return conn.execute("INSERT INTO files(path) VALUES (?)", (path,)).lastrowid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetCoveragePerFile:
    def test_empty_files_table_returns_empty(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        assert get_coverage_per_file(conn) == []

    def test_bare_file_all_flags_zero(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        _add_file(conn)
        rows = get_coverage_per_file(conn)
        assert len(rows) == 1
        r = rows[0]
        assert r["has_description"] == 0
        assert r["has_tags"] == 0
        assert r["has_entities"] == 0
        assert r["has_gps"] == 0
        assert r["has_aesthetic_score"] == 0
        assert r["has_asset_date"] == 0
        assert r["has_quality_score"] == 0
        assert r["has_transcript"] == 0
        assert r["has_face"] == 0
        assert r["has_voice"] == 0
        assert r["tag_count"] == 0
        assert r["entity_count"] == 0

    def test_has_description_only_when_done(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO descriptions(file_id, pass1_status) VALUES (?, 'pending')", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_description"] == 0

        conn.execute("UPDATE descriptions SET pass1_status = 'done' WHERE file_id = ?", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_description"] == 1

    def test_has_tags_and_tag_count(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO file_derived_tags(file_id, tag) VALUES (?, 'beach')", (fid,))
        conn.execute("INSERT INTO file_derived_tags(file_id, tag) VALUES (?, 'sunset')", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_tags"] == 1
        assert r["tag_count"] == 2

    def test_has_entities_excludes_stale(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute(
            "INSERT INTO file_entity_matches(file_id, matched_value, stale) VALUES (?, 'Loc', 1)",
            (fid,),
        )
        r = get_coverage_per_file(conn)[0]
        assert r["has_entities"] == 0
        assert r["entity_count"] == 0

        conn.execute("UPDATE file_entity_matches SET stale = 0")
        r = get_coverage_per_file(conn)[0]
        assert r["has_entities"] == 1
        assert r["entity_count"] == 1

    def test_has_gps_requires_gps_latitude_canonical_name(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute(
            "INSERT INTO file_metadata_fields(file_id, canonical_name, value) VALUES (?, 'gps_longitude', '49.0')",
            (fid,),
        )
        r = get_coverage_per_file(conn)[0]
        assert r["has_gps"] == 0

        conn.execute(
            "INSERT INTO file_metadata_fields(file_id, canonical_name, value) VALUES (?, 'gps_latitude', '49.0')",
            (fid,),
        )
        r = get_coverage_per_file(conn)[0]
        assert r["has_gps"] == 1

    def test_has_aesthetic_score(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO file_aesthetic(file_id, model_name, score) VALUES (?, 'nima', 6.0)", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_aesthetic_score"] == 1

    def test_has_asset_date_requires_exact_field_name(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO file_captured_fields(file_id, field_name, value) VALUES (?, 'file_date_full', '20240101')", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_asset_date"] == 0

        conn.execute("INSERT INTO file_captured_fields(file_id, field_name, value) VALUES (?, 'asset_date', '20240101')", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_asset_date"] == 1

    def test_has_quality_score(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO file_quality(file_id, sharpness) VALUES (?, 0.9)", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_quality_score"] == 1

    def test_has_transcript_only_when_done(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO transcriptions(file_id, transcribe_status) VALUES (?, 'pending')", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_transcript"] == 0

        conn.execute("UPDATE transcriptions SET transcribe_status = 'done'")
        r = get_coverage_per_file(conn)[0]
        assert r["has_transcript"] == 1

    def test_has_face(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO file_face_regions(file_id) VALUES (?)", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_face"] == 1

    def test_has_voice(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        fid = _add_file(conn)
        conn.execute("INSERT INTO file_voice_embeddings(file_id) VALUES (?)", (fid,))
        r = get_coverage_per_file(conn)[0]
        assert r["has_voice"] == 1

    def test_path_column_present(self):
        from src.db.corpus import get_coverage_per_file
        conn = _make_db()
        _add_file(conn, "/images/photo.jpg")
        r = get_coverage_per_file(conn)[0]
        assert r["path"] == "/images/photo.jpg"
