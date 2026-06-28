"""Integration tests for KB.P15 Face Stage — mocked detect/embed, real SQLite."""
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.corpus import open_corpus
from src.db.kb import open_kb


def _fake_embedding(seed: int = 0) -> bytes:
    import numpy as np
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    v = v / float(np.linalg.norm(v))
    return v.tobytes()


def _fake_detect(img_bytes, detection_model_path):
    """Return one synthetic face bbox per call."""
    return [{"bbox": [10.0, 10.0, 50.0, 50.0], "score": 0.99}]


def _fake_embed(img_bytes, bbox, embedding_model_path, *, _seed=0):
    return _fake_embedding(seed=_seed)


def _make_config(tmp_path, *, similarity_threshold=0.55, unknown_clusters=False):
    from src.config import Config
    det_model = tmp_path / "det.onnx"
    emb_model = tmp_path / "emb.onnx"
    det_model.write_bytes(b"fake")
    emb_model.write_bytes(b"fake")
    return Config(
        face_detection_model=str(det_model),
        face_embedding_model=str(emb_model),
        face_similarity_threshold=similarity_threshold,
        unknown_face_clusters=unknown_clusters,
    )


def _ensure_source(corpus_conn) -> int:
    row = corpus_conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
    if row:
        return row["id"]
    return corpus_conn.execute(
        "INSERT INTO sources(path, file_type, recursive) VALUES ('/src', 'all', 1)"
    ).lastrowid


def _ingest_image(corpus_conn, file_id: int, path: str) -> None:
    source_id = _ensure_source(corpus_conn)
    corpus_conn.execute(
        "INSERT OR IGNORE INTO files(id, source_id, path, filename, ext, file_type, file_size, mtime) "
        "VALUES (?, ?, ?, ?, '.jpg', 'images', 1000, 0.0)",
        (file_id, source_id, path, Path(path).name),
    )
    corpus_conn.commit()


@pytest.fixture
def face_dbs(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    return corpus_conn, kb_conn, corpus_path, kb_path, tmp_path


class TestRunFaceIntegration:
    def test_happy_path_no_people(self, face_dbs, sample_image):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.face import run_face

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = face_dbs
        _ingest_image(corpus_conn, 1, str(sample_image))
        corpus_conn.close()
        kb_conn.close()

        config = _make_config(tmp_path)
        with (
            patch("src.stages.face.detect_faces", side_effect=_fake_detect),
            patch("src.stages.face.embed_face", return_value=_fake_embedding(0)),
        ):
            result = run_face(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["files_processed"] == 1
        assert result["faces_detected"] == 1
        assert result["faces_matched"] == 0
        assert result["errors"] == 0

        corpus_conn2 = open_corpus(corpus_path)
        rows = corpus_conn2.execute("SELECT * FROM file_face_regions").fetchall()
        corpus_conn2.close()
        assert len(rows) == 1
        assert rows[0]["person_id"] is None

    def test_face_matches_known_person(self, face_dbs, sample_image):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.face import run_face

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = face_dbs
        _ingest_image(corpus_conn, 1, str(sample_image))
        corpus_conn.close()

        emb = _fake_embedding(42)
        kb_conn.execute(
            "INSERT INTO people(id, preferred_name, face_centroid, face_samples) VALUES (1, 'Alice', ?, 3)",
            (emb,),
        )
        kb_conn.commit()
        kb_conn.close()

        config = _make_config(tmp_path, similarity_threshold=0.10)
        with (
            patch("src.stages.face.detect_faces", side_effect=_fake_detect),
            patch("src.stages.face.embed_face", return_value=emb),
        ):
            result = run_face(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["faces_matched"] == 1

        corpus_conn2 = open_corpus(corpus_path)
        row = corpus_conn2.execute("SELECT person_id FROM file_face_regions").fetchone()
        corpus_conn2.close()
        assert row["person_id"] == 1

    def test_resume_on_restart_no_duplicates(self, face_dbs, sample_images):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.face import run_face

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = face_dbs
        for i, img in enumerate(sample_images):
            _ingest_image(corpus_conn, i + 1, str(img))
        corpus_conn.close()
        kb_conn.close()

        config = _make_config(tmp_path)
        cancel1 = threading.Event()
        call_count = 0

        def detecting_with_cancel(img_bytes, det_model):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                cancel1.set()
            return _fake_detect(img_bytes, det_model)

        with (
            patch("src.stages.face.detect_faces", side_effect=detecting_with_cancel),
            patch("src.stages.face.embed_face", return_value=_fake_embedding(0)),
        ):
            run_face(corpus_path, kb_path, config, NullProgressReporter(), cancel1)

        corpus_conn2 = open_corpus(corpus_path)
        corpus_conn2.execute("SELECT COUNT(*) FROM file_face_regions").fetchone()[0]
        corpus_conn2.close()

        # Second full run
        with (
            patch("src.stages.face.detect_faces", side_effect=_fake_detect),
            patch("src.stages.face.embed_face", return_value=_fake_embedding(0)),
        ):
            run_face(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        corpus_conn3 = open_corpus(corpus_path)
        total = corpus_conn3.execute("SELECT COUNT(*) FROM file_face_regions").fetchone()[0]
        corpus_conn3.close()
        assert total == len(sample_images)

    def test_detection_error_increments_error_count(self, face_dbs, sample_image):
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.face import run_face

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = face_dbs
        _ingest_image(corpus_conn, 1, str(sample_image))
        corpus_conn.close()
        kb_conn.close()

        config = _make_config(tmp_path)
        with patch("src.stages.face.detect_faces", side_effect=RuntimeError("bad file")):
            result = run_face(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())

        assert result["errors"] == 1
        assert result["files_processed"] == 0

    def test_missing_model_raises_model_load_error(self, face_dbs):
        from src.config import Config
        from src.pipeline.cancel import make_cancel_event
        from src.pipeline.progress import NullProgressReporter
        from src.stages.face import ModelLoadError, run_face

        corpus_conn, kb_conn, corpus_path, kb_path, tmp_path = face_dbs
        corpus_conn.close()
        kb_conn.close()

        config = Config(face_detection_model="", face_embedding_model="")
        with pytest.raises(ModelLoadError):
            run_face(corpus_path, kb_path, config, NullProgressReporter(), make_cancel_event())
