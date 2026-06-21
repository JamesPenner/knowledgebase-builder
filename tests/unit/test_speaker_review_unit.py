"""Unit tests for KB.P18 Speaker Review — DB helpers and merge_voice_centroid."""
import sqlite3

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blob(seed: int = 0, dim: int = 256) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


def _make_corpus_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE files (
            id       INTEGER PRIMARY KEY,
            path     TEXT    NOT NULL,
            file_type TEXT   NOT NULL DEFAULT 'audio'
        );
        CREATE TABLE file_voice_segments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id       INTEGER NOT NULL,
            segment_index INTEGER NOT NULL,
            start_ms      INTEGER NOT NULL,
            end_ms        INTEGER NOT NULL,
            speaker_label TEXT    NOT NULL,
            embedding     BLOB,
            cluster_id    INTEGER,
            person_id     INTEGER,
            similarity    REAL,
            processed_at  DATETIME DEFAULT (datetime('now')),
            UNIQUE(file_id, segment_index)
        );
        CREATE TABLE voice_speaker_clusters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            centroid     BLOB    NOT NULL,
            member_count INTEGER NOT NULL DEFAULT 0,
            spread       REAL,
            label        TEXT,
            person_id    INTEGER,
            created_at   DATETIME DEFAULT (datetime('now'))
        );
    """)
    return conn


def _make_kb_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE people (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            preferred_name TEXT    NOT NULL,
            title          TEXT,
            first_name     TEXT,
            middle_name    TEXT,
            last_name      TEXT,
            family         INTEGER DEFAULT 0,
            notes          TEXT,
            voice_centroid BLOB,
            voice_samples  INTEGER DEFAULT 0,
            created_at     DATETIME DEFAULT (datetime('now'))
        );
    """)
    return conn


def _seed_cluster(conn, person_id=None, centroid_seed=0, member_count=2):
    cur = conn.execute(
        "INSERT INTO voice_speaker_clusters (centroid, member_count, person_id) VALUES (?, ?, ?)",
        (_blob(centroid_seed), member_count, person_id),
    )
    return cur.lastrowid


def _seed_segment(conn, file_id, cluster_id, seg_index=0, embedding_seed=0):
    conn.execute(
        "INSERT INTO file_voice_segments (file_id, segment_index, start_ms, end_ms, speaker_label, embedding, cluster_id) "
        "VALUES (?, ?, 0, 2000, 'SPEAKER_00', ?, ?)",
        (file_id, seg_index, _blob(embedding_seed), cluster_id),
    )


# ---------------------------------------------------------------------------
# get_pending_speaker_clusters
# ---------------------------------------------------------------------------

