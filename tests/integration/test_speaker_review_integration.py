"""Integration tests for KB.P18 Speaker Review UI — API routes and UI routes."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.api.deps import resolve_kb
from src.db.corpus import open_corpus, upsert_voice_speaker_cluster
from src.db.kb import open_kb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blob(seed: int = 0, dim: int = 256) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


def _make_client(corpus_path: Path, kb_path: Path) -> TestClient:
    def _override():
        return corpus_path, kb_path
    app.dependency_overrides[resolve_kb] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(resolve_kb, None)


def _seed_source(corpus_conn):
    row = corpus_conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
    if row:
        return row["id"]
    return corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid


def _seed_file(corpus_conn, path: str = "/audio/clip.wav", file_type: str = "audio") -> int:
    sid = _seed_source(corpus_conn)
    cur = corpus_conn.execute(
        "INSERT INTO files(source_id, path, filename, ext, file_type, file_size, mtime) "
        "VALUES (?, ?, ?, '.wav', ?, 1000, 0.0)",
        (sid, path, Path(path).name, file_type),
    )
    corpus_conn.commit()
    return cur.lastrowid


def _seed_cluster(corpus_conn, person_id=None, seed=0, member_count=3) -> int:
    centroid = _blob(seed)
    cid = upsert_voice_speaker_cluster(corpus_conn, None, centroid, member_count, 0.1)
    if person_id is not None:
        corpus_conn.execute(
            "UPDATE voice_speaker_clusters SET person_id=?, label='Assigned' WHERE id=?",
            (person_id, cid),
        )
    corpus_conn.commit()
    return cid


def _seed_segment(corpus_conn, file_id, cluster_id, seg_index=0):
    corpus_conn.execute(
        "INSERT OR IGNORE INTO file_voice_segments"
        " (file_id, segment_index, start_ms, end_ms, speaker_label, embedding, cluster_id)"
        " VALUES (?, ?, 0, 3000, 'SPEAKER_00', ?, ?)",
        (file_id, seg_index, _blob(seg_index), cluster_id),
    )
    corpus_conn.commit()


def _setup(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    return corpus_conn, kb_conn, corpus_path, kb_path


# ---------------------------------------------------------------------------
# API — pending
# ---------------------------------------------------------------------------

class TestSpeakersPendingAPI:
    def test_returns_unassigned_clusters(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        fid = _seed_file(corpus_conn)
        cid = _seed_cluster(corpus_conn)
        _seed_segment(corpus_conn, fid, cid)
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/api/review/speakers/pending")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == cid

    def test_excludes_assigned(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')")
        kb_conn.commit()
        pid = kb_conn.execute("SELECT id FROM people LIMIT 1").fetchone()["id"]
        kb_conn.close()
        _seed_cluster(corpus_conn, person_id=pid)
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/api/review/speakers/pending")
        assert r.json()["total"] == 0

    def test_includes_people_list(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')")
        kb_conn.commit()
        kb_conn.close()
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/api/review/speakers/pending")
        assert any(p["preferred_name"] == "Alice" for p in r.json()["people"])


# ---------------------------------------------------------------------------
# API — assign existing person
# ---------------------------------------------------------------------------

class TestSpeakersDecideAPI:
    def test_assign_existing_person(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')")
        kb_conn.commit()
        pid = kb_conn.execute("SELECT id FROM people LIMIT 1").fetchone()["id"]
        fid = _seed_file(corpus_conn)
        cid = _seed_cluster(corpus_conn)
        _seed_segment(corpus_conn, fid, cid)
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.post("/api/review/speakers/decide", json={
            "cluster_id": cid, "action": "assign", "person_id": pid,
        })
        assert r.status_code == 200

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute(
            "SELECT person_id, label FROM voice_speaker_clusters WHERE id=?", (cid,)
        ).fetchone()
        corpus_conn2.close()
        assert row["person_id"] == pid
        assert row["label"] == "Alice"

    def test_assign_creates_new_person(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        cid = _seed_cluster(corpus_conn)
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.post("/api/review/speakers/decide", json={
            "cluster_id": cid, "action": "assign", "new_name": "Bob",
        })
        assert r.status_code == 200

        kb_conn2 = open_kb(kb_path)
        p = kb_conn2.execute("SELECT preferred_name FROM people WHERE preferred_name='Bob'").fetchone()
        kb_conn2.close()
        assert p is not None

    def test_assign_updates_voice_centroid(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Carol')")
        kb_conn.commit()
        pid = kb_conn.execute("SELECT id FROM people LIMIT 1").fetchone()["id"]
        kb_conn.close()
        cid = _seed_cluster(corpus_conn)
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        client.post("/api/review/speakers/decide", json={
            "cluster_id": cid, "action": "assign", "person_id": pid,
        })

        kb_conn2 = open_kb(kb_path)
        row = kb_conn2.execute("SELECT voice_centroid, voice_samples FROM people WHERE id=?", (pid,)).fetchone()
        kb_conn2.close()
        assert row["voice_centroid"] is not None
        assert row["voice_samples"] > 0

    def test_assign_no_name_returns_400(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        cid = _seed_cluster(corpus_conn)
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.post("/api/review/speakers/decide", json={
            "cluster_id": cid, "action": "assign",
        })
        assert r.status_code == 400

    def test_unknown_action_returns_400(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        cid = _seed_cluster(corpus_conn)
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.post("/api/review/speakers/decide", json={
            "cluster_id": cid, "action": "ignore",
        })
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# API — unassign
# ---------------------------------------------------------------------------

class TestSpeakersUnassignAPI:
    def test_unassign_returns_cluster_to_pending(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')")
        kb_conn.commit()
        pid = kb_conn.execute("SELECT id FROM people LIMIT 1").fetchone()["id"]
        kb_conn.close()
        cid = _seed_cluster(corpus_conn, person_id=pid)
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.delete(f"/api/review/speakers/decisions/{cid}")
        assert r.status_code == 200

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute(
            "SELECT person_id FROM voice_speaker_clusters WHERE id=?", (cid,)
        ).fetchone()
        corpus_conn2.close()
        assert row["person_id"] is None


# ---------------------------------------------------------------------------
# Audio clip endpoint
# ---------------------------------------------------------------------------

class TestSpeakerClipAPI:
    def test_clip_returns_audio_wav(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        fid = _seed_file(corpus_conn, path="/audio/meet.wav")
        corpus_conn.close()
        kb_conn.close()

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = b"RIFF\x00\x00\x00\x00WAVEfmt "

        client = _make_client(corpus_path, kb_path)
        with patch("src.api.review.subprocess.run", return_value=fake_result):
            r = client.get(f"/api/review/speakers/clip?file_id={fid}&start_ms=0&end_ms=3000")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/wav")

    def test_clip_file_not_found_returns_404(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/api/review/speakers/clip?file_id=999&start_ms=0&end_ms=2000")
        assert r.status_code == 404

    def test_clip_ffmpeg_error_returns_500(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        fid = _seed_file(corpus_conn)
        corpus_conn.close()
        kb_conn.close()

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = b""

        client = _make_client(corpus_path, kb_path)
        with patch("src.api.review.subprocess.run", return_value=fake_result):
            r = client.get(f"/api/review/speakers/clip?file_id={fid}&start_ms=0&end_ms=2000")
        assert r.status_code == 500


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

class TestSpeakerReviewUIRoutes:
    def test_page_route_returns_200(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        corpus_conn.close()
        kb_conn.close()
        client = _make_client(corpus_path, kb_path)
        r = client.get("/review/speakers?kb=test")
        assert r.status_code == 200

    def test_queue_partial_contains_audio_element(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        fid = _seed_file(corpus_conn, "/audio/clip.wav")
        cid = _seed_cluster(corpus_conn)
        _seed_segment(corpus_conn, fid, cid)
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/review/speakers/partials/queue?kb=test")
        assert r.status_code == 200
        assert b"<audio" in r.content

    def test_decisions_partial_shows_person_name(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')")
        kb_conn.commit()
        pid = kb_conn.execute("SELECT id FROM people LIMIT 1").fetchone()["id"]
        kb_conn.close()
        cid = _seed_cluster(corpus_conn, person_id=pid)
        corpus_conn.execute(
            "UPDATE voice_speaker_clusters SET label='Alice' WHERE id=?", (cid,)
        )
        corpus_conn.commit()
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/review/speakers/partials/decisions?kb=test")
        assert r.status_code == 200
        assert b"Alice" in r.content


# ---------------------------------------------------------------------------
# Centroid quality (KB.AJ2)
# ---------------------------------------------------------------------------

class TestSpeakerCentroidQuality:
    def test_queue_ranks_pending_clusters_by_similarity(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        near_id = _seed_cluster(corpus_conn, seed=0)
        far_id = _seed_cluster(corpus_conn, seed=9)
        kb_conn.execute(
            "INSERT INTO people (preferred_name, voice_centroid, voice_samples) VALUES (?, ?, 1)",
            ("Dora", _blob(0)),
        )
        kb_conn.commit()
        corpus_conn.close()
        kb_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/review/speakers/partials/queue?kb=test")
        assert r.status_code == 200
        text = r.text
        assert "Suggested: <strong>Dora</strong>" in text
        assert text.index(f"Cluster #{near_id}") < text.index(f"Cluster #{far_id}")

    def test_quality_partial_shows_reliable_banner_when_thresholds_met(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        centroid = _blob(1)
        pid = kb_conn.execute(
            "INSERT INTO people (preferred_name, voice_centroid, voice_samples) VALUES ('Eli', ?, 1)",
            (centroid,),
        ).lastrowid
        kb_conn.commit()
        kb_conn.close()
        fid = _seed_file(corpus_conn)
        for i in range(5):
            _seed_cluster(corpus_conn, person_id=pid, seed=1)
        _seed_segment(corpus_conn, fid, None, seg_index=0)
        corpus_conn.execute(
            "UPDATE file_voice_segments SET person_id=?, embedding=? WHERE file_id=? AND segment_index=0",
            (pid, centroid, fid),
        )
        corpus_conn.commit()
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/review/speakers/partials/quality?kb=test")
        assert r.status_code == 200
        assert b"Centroids reliable" in r.content
        assert b"Reliable" in r.content

    def test_quality_partial_no_banner_when_needs_more_samples(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        pid = kb_conn.execute(
            "INSERT INTO people (preferred_name) VALUES ('Finn')"
        ).lastrowid
        kb_conn.commit()
        kb_conn.close()
        _seed_cluster(corpus_conn, person_id=pid)
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.get("/review/speakers/partials/quality?kb=test")
        assert r.status_code == 200
        assert b"Centroids reliable" not in r.content
        assert b"Needs More Samples" in r.content

    def test_nav_link_present_in_base(self):
        base = Path("D:/Python_Environments/kb-builder/templates/base.html").read_text()
        assert "/knowledge/people/speakers" in base

    def test_ui_decide_assign_existing(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Dave')")
        kb_conn.commit()
        pid = kb_conn.execute("SELECT id FROM people LIMIT 1").fetchone()["id"]
        kb_conn.close()
        cid = _seed_cluster(corpus_conn)
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.post(
            "/review/speakers/decide?kb=test",
            data={"cluster_id": cid, "action": "assign", "person_id": str(pid), "new_name": ""},
        )
        assert r.status_code == 200

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute(
            "SELECT person_id FROM voice_speaker_clusters WHERE id=?", (cid,)
        ).fetchone()
        corpus_conn2.close()
        assert row["person_id"] == pid

    def test_ui_unassign(self, tmp_path):
        corpus_conn, kb_conn, corpus_path, kb_path = _setup(tmp_path)
        kb_conn.execute("INSERT INTO people(preferred_name) VALUES ('Eve')")
        kb_conn.commit()
        pid = kb_conn.execute("SELECT id FROM people LIMIT 1").fetchone()["id"]
        kb_conn.close()
        cid = _seed_cluster(corpus_conn, person_id=pid)
        corpus_conn.close()

        client = _make_client(corpus_path, kb_path)
        r = client.delete(f"/review/speakers/decisions/{cid}?kb=test")
        assert r.status_code == 200

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute(
            "SELECT person_id FROM voice_speaker_clusters WHERE id=?", (cid,)
        ).fetchone()
        corpus_conn2.close()
        assert row["person_id"] is None
