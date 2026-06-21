"""Unit tests for get_corpus_stats — in-memory SQLite, no filesystem."""
import pytest

from src.db.corpus import get_corpus_stats, open_corpus
from src.db.kb import open_kb


@pytest.fixture
def mem_dbs(tmp_path):
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    kb_conn = open_kb(tmp_path / "knowledge.db")
    yield corpus_conn, kb_conn
    corpus_conn.close()
    kb_conn.close()


def _add_source(conn, path="/photos"):
    conn.execute("INSERT OR IGNORE INTO sources (path, file_type) VALUES (?, 'all')", (path,))
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE path=?", (path,)).fetchone()["id"]


def _add_file(conn, source_id, path, file_type, sha256=None, canonical_id=None):
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, ?, ?, '.jpg', ?, 1000, 0.0)",
        (source_id, path, path.split("/")[-1], file_type),
    )
    conn.commit()
    fid = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    if sha256:
        conn.execute("UPDATE files SET sha256=? WHERE id=?", (sha256, fid))
    if canonical_id is not None:
        conn.execute("UPDATE files SET canonical_id=? WHERE id=?", (canonical_id, fid))
    conn.commit()
    return fid


class TestFileCounts:
    def test_total_file_count(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        _add_file(conn, sid, "/p/a.jpg", "images")
        _add_file(conn, sid, "/p/b.jpg", "images")
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["files"]["total"] == 2

    def test_by_type_breakdown(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        _add_file(conn, sid, "/p/a.jpg", "images")
        _add_file(conn, sid, "/p/b.mp4", "video")
        _add_file(conn, sid, "/p/c.mp3", "audio")
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["files"]["by_type"]["images"] == 1
        assert stats["files"]["by_type"]["video"] == 1
        assert stats["files"]["by_type"]["audio"] == 1

    def test_duplicate_count(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        fid1 = _add_file(conn, sid, "/p/a.jpg", "images")
        _add_file(conn, sid, "/p/b.jpg", "images", canonical_id=fid1)
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["files"]["duplicates"] == 1

    def test_sources_count(self, mem_dbs):
        conn, kb_conn = mem_dbs
        _add_source(conn, "/photos")
        _add_source(conn, "/videos")
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["files"]["sources"] == 2


class TestHashCoverage:
    def test_hash_covered_and_eligible(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        _add_file(conn, sid, "/p/a.jpg", "images", sha256="abc")
        _add_file(conn, sid, "/p/b.jpg", "images")
        stats = get_corpus_stats(conn, kb_conn)
        h = stats["stages"]["hash"]
        assert h["covered"] == 1
        assert h["eligible"] == 2
        assert h["total"] == 2

    def test_hash_eligible_pct_and_total_pct_present(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        _add_file(conn, sid, "/p/a.jpg", "images", sha256="abc")
        _add_file(conn, sid, "/p/b.jpg", "images")
        stats = get_corpus_stats(conn, kb_conn)
        h = stats["stages"]["hash"]
        assert "eligible_pct" in h
        assert "total_pct" in h
        assert h["eligible_pct"] == pytest.approx(50.0)
        assert h["total_pct"] == pytest.approx(50.0)


class TestDescribeCoverage:
    def test_describe_eligible_images_and_video_only(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        fid = _add_file(conn, sid, "/p/a.jpg", "images")
        _add_file(conn, sid, "/p/b.mp3", "audio")
        conn.execute(
            "INSERT INTO descriptions (file_id, model, pass1_status) VALUES (?, 'test', 'done')",
            (fid,),
        )
        conn.commit()
        stats = get_corpus_stats(conn, kb_conn)
        d = stats["stages"]["describe"]
        assert d["eligible"] == 1
        assert d["covered"] == 1
        assert d["total"] == 2
        assert d["eligible_pct"] == pytest.approx(100.0)
        assert d["total_pct"] == pytest.approx(50.0)


class TestTranscribeCoverage:
    def test_transcribe_eligible_audio_and_video_only(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        _add_file(conn, sid, "/p/a.jpg", "images")
        fid = _add_file(conn, sid, "/p/b.mp3", "audio")
        _add_file(conn, sid, "/p/c.mp4", "video")
        conn.execute(
            "INSERT INTO transcriptions (file_id, model, transcribe_status) VALUES (?, 'test', 'done')",
            (fid,),
        )
        conn.commit()
        stats = get_corpus_stats(conn, kb_conn)
        t = stats["stages"]["transcribe"]
        assert t["eligible"] == 2
        assert t["covered"] == 1
        assert t["total"] == 3
        assert t["eligible_pct"] == pytest.approx(50.0)


class TestRetagCoverage:
    def test_retag_covered_distinct_files(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        fid1 = _add_file(conn, sid, "/p/a.jpg", "images")
        fid2 = _add_file(conn, sid, "/p/b.jpg", "images")
        _add_file(conn, sid, "/p/c.jpg", "images")
        conn.execute(
            "INSERT INTO retag_output (file_id, tags_json, new_terms_proposed_json, model, retag_status)"
            " VALUES (?, '[]', '[]', 'test', 'done')",
            (fid1,),
        )
        conn.execute(
            "INSERT INTO retag_output (file_id, tags_json, new_terms_proposed_json, model, retag_status)"
            " VALUES (?, '[]', '[]', 'test', 'done')",
            (fid2,),
        )
        conn.commit()
        stats = get_corpus_stats(conn, kb_conn)
        r = stats["stages"]["retag"]
        assert r["covered"] == 2
        assert r["eligible"] == 3
        assert r["total"] == 3


class TestVocabularyCoverage:
    def test_vocab_terms_and_with_synonyms(self, mem_dbs):
        conn, kb_conn = mem_dbs
        kb_conn.execute(
            "INSERT INTO vocabulary (term, synonyms_json, source) VALUES ('cat', '[]', 'manual')"
        )
        kb_conn.execute(
            "INSERT INTO vocabulary (term, synonyms_json, source) VALUES ('dog', '[\"hound\"]', 'manual')"
        )
        kb_conn.commit()
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["vocabulary"]["terms"] == 2
        assert stats["vocabulary"]["with_synonyms"] == 1


class TestAestheticCoverage:
    def test_aesthetic_model_counts(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        fid = _add_file(conn, sid, "/p/a.jpg", "images")
        conn.execute(
            "INSERT INTO file_aesthetic (file_id, model_name, score, band)"
            " VALUES (?, 'nima_mobilenet', 0.7, 'good')",
            (fid,),
        )
        conn.commit()
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["aesthetic"]["nima_scored"] == 1
        assert stats["aesthetic"]["clip_scored"] == 0
        assert stats["aesthetic"]["combined_rank"] == 0


class TestAestheticCoverageMultiModel:
    def test_clip_and_combined_rank_counts(self, mem_dbs):
        conn, kb_conn = mem_dbs
        sid = _add_source(conn)
        fid = _add_file(conn, sid, "/p/a.jpg", "images")
        conn.execute(
            "INSERT INTO file_aesthetic (file_id, model_name, score, band) VALUES (?, 'nima_mobilenet', 0.7, 'good')",
            (fid,),
        )
        conn.execute(
            "INSERT INTO file_aesthetic (file_id, model_name, score, band) VALUES (?, 'clip_vit_b32', 0.5, 'average')",
            (fid,),
        )
        conn.execute(
            "INSERT INTO file_aesthetic (file_id, model_name, score, band) VALUES (?, 'combined_rank', 0.6, 'good')",
            (fid,),
        )
        conn.commit()
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["aesthetic"]["nima_scored"] == 1
        assert stats["aesthetic"]["clip_scored"] == 1
        assert stats["aesthetic"]["combined_rank"] == 1


class TestIngestStageStructure:
    def test_ingest_stage_has_required_keys(self, mem_dbs):
        conn, kb_conn = mem_dbs
        stats = get_corpus_stats(conn, kb_conn)
        ingest = stats["stages"]["ingest"]
        assert "files_processed" in ingest
        assert "last_run_at" in ingest
        assert "duration_seconds" in ingest

    def test_ingest_stage_reads_checkpoint(self, mem_dbs):
        conn, kb_conn = mem_dbs
        from src.db.corpus import update_pipeline_checkpoint
        update_pipeline_checkpoint(conn, stage="ingest", files_processed=50, duration_seconds=3.5)
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["stages"]["ingest"]["files_processed"] == 50

    def test_all_covered_stages_have_eligible_and_total_pct(self, mem_dbs):
        conn, kb_conn = mem_dbs
        stats = get_corpus_stats(conn, kb_conn)
        for stage_name in ("hash", "describe", "transcribe", "retag"):
            st = stats["stages"][stage_name]
            assert "eligible_pct" in st, f"{stage_name} missing eligible_pct"
            assert "total_pct" in st, f"{stage_name} missing total_pct"


class TestEmptyCorpus:
    def test_empty_corpus_no_error(self, mem_dbs):
        conn, kb_conn = mem_dbs
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["files"]["total"] == 0
        assert stats["stages"]["hash"]["covered"] == 0
        assert stats["stages"]["hash"]["eligible_pct"] == 0.0
        assert stats["stages"]["hash"]["total_pct"] == 0.0
        assert stats["stages"]["describe"]["eligible_pct"] == 0.0
        assert stats["stages"]["transcribe"]["eligible_pct"] == 0.0
        assert stats["stages"]["retag"]["eligible_pct"] == 0.0
        assert stats["vocabulary"]["terms"] == 0
        assert stats["aesthetic"]["nima_scored"] == 0

    def test_empty_corpus_by_type_is_empty_dict(self, mem_dbs):
        conn, kb_conn = mem_dbs
        stats = get_corpus_stats(conn, kb_conn)
        assert stats["files"]["by_type"] == {}