class TestGetPendingSpeakerClusters:
    def test_returns_unassigned_clusters(self):
        from src.db.corpus import get_pending_speaker_clusters
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/a.wav')")
        cid = _seed_cluster(conn, person_id=None)
        _seed_segment(conn, 1, cid)
        rows = get_pending_speaker_clusters(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == cid

    def test_excludes_assigned_clusters(self):
        from src.db.corpus import get_pending_speaker_clusters
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/a.wav')")
        cid = _seed_cluster(conn, person_id=7)
        _seed_segment(conn, 1, cid)
        rows = get_pending_speaker_clusters(conn)
        assert rows == []

    def test_includes_sample_segment_path(self):
        from src.db.corpus import get_pending_speaker_clusters
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/audio/meet.wav')")
        cid = _seed_cluster(conn)
        _seed_segment(conn, 1, cid)
        rows = get_pending_speaker_clusters(conn)
        assert rows[0]["sample_path"] == "/audio/meet.wav"
        assert rows[0]["sample_file_id"] == 1

    def test_sample_is_none_when_no_segments(self):
        from src.db.corpus import get_pending_speaker_clusters
        conn = _make_corpus_db()
        _seed_cluster(conn)
        rows = get_pending_speaker_clusters(conn)
        assert len(rows) == 1
        assert rows[0]["sample_file_id"] is None

    def test_empty_when_no_clusters(self):
        from src.db.corpus import get_pending_speaker_clusters
        conn = _make_corpus_db()
        assert get_pending_speaker_clusters(conn) == []


# ---------------------------------------------------------------------------
# get_assigned_speaker_clusters
# ---------------------------------------------------------------------------

class TestGetAssignedSpeakerClusters:
    def test_returns_assigned(self):
        from src.db.corpus import get_assigned_speaker_clusters
        conn = _make_corpus_db()
        _seed_cluster(conn, person_id=3, centroid_seed=0)
        rows = get_assigned_speaker_clusters(conn)
        assert len(rows) == 1
        assert rows[0]["person_id"] == 3

    def test_excludes_unassigned(self):
        from src.db.corpus import get_assigned_speaker_clusters
        conn = _make_corpus_db()
        _seed_cluster(conn, person_id=None)
        assert get_assigned_speaker_clusters(conn) == []

    def test_empty_table(self):
        from src.db.corpus import get_assigned_speaker_clusters
        conn = _make_corpus_db()
        assert get_assigned_speaker_clusters(conn) == []


# ---------------------------------------------------------------------------
# assign_speaker_cluster
# ---------------------------------------------------------------------------

class TestAssignSpeakerCluster:
    def test_sets_person_id_and_label(self):
        from src.db.corpus import assign_speaker_cluster
        conn = _make_corpus_db()
        cid = _seed_cluster(conn)
        assign_speaker_cluster(conn, cid, 5, "Alice")
        row = conn.execute("SELECT person_id, label FROM voice_speaker_clusters WHERE id=?", (cid,)).fetchone()
        assert row["person_id"] == 5
        assert row["label"] == "Alice"

    def test_propagates_person_id_to_segments(self):
        from src.db.corpus import assign_speaker_cluster
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/a.wav')")
        cid = _seed_cluster(conn)
        _seed_segment(conn, 1, cid, seg_index=0)
        _seed_segment(conn, 1, cid, seg_index=1)
        assign_speaker_cluster(conn, cid, 5, "Alice")
        segs = conn.execute("SELECT person_id FROM file_voice_segments WHERE cluster_id=?", (cid,)).fetchall()
        assert all(s["person_id"] == 5 for s in segs)

    def test_does_not_affect_other_clusters(self):
        from src.db.corpus import assign_speaker_cluster
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/a.wav')")
        cid1 = _seed_cluster(conn, centroid_seed=0)
        cid2 = _seed_cluster(conn, centroid_seed=1)
        _seed_segment(conn, 1, cid2, seg_index=0)
        assign_speaker_cluster(conn, cid1, 5, "Alice")
        seg = conn.execute("SELECT person_id FROM file_voice_segments WHERE cluster_id=?", (cid2,)).fetchone()
        assert seg["person_id"] is None


# ---------------------------------------------------------------------------
# unassign_speaker_cluster
# ---------------------------------------------------------------------------

class TestUnassignSpeakerCluster:
    def test_clears_cluster_person_id(self):
        from src.db.corpus import assign_speaker_cluster, unassign_speaker_cluster
        conn = _make_corpus_db()
        cid = _seed_cluster(conn)
        assign_speaker_cluster(conn, cid, 5, "Alice")
        unassign_speaker_cluster(conn, cid)
        row = conn.execute("SELECT person_id, label FROM voice_speaker_clusters WHERE id=?", (cid,)).fetchone()
        assert row["person_id"] is None
        assert row["label"] is None

    def test_clears_segment_person_ids(self):
        from src.db.corpus import assign_speaker_cluster, unassign_speaker_cluster
        conn = _make_corpus_db()
        conn.execute("INSERT INTO files(id, path) VALUES (1, '/a.wav')")
        cid = _seed_cluster(conn)
        _seed_segment(conn, 1, cid)
        assign_speaker_cluster(conn, cid, 5, "Alice")
        unassign_speaker_cluster(conn, cid)
        seg = conn.execute("SELECT person_id FROM file_voice_segments WHERE cluster_id=?", (cid,)).fetchone()
        assert seg["person_id"] is None


# ---------------------------------------------------------------------------
# get_all_people
# ---------------------------------------------------------------------------

class TestGetAllPeople:
    def test_returns_all_ordered_by_name(self):
        from src.db.kb import get_all_people
        conn = _make_kb_db()
        conn.execute("INSERT INTO people(preferred_name) VALUES ('Zelda')")
        conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')")
        rows = get_all_people(conn)
        assert [r["preferred_name"] for r in rows] == ["Alice", "Zelda"]

    def test_empty_table_returns_empty_list(self):
        from src.db.kb import get_all_people
        conn = _make_kb_db()
        assert get_all_people(conn) == []


# ---------------------------------------------------------------------------
# merge_voice_centroid
# ---------------------------------------------------------------------------

class TestMergeVoiceCentroid:
    def test_sets_directly_when_no_prior_centroid(self):
        from src.db.kb import merge_voice_centroid
        conn = _make_kb_db()
        pid = conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')").lastrowid
        emb = _blob(1)
        merge_voice_centroid(conn, pid, emb, 3)
        row = conn.execute("SELECT voice_centroid, voice_samples FROM people WHERE id=?", (pid,)).fetchone()
        result = np.frombuffer(bytes(row["voice_centroid"]), dtype=np.float32)
        expected = np.frombuffer(emb, dtype=np.float32)
        np.testing.assert_allclose(result, expected, atol=1e-5)
        assert row["voice_samples"] == 3

    def test_weighted_average_is_l2_normalised(self):
        from src.db.kb import merge_voice_centroid
        conn = _make_kb_db()
        pid = conn.execute("INSERT INTO people(preferred_name) VALUES ('Bob')").lastrowid
        emb1 = _blob(0)
        emb2 = _blob(1)
        merge_voice_centroid(conn, pid, emb1, 2)
        merge_voice_centroid(conn, pid, emb2, 2)
        row = conn.execute("SELECT voice_centroid FROM people WHERE id=?", (pid,)).fetchone()
        result = np.frombuffer(bytes(row["voice_centroid"]), dtype=np.float32)
        norm = float(np.linalg.norm(result))
        assert abs(norm - 1.0) < 1e-4

    def test_sample_count_accumulates(self):
        from src.db.kb import merge_voice_centroid
        conn = _make_kb_db()
        pid = conn.execute("INSERT INTO people(preferred_name) VALUES ('Carol')").lastrowid
        merge_voice_centroid(conn, pid, _blob(0), 3)
        merge_voice_centroid(conn, pid, _blob(1), 5)
        row = conn.execute("SELECT voice_samples FROM people WHERE id=?", (pid,)).fetchone()
        assert row["voice_samples"] == 8

    def test_noop_for_unknown_person(self):
        from src.db.kb import merge_voice_centroid
        conn = _make_kb_db()
        merge_voice_centroid(conn, 999, _blob(0), 1)
