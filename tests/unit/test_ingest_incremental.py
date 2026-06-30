"""Tests for update_source_ingested and per-source incremental re-ingest."""
import os
import threading
import time

from src.db.corpus import add_source, open_corpus, update_source_ingested
from src.pipeline.progress import NullProgressReporter
from src.stages.ingest import run_ingest


# ---------------------------------------------------------------------------
# update_source_ingested
# ---------------------------------------------------------------------------

class TestUpdateSourceIngested:
    def test_writes_last_ingested_at_and_count(self, tmp_path):
        conn = open_corpus(tmp_path / "corpus.db")
        sid = add_source(conn, str(tmp_path / "photos"))
        update_source_ingested(conn, sid, 42)
        row = conn.execute("SELECT last_ingested_at, file_count_ingested FROM sources WHERE id=?", (sid,)).fetchone()
        assert row["last_ingested_at"] is not None
        assert row["file_count_ingested"] == 42
        conn.close()

    def test_second_call_updates_not_appends(self, tmp_path):
        conn = open_corpus(tmp_path / "corpus.db")
        sid = add_source(conn, str(tmp_path / "photos"))
        update_source_ingested(conn, sid, 10)
        update_source_ingested(conn, sid, 20)
        row = conn.execute("SELECT file_count_ingested FROM sources WHERE id=?", (sid,)).fetchone()
        assert row["file_count_ingested"] == 20
        conn.close()


# ---------------------------------------------------------------------------
# run_ingest always calls update_source_ingested
# ---------------------------------------------------------------------------

class TestIngestAlwaysUpdatesSource:
    def test_last_ingested_at_written(self, tmp_path, dbs):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        add_source(corpus_conn, str(src_dir))
        corpus_conn.close()

        from src.config import load_config
        cfg = load_config(None)
        cancel = threading.Event()
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel)

        conn = open_corpus(corpus_path)
        row = conn.execute("SELECT last_ingested_at, file_count_ingested FROM sources").fetchone()
        assert row["last_ingested_at"] is not None
        assert row["file_count_ingested"] == 1
        conn.close()


# ---------------------------------------------------------------------------
# Per-source incremental — first run (no last_ingested_at): full walk
# ---------------------------------------------------------------------------

class TestIncrementalFirstRun:
    def test_full_walk_when_no_last_ingested_at(self, tmp_path, dbs):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        (src_dir / "b.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        add_source(corpus_conn, str(src_dir), incremental=True)
        corpus_conn.close()

        from src.config import load_config
        cfg = load_config(None)
        cancel = threading.Event()
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel)

        conn = open_corpus(corpus_path)
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert count == 2
        conn.close()


# ---------------------------------------------------------------------------
# Per-source incremental — second run with prior last_ingested_at
# ---------------------------------------------------------------------------

class TestIncrementalSecondRun:
    def test_old_files_skipped_new_files_processed(self, tmp_path, dbs):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        old_file = src_dir / "old.jpg"
        old_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        add_source(corpus_conn, str(src_dir), incremental=True)
        corpus_conn.close()

        from src.config import load_config
        cfg = load_config(None)
        cancel = threading.Event()

        # First run ingests old_file and writes last_ingested_at
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel)

        # Force old_file mtime to well before last_ingested_at
        past = time.time() - 3600
        os.utime(str(old_file), (past, past))

        # Add a new file (mtime = now, after last_ingested_at)
        new_file = src_dir / "new.jpg"
        new_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)

        conn = open_corpus(corpus_path)
        files_before = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()

        # Second run — incremental source — should skip old_file, process new_file
        cancel2 = threading.Event()
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel2)

        conn = open_corpus(corpus_path)
        files_after = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()

        assert files_before == 1
        assert files_after == 2

    def test_incremental_does_not_lose_existing_files(self, tmp_path, dbs):
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        for i in range(3):
            (src_dir / f"file{i}.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        add_source(corpus_conn, str(src_dir), incremental=True)
        corpus_conn.close()

        from src.config import load_config
        cfg = load_config(None)
        cancel = threading.Event()
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel)

        # Set all files old
        past = time.time() - 3600
        for f in src_dir.iterdir():
            os.utime(str(f), (past, past))

        cancel2 = threading.Event()
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel2)

        conn = open_corpus(corpus_path)
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        assert count == 3

    def test_non_incremental_source_always_full_walks(self, tmp_path, dbs):
        """A source with incremental=False does a full walk even after a prior run."""
        corpus_conn, kb_conn, corpus_path, kb_path = dbs
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        (src_dir / "old.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        add_source(corpus_conn, str(src_dir), incremental=False)
        corpus_conn.close()

        from src.config import load_config
        cfg = load_config(None)
        cancel = threading.Event()
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel)

        # Force mtime to the past
        past = time.time() - 3600
        os.utime(str(src_dir / "old.jpg"), (past, past))

        # Add a new file
        (src_dir / "new.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

        cancel2 = threading.Event()
        run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), cancel2)

        conn = open_corpus(corpus_path)
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        assert count == 2
