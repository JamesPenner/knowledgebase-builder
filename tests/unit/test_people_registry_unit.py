"""Unit tests for people registry DB helpers (KB.Q4)."""
import struct

import pytest

from src.db.corpus import open_corpus
from src.db.kb import (
    delete_person,
    get_people_with_cluster_counts,
    merge_people,
    open_kb,
)


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
