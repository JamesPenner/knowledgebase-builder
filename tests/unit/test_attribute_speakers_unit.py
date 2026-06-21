"""Unit tests for KB.P19 Transcript Speaker Attribution — DB helpers and overlap logic."""
import sqlite3


# ---------------------------------------------------------------------------
# In-memory DB helpers
# ---------------------------------------------------------------------------

def _make_corpus_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE files (
            id        INTEGER PRIMARY KEY,
            path      TEXT    NOT NULL,
            file_type TEXT    NOT NULL DEFAULT 'audio'
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
        CREATE TABLE transcript_segments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     INTEGER NOT NULL,
            start_ms    INTEGER,
            end_ms      INTEGER,
            text        TEXT    NOT NULL DEFAULT '',
            speaker_label TEXT,
            avg_logprob REAL
        );
    """)
    return conn


def _add_file(conn, path="/audio/a.wav", file_type="audio") -> int:
    cur = conn.execute("INSERT INTO files(path, file_type) VALUES (?, ?)", (path, file_type))
    return cur.lastrowid


def _add_voice_seg(conn, file_id, start_ms, end_ms, label="SPEAKER_00",
                   cluster_id=None, person_id=None, seg_index=0):
    conn.execute(
        "INSERT INTO file_voice_segments "
        "(file_id, segment_index, start_ms, end_ms, speaker_label, cluster_id, person_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, seg_index, start_ms, end_ms, label, cluster_id, person_id),
    )


def _add_ts(conn, file_id, start_ms, end_ms, text="Hello", speaker_label=None):
    cur = conn.execute(
        "INSERT INTO transcript_segments "
        "(file_id, start_ms, end_ms, text, speaker_label) "
        "VALUES (?, ?, ?, ?, ?)",
        (file_id, start_ms, end_ms, text, speaker_label),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# get_files_pending_speaker_attribution
# ---------------------------------------------------------------------------

class TestGetFilesPendingAttribution:
    def test_returns_file_with_pending_transcript_and_voice(self):
        from src.db.corpus import get_files_pending_speaker_attribution
        conn = _make_corpus_db()
        fid = _add_file(conn)
        _add_voice_seg(conn, fid, 0, 5000)
        _add_ts(conn, fid, 0, 5000, speaker_label=None)
        conn.commit()
        rows = get_files_pending_speaker_attribution(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == fid

    def test_excludes_file_with_no_voice_segments(self):
        from src.db.corpus import get_files_pending_speaker_attribution
        conn = _make_corpus_db()
        fid = _add_file(conn)
        _add_ts(conn, fid, 0, 5000)
        conn.commit()
        rows = get_files_pending_speaker_attribution(conn)
        assert rows == []

    def test_excludes_file_where_all_transcripts_already_attributed(self):
        from src.db.corpus import get_files_pending_speaker_attribution
        conn = _make_corpus_db()
        fid = _add_file(conn)
        _add_voice_seg(conn, fid, 0, 5000)
        _add_ts(conn, fid, 0, 5000, speaker_label="Alice")
        conn.commit()
        rows = get_files_pending_speaker_attribution(conn)
        assert rows == []

    def test_returns_file_with_mix_of_attributed_and_unattributed(self):
        from src.db.corpus import get_files_pending_speaker_attribution
        conn = _make_corpus_db()
        fid = _add_file(conn)
        _add_voice_seg(conn, fid, 0, 5000)
        _add_ts(conn, fid, 0, 2000, speaker_label="Alice")
        _add_ts(conn, fid, 2000, 4000, speaker_label=None)
        conn.commit()
        rows = get_files_pending_speaker_attribution(conn)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# set_transcript_segment_speaker
# ---------------------------------------------------------------------------

class TestSetTranscriptSegmentSpeaker:
    def test_sets_speaker_label(self):
        from src.db.corpus import set_transcript_segment_speaker
        conn = _make_corpus_db()
        fid = _add_file(conn)
        sid = _add_ts(conn, fid, 0, 1000)
        set_transcript_segment_speaker(conn, sid, "Bob")
        row = conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid,)).fetchone()
        assert row["speaker_label"] == "Bob"

    def test_does_not_affect_other_segments(self):
        from src.db.corpus import set_transcript_segment_speaker
        conn = _make_corpus_db()
        fid = _add_file(conn)
        sid1 = _add_ts(conn, fid, 0, 1000)
        sid2 = _add_ts(conn, fid, 1000, 2000)
        set_transcript_segment_speaker(conn, sid1, "Carol")
        row2 = conn.execute("SELECT speaker_label FROM transcript_segments WHERE id=?", (sid2,)).fetchone()
        assert row2["speaker_label"] is None


# ---------------------------------------------------------------------------
# reset_transcript_speaker_labels
# ---------------------------------------------------------------------------

class TestResetTranscriptSpeakerLabels:
    def test_clears_all_attributed_and_returns_count(self):
        from src.db.corpus import reset_transcript_speaker_labels
        conn = _make_corpus_db()
        fid = _add_file(conn)
        _add_ts(conn, fid, 0, 1000, speaker_label="Alice")
        _add_ts(conn, fid, 1000, 2000, speaker_label="Bob")
        n = reset_transcript_speaker_labels(conn)
        assert n == 2
        rows = conn.execute("SELECT speaker_label FROM transcript_segments").fetchall()
        assert all(r["speaker_label"] is None for r in rows)

    def test_leaves_null_rows_untouched_and_returns_zero(self):
        from src.db.corpus import reset_transcript_speaker_labels
        conn = _make_corpus_db()
        fid = _add_file(conn)
        _add_ts(conn, fid, 0, 1000, speaker_label=None)
        n = reset_transcript_speaker_labels(conn)
        assert n == 0


# ---------------------------------------------------------------------------
# get_transcript_segments_for_export
# ---------------------------------------------------------------------------

class TestGetTranscriptSegmentsForExport:
    def test_returns_joined_path_and_columns(self):
        from src.db.corpus import get_transcript_segments_for_export
        conn = _make_corpus_db()
        fid = _add_file(conn, "/audio/meet.wav")
        _add_ts(conn, fid, 0, 5000, text="Hello world", speaker_label="Alice")
        conn.commit()
        rows = get_transcript_segments_for_export(conn)
        assert len(rows) == 1
        r = rows[0]
        assert r["path"] == "/audio/meet.wav"
        assert r["text"] == "Hello world"
        assert r["speaker_label"] == "Alice"
        assert r["start_ms"] == 0
        assert r["end_ms"] == 5000

    def test_orders_by_path_then_start_ms(self):
        from src.db.corpus import get_transcript_segments_for_export
        conn = _make_corpus_db()
        fid1 = _add_file(conn, "/b.wav")
        fid2 = _add_file(conn, "/a.wav")
        _add_ts(conn, fid1, 0, 1000)
        _add_ts(conn, fid2, 0, 500)
        _add_ts(conn, fid2, 500, 1000)
        conn.commit()
        rows = get_transcript_segments_for_export(conn)
        assert rows[0]["path"] == "/a.wav"
        assert rows[1]["path"] == "/a.wav"
        assert rows[2]["path"] == "/b.wav"

    def test_empty_table_returns_empty_list(self):
        from src.db.corpus import get_transcript_segments_for_export
        conn = _make_corpus_db()
        assert get_transcript_segments_for_export(conn) == []


# ---------------------------------------------------------------------------
# _best_overlap (private helper — test via import)
# ---------------------------------------------------------------------------

class TestBestOverlap:
    def _make_vs(self, start_ms, end_ms, label="SPEAKER_00", person_id=None, cluster_id=None):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE t (start_ms INT, end_ms INT, speaker_label TEXT, person_id INT, cluster_id INT)"
        )
        conn.execute("INSERT INTO t VALUES (?,?,?,?,?)", (start_ms, end_ms, label, person_id, cluster_id))
        return conn.execute("SELECT * FROM t").fetchone()

    def test_full_overlap_returns_voice_segment(self):
        from src.stages.attribute_speakers import _best_overlap
        vs = self._make_vs(0, 5000)
        result = _best_overlap(0, 5000, [vs])
        assert result is not None

    def test_partial_overlap_returns_voice_segment(self):
        from src.stages.attribute_speakers import _best_overlap
        vs = self._make_vs(3000, 8000)
        result = _best_overlap(2000, 5000, [vs])
        assert result is not None

    def test_zero_overlap_returns_none(self):
        from src.stages.attribute_speakers import _best_overlap
        vs = self._make_vs(6000, 8000)
        result = _best_overlap(0, 5000, [vs])
        assert result is None

    def test_picks_highest_overlap_from_multiple(self):
        from src.stages.attribute_speakers import _best_overlap
        vs_small = self._make_vs(4000, 5500, label="SMALL")
        vs_big = self._make_vs(0, 5000, label="BIG")
        result = _best_overlap(0, 5000, [vs_small, vs_big])
        assert result["speaker_label"] == "BIG"

    def test_null_ts_times_returns_none(self):
        from src.stages.attribute_speakers import _best_overlap
        vs = self._make_vs(0, 5000)
        assert _best_overlap(None, 5000, [vs]) is None
        assert _best_overlap(0, None, [vs]) is None


# ---------------------------------------------------------------------------
# _resolve_label
# ---------------------------------------------------------------------------

class TestResolveLabel:
    def _make_row(self, person_id, cluster_id, speaker_label):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (person_id INT, cluster_id INT, speaker_label TEXT)")
        conn.execute("INSERT INTO t VALUES (?,?,?)", (person_id, cluster_id, speaker_label))
        return conn.execute("SELECT * FROM t").fetchone()

    def test_person_id_wins(self):
        from src.stages.attribute_speakers import _resolve_label
        row = self._make_row(person_id=1, cluster_id=1, speaker_label="SPEAKER_00")
        label = _resolve_label(row, people_map={1: "Alice"}, cluster_map={1: "Group A"})
        assert label == "Alice"

    def test_cluster_label_used_when_no_person(self):
        from src.stages.attribute_speakers import _resolve_label
        row = self._make_row(person_id=None, cluster_id=2, speaker_label="SPEAKER_01")
        label = _resolve_label(row, people_map={}, cluster_map={2: "Friends"})
        assert label == "Friends"

    def test_raw_speaker_label_fallback(self):
        from src.stages.attribute_speakers import _resolve_label
        row = self._make_row(person_id=None, cluster_id=None, speaker_label="SPEAKER_02")
        label = _resolve_label(row, people_map={}, cluster_map={})
        assert label == "SPEAKER_02"

    def test_empty_cluster_label_falls_through_to_raw(self):
        from src.stages.attribute_speakers import _resolve_label
        row = self._make_row(person_id=None, cluster_id=3, speaker_label="SPEAKER_03")
        label = _resolve_label(row, people_map={}, cluster_map={3: ""})
        assert label == "SPEAKER_03"
