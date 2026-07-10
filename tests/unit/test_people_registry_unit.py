"""Unit tests for people registry DB helpers (KB.Q4, KB.AJ2)."""
import struct

import numpy as np
import pytest

from src.db.corpus import open_corpus
from src.db.kb import (
    annotate_people_centroid_status,
    delete_person,
    get_centroid_quality,
    get_people_with_cluster_counts,
    get_voice_embeddings_for_person,
    merge_people,
    open_kb,
)


def _norm_blob(dim: int, seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dbs(tmp_path):
    kb_conn = open_kb(tmp_path / "knowledge.db")
    corpus_conn = open_corpus(tmp_path / "corpus.db")
    return kb_conn, corpus_conn


def _add_person(kb_conn, name: str) -> int:
    kb_conn.execute("INSERT INTO people (preferred_name) VALUES (?)", (name,))
    kb_conn.commit()
    return kb_conn.execute("SELECT id FROM people WHERE preferred_name = ?", (name,)).fetchone()[0]


def _add_voice_cluster(corpus_conn, person_id=None, label=None, member_count=3) -> int:
    centroid = struct.pack("f" * 256, *([0.0] * 256))
    corpus_conn.execute(
        "INSERT INTO voice_speaker_clusters (centroid, member_count, spread, person_id, label)"
        " VALUES (?, ?, ?, ?, ?)",
        (centroid, member_count, 0.1, person_id, label),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_face_cluster(corpus_conn, person_id=None, label=None, member_count=2) -> int:
    corpus_conn.execute(
        "INSERT INTO face_clusters (centroid, member_count, spread, person_id, label)"
        " VALUES (?, ?, ?, ?, ?)",
        (b"\x00" * 512, member_count, 0.05, person_id, label),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_file(corpus_conn, path="/src/a.jpg") -> int:
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/src', 'all', 1)"
    )
    corpus_conn.commit()
    source_id = corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename) VALUES (?, ?, ?)",
        (source_id, path, path.rsplit("/", 1)[-1]),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_face_region(corpus_conn, file_id, person_id, region_index=0, embedding=None) -> int:
    corpus_conn.execute(
        "INSERT INTO file_face_regions (file_id, region_index, bbox, embedding, person_id)"
        " VALUES (?, ?, '[0,0,1,1]', ?, ?)",
        (file_id, region_index, embedding or b"\x00" * 512, person_id),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_voice_segment(corpus_conn, file_id, person_id, segment_index=0, embedding=None) -> int:
    corpus_conn.execute(
        "INSERT INTO file_voice_segments"
        " (file_id, segment_index, start_ms, end_ms, speaker_label, embedding, person_id)"
        " VALUES (?, ?, 0, 1000, 'SPEAKER_00', ?, ?)",
        (file_id, segment_index, embedding, person_id),
    )
    corpus_conn.commit()
    return corpus_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# get_people_with_cluster_counts
# ---------------------------------------------------------------------------

def test_get_people_with_cluster_counts_returns_all(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Alice")
    _add_voice_cluster(corpus_conn, person_id=pid)
    _add_face_cluster(corpus_conn, person_id=pid)

    result = get_people_with_cluster_counts(kb_conn, corpus_conn)
    assert len(result) == 1
    assert result[0]["id"] == pid
    assert result[0]["preferred_name"] == "Alice"
    assert result[0]["voice_cluster_count"] == 1
    assert result[0]["face_cluster_count"] == 1


def test_get_people_with_cluster_counts_empty(dbs):
    kb_conn, corpus_conn = dbs
    result = get_people_with_cluster_counts(kb_conn, corpus_conn)
    assert result == []


def test_get_people_with_cluster_counts_unassigned_clusters_not_counted(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Bob")
    _add_voice_cluster(corpus_conn, person_id=None)  # unassigned
    _add_face_cluster(corpus_conn, person_id=pid)    # assigned

    result = get_people_with_cluster_counts(kb_conn, corpus_conn)
    assert result[0]["voice_cluster_count"] == 0
    assert result[0]["face_cluster_count"] == 1


# ---------------------------------------------------------------------------
# delete_person
# ---------------------------------------------------------------------------

def test_delete_person_success(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Charlie")
    delete_person(kb_conn, corpus_conn, pid)
    row = kb_conn.execute("SELECT id FROM people WHERE id = ?", (pid,)).fetchone()
    assert row is None


def test_delete_person_blocked_by_voice_cluster(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Diana")
    _add_voice_cluster(corpus_conn, person_id=pid)
    with pytest.raises(ValueError, match="voice cluster"):
        delete_person(kb_conn, corpus_conn, pid)


def test_delete_person_blocked_by_face_cluster(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Eve")
    _add_face_cluster(corpus_conn, person_id=pid)
    with pytest.raises(ValueError, match="face cluster"):
        delete_person(kb_conn, corpus_conn, pid)


def test_delete_person_not_found_raises_key_error(dbs):
    kb_conn, corpus_conn = dbs
    with pytest.raises(KeyError):
        delete_person(kb_conn, corpus_conn, 9999)


# ---------------------------------------------------------------------------
# merge_people
# ---------------------------------------------------------------------------

def test_merge_people_reassigns_clusters(dbs):
    kb_conn, corpus_conn = dbs
    keep_id = _add_person(kb_conn, "Frank")
    drop_id = _add_person(kb_conn, "Francis")
    vc_id = _add_voice_cluster(corpus_conn, person_id=drop_id, label="Francis")
    fc_id = _add_face_cluster(corpus_conn, person_id=drop_id, label="Francis")

    merge_people(kb_conn, corpus_conn, keep_id, drop_id)

    vc = corpus_conn.execute("SELECT person_id, label FROM voice_speaker_clusters WHERE id=?", (vc_id,)).fetchone()
    fc = corpus_conn.execute("SELECT person_id, label FROM face_clusters WHERE id=?", (fc_id,)).fetchone()
    assert vc["person_id"] == keep_id
    assert vc["label"] == "Frank"
    assert fc["person_id"] == keep_id
    assert fc["label"] == "Frank"


def test_merge_people_deletes_source_person(dbs):
    kb_conn, corpus_conn = dbs
    keep_id = _add_person(kb_conn, "Grace")
    drop_id = _add_person(kb_conn, "Greta")

    merge_people(kb_conn, corpus_conn, keep_id, drop_id)

    row = kb_conn.execute("SELECT id FROM people WHERE id = ?", (drop_id,)).fetchone()
    assert row is None


def test_merge_people_same_id_raises_value_error(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Hank")
    with pytest.raises(ValueError, match="differ"):
        merge_people(kb_conn, corpus_conn, pid, pid)


def test_merge_people_missing_keep_raises_key_error(dbs):
    kb_conn, corpus_conn = dbs
    drop_id = _add_person(kb_conn, "Ivan")
    with pytest.raises(KeyError):
        merge_people(kb_conn, corpus_conn, 9999, drop_id)


def test_merge_people_missing_from_raises_key_error(dbs):
    kb_conn, corpus_conn = dbs
    keep_id = _add_person(kb_conn, "Jane")
    with pytest.raises(KeyError):
        merge_people(kb_conn, corpus_conn, keep_id, 9999)


def test_merge_people_updates_face_centroid(dbs):
    kb_conn, corpus_conn = dbs
    keep_id = _add_person(kb_conn, "Karl")
    drop_id = _add_person(kb_conn, "Karla")
    _add_face_cluster(corpus_conn, person_id=drop_id, label="Karla", member_count=4)

    merge_people(kb_conn, corpus_conn, keep_id, drop_id)

    row = kb_conn.execute(
        "SELECT face_centroid, face_samples FROM people WHERE id=?", (keep_id,)
    ).fetchone()
    assert row["face_centroid"] is not None
    assert row["face_samples"] == 4


# ---------------------------------------------------------------------------
# get_voice_embeddings_for_person (KB.AJ2)
# ---------------------------------------------------------------------------

def test_get_voice_embeddings_for_person_returns_assigned_only(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Liam")
    fid = _add_file(corpus_conn, "/src/a.wav")
    e1 = _norm_blob(256, 1)
    _add_voice_segment(corpus_conn, fid, pid, segment_index=0, embedding=e1)
    _add_voice_segment(corpus_conn, fid, None, segment_index=1, embedding=_norm_blob(256, 2))

    result = get_voice_embeddings_for_person(kb_conn, corpus_conn, pid)
    assert result == [e1]


def test_get_voice_embeddings_for_person_excludes_null_embeddings(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Mona")
    fid = _add_file(corpus_conn, "/src/b.wav")
    _add_voice_segment(corpus_conn, fid, pid, segment_index=0, embedding=None)

    assert get_voice_embeddings_for_person(kb_conn, corpus_conn, pid) == []


# ---------------------------------------------------------------------------
# get_people_with_cluster_counts — mean similarity fields (KB.AJ2)
# ---------------------------------------------------------------------------

def test_cluster_counts_includes_voice_samples(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Nora")
    kb_conn.execute("UPDATE people SET voice_samples = 7 WHERE id = ?", (pid,))
    kb_conn.commit()

    result = get_people_with_cluster_counts(kb_conn, corpus_conn)
    assert result[0]["voice_samples"] == 7


def test_cluster_counts_face_mean_similarity_computed_from_assigned_regions(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Omar")
    centroid = _norm_blob(512, 0)
    kb_conn.execute(
        "UPDATE people SET face_centroid = ?, face_samples = 1 WHERE id = ?", (centroid, pid)
    )
    kb_conn.commit()
    _add_face_cluster(corpus_conn, person_id=pid)
    fid = _add_file(corpus_conn)
    _add_face_region(corpus_conn, fid, pid, embedding=centroid)

    result = get_people_with_cluster_counts(kb_conn, corpus_conn)
    assert result[0]["face_mean_similarity"] == pytest.approx(1.0, abs=1e-5)


def test_cluster_counts_mean_similarity_none_without_clusters(dbs):
    kb_conn, corpus_conn = dbs
    _add_person(kb_conn, "Priya")
    result = get_people_with_cluster_counts(kb_conn, corpus_conn)
    assert result[0]["face_mean_similarity"] is None
    assert result[0]["voice_mean_similarity"] is None


# ---------------------------------------------------------------------------
# annotate_people_centroid_status / get_centroid_quality (KB.AJ2)
# ---------------------------------------------------------------------------

def test_annotate_people_centroid_status_adds_both_kinds(dbs):
    kb_conn, corpus_conn = dbs
    _add_person(kb_conn, "Quinn")
    people = get_people_with_cluster_counts(kb_conn, corpus_conn)
    annotated = annotate_people_centroid_status(
        people,
        face_min_clusters=5, face_min_similarity=0.7,
        voice_min_clusters=5, voice_min_similarity=0.7,
    )
    assert annotated[0]["face_status"] == "too_few_samples"
    assert annotated[0]["voice_status"] == "too_few_samples"


def test_get_centroid_quality_all_reliable_false_when_no_tracked_people(dbs):
    kb_conn, corpus_conn = dbs
    _add_person(kb_conn, "Rosa")
    statuses, all_reliable = get_centroid_quality(
        kb_conn, corpus_conn, "face", min_clusters=5, min_similarity=0.7
    )
    assert statuses[0]["status"] == "too_few_samples"
    assert all_reliable is False


def test_get_centroid_quality_all_reliable_true_when_thresholds_met(dbs):
    kb_conn, corpus_conn = dbs
    pid = _add_person(kb_conn, "Sam")
    centroid = _norm_blob(512, 5)
    kb_conn.execute(
        "UPDATE people SET face_centroid = ?, face_samples = 5 WHERE id = ?", (centroid, pid)
    )
    kb_conn.commit()
    for i in range(5):
        _add_face_cluster(corpus_conn, person_id=pid)
    fid = _add_file(corpus_conn)
    _add_face_region(corpus_conn, fid, pid, embedding=centroid)

    statuses, all_reliable = get_centroid_quality(
        kb_conn, corpus_conn, "face", min_clusters=5, min_similarity=0.7
    )
    assert statuses[0]["status"] == "reliable"
    assert all_reliable is True
