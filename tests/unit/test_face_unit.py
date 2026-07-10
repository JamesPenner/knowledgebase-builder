"""Unit tests for KB.P15 Face Stage — no ONNX inference, no filesystem."""
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Helpers: minimal in-memory schemas
# ---------------------------------------------------------------------------

def _make_corpus_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE files (
            id        INTEGER PRIMARY KEY,
            path      TEXT    NOT NULL,
            filename  TEXT,
            file_type TEXT    NOT NULL DEFAULT 'images',
            canonical_id INTEGER
        );
        CREATE TABLE file_face_regions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id       INTEGER NOT NULL,
            region_index  INTEGER NOT NULL,
            source        TEXT NOT NULL DEFAULT 'ml',
            bbox          TEXT,
            embedding     BLOB NOT NULL,
            person_id     INTEGER,
            similarity    REAL,
            detected_at   DATETIME DEFAULT (datetime('now')),
            UNIQUE(file_id, source, region_index)
        );
        CREATE TABLE face_clusters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            centroid     BLOB NOT NULL,
            member_count INTEGER NOT NULL DEFAULT 0,
            spread       REAL,
            created_at   DATETIME DEFAULT (datetime('now'))
        );
        CREATE TABLE face_cluster_members (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id   INTEGER NOT NULL,
            file_id      INTEGER NOT NULL,
            region_index INTEGER NOT NULL,
            similarity   REAL,
            UNIQUE(file_id, region_index)
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
            notes          TEXT,
            family         INTEGER NOT NULL DEFAULT 0,
            voice_centroid BLOB,
            voice_samples  INTEGER NOT NULL DEFAULT 0,
            face_centroid  BLOB,
            face_samples   INTEGER NOT NULL DEFAULT 0,
            created_at     DATETIME DEFAULT (datetime('now'))
        );
        CREATE TABLE people_names (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id        INTEGER NOT NULL,
            name             TEXT    NOT NULL,
            is_metadata_form INTEGER NOT NULL DEFAULT 0,
            UNIQUE(person_id, name)
        );
        CREATE TABLE life_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id  INTEGER NOT NULL,
            event_type TEXT    NOT NULL,
            event_date TEXT,
            partner_id INTEGER,
            notes      TEXT,
            added_at   DATETIME DEFAULT (datetime('now'))
        );
    """)
    return conn


def _fake_embedding(value: float = 1.0) -> bytes:
    """Return a normalised 512D float32 embedding as bytes."""
    import numpy as np
    v = np.ones(512, dtype=np.float32) * value
    v = v / float(np.linalg.norm(v))
    return v.tobytes()


def _insert_file(conn, file_id=1, path="img.jpg", file_type="images"):
    conn.execute(
        "INSERT INTO files(id, path, file_type) VALUES (?, ?, ?)",
        (file_id, path, file_type),
    )


# ---------------------------------------------------------------------------
# Config: 5 new fields
# ---------------------------------------------------------------------------

class TestFaceConfig:
    def test_face_detection_model_default_empty(self):
        from src.config import Config
        assert Config().face_detection_model == ""

    def test_face_embedding_model_default_empty(self):
        from src.config import Config
        assert Config().face_embedding_model == ""

    def test_face_similarity_threshold_default(self):
        from src.config import Config
        assert Config().face_similarity_threshold == pytest.approx(0.55)

    def test_unknown_face_clusters_default_false(self):
        from src.config import Config
        assert Config().unknown_face_clusters is False

    def test_export_biometric_default_false(self):
        from src.config import Config
        assert Config().export_biometric is False

    def test_load_config_face_detection(self, tmp_path):
        from src.config import load_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("models:\n  face_detection: /path/det.onnx\n")
        assert load_config(cfg).face_detection_model == "/path/det.onnx"

    def test_load_config_face_embedding(self, tmp_path):
        from src.config import load_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("models:\n  face_embedding: /path/emb.onnx\n")
        assert load_config(cfg).face_embedding_model == "/path/emb.onnx"

    def test_load_config_face_similarity_threshold(self, tmp_path):
        from src.config import load_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("thresholds:\n  face_similarity_threshold: 0.70\n")
        assert load_config(cfg).face_similarity_threshold == pytest.approx(0.70)

    def test_load_config_unknown_face_clusters(self, tmp_path):
        from src.config import load_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("write_back:\n  unknown_face_clusters: true\n")
        assert load_config(cfg).unknown_face_clusters is True

    def test_load_config_export_biometric(self, tmp_path):
        from src.config import load_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("write_back:\n  export_biometric: true\n")
        assert load_config(cfg).export_biometric is True


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_embeddings_is_one(self):
        from src.stages.face import cosine_similarity
        emb = _fake_embedding(1.0)
        assert cosine_similarity(emb, emb) == pytest.approx(1.0, abs=1e-5)

    def test_opposite_embeddings_is_neg_one(self):
        import numpy as np
        from src.stages.face import cosine_similarity
        v1 = np.ones(512, dtype=np.float32)
        v1 = v1 / float(np.linalg.norm(v1))
        v2 = -v1
        assert cosine_similarity(v1.tobytes(), v2.tobytes()) == pytest.approx(-1.0, abs=1e-5)

    def test_orthogonal_embeddings_is_zero(self):
        import numpy as np
        from src.stages.face import cosine_similarity
        v1 = np.zeros(512, dtype=np.float32)
        v1[0] = 1.0
        v2 = np.zeros(512, dtype=np.float32)
        v2[1] = 1.0
        assert cosine_similarity(v1.tobytes(), v2.tobytes()) == pytest.approx(0.0, abs=1e-5)

    def test_zero_vector_returns_zero(self):
        import numpy as np
        from src.stages.face import cosine_similarity
        zero = np.zeros(512, dtype=np.float32).tobytes()
        emb = _fake_embedding()
        assert cosine_similarity(zero, emb) == 0.0


# ---------------------------------------------------------------------------
# update_centroid
# ---------------------------------------------------------------------------

class TestUpdateCentroid:
    def test_first_sample_returns_normalised_embedding(self):
        import numpy as np
        from src.stages.face import update_centroid
        emb = _fake_embedding(2.0)
        blob, count = update_centroid(None, 0, emb)
        assert count == 1
        v = np.frombuffer(blob, dtype=np.float32)
        assert float(np.linalg.norm(v)) == pytest.approx(1.0, abs=1e-5)

    def test_two_samples_incremental_mean(self):
        import numpy as np
        from src.stages.face import update_centroid
        emb1 = _fake_embedding(1.0)
        emb2 = _fake_embedding(2.0)
        blob1, count1 = update_centroid(None, 0, emb1)
        blob2, count2 = update_centroid(blob1, count1, emb2)
        assert count2 == 2
        v = np.frombuffer(blob2, dtype=np.float32)
        # Centroid should be normalised
        assert float(np.linalg.norm(v)) == pytest.approx(1.0, abs=1e-5)

    def test_count_increments(self):
        from src.stages.face import update_centroid
        emb = _fake_embedding()
        blob, count = update_centroid(None, 0, emb)
        _, count2 = update_centroid(blob, count, emb)
        assert count2 == 2


# ---------------------------------------------------------------------------
# get_files_without_face_regions
# ---------------------------------------------------------------------------

class TestGetFilesWithoutFaceRegions:
    def test_returns_image_with_no_region(self):
        from src.db.corpus import get_files_without_face_regions
        conn = _make_corpus_db()
        _insert_file(conn, 1, "a.jpg", "images")
        rows = get_files_without_face_regions(conn)
        assert len(rows) == 1
        assert rows[0]["path"] == "a.jpg"

    def test_excludes_file_with_region(self):
        from src.db.corpus import get_files_without_face_regions, upsert_face_region
        conn = _make_corpus_db()
        _insert_file(conn, 1, "a.jpg", "images")
        upsert_face_region(conn, 1, 0, None, _fake_embedding(), None, None)
        rows = get_files_without_face_regions(conn)
        assert len(rows) == 0

    def test_excludes_video_files(self):
        from src.db.corpus import get_files_without_face_regions
        conn = _make_corpus_db()
        _insert_file(conn, 1, "clip.mp4", "video")
        rows = get_files_without_face_regions(conn)
        assert len(rows) == 0

    def test_mixed_returns_only_unprocessed_images(self):
        from src.db.corpus import get_files_without_face_regions, upsert_face_region
        conn = _make_corpus_db()
        _insert_file(conn, 1, "a.jpg", "images")
        _insert_file(conn, 2, "b.jpg", "images")
        _insert_file(conn, 3, "clip.mp4", "video")
        upsert_face_region(conn, 1, 0, None, _fake_embedding(), None, None)
        rows = get_files_without_face_regions(conn)
        assert len(rows) == 1
        assert rows[0]["path"] == "b.jpg"


# ---------------------------------------------------------------------------
# upsert_face_region
# ---------------------------------------------------------------------------

class TestUpsertFaceRegion:
    def test_inserts_region(self):
        from src.db.corpus import get_face_regions_for_file, upsert_face_region
        conn = _make_corpus_db()
        _insert_file(conn)
        upsert_face_region(conn, 1, 0, '[[10,20,50,80]]', _fake_embedding(), 3, 0.88)
        rows = get_face_regions_for_file(conn, 1)
        assert len(rows) == 1
        assert rows[0]["person_id"] == 3
        assert rows[0]["similarity"] == pytest.approx(0.88)

    def test_upsert_replaces_existing(self):
        from src.db.corpus import get_face_regions_for_file, upsert_face_region
        conn = _make_corpus_db()
        _insert_file(conn)
        upsert_face_region(conn, 1, 0, None, _fake_embedding(), None, None)
        upsert_face_region(conn, 1, 0, None, _fake_embedding(), 5, 0.91)
        rows = get_face_regions_for_file(conn, 1)
        assert len(rows) == 1
        assert rows[0]["person_id"] == 5

    def test_multiple_regions_per_file(self):
        from src.db.corpus import get_face_regions_for_file, upsert_face_region
        conn = _make_corpus_db()
        _insert_file(conn)
        upsert_face_region(conn, 1, 0, None, _fake_embedding(), None, None)
        upsert_face_region(conn, 1, 1, None, _fake_embedding(), None, None)
        rows = get_face_regions_for_file(conn, 1)
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# reset_face_regions
# ---------------------------------------------------------------------------

class TestResetFaceRegions:
    def test_deletes_all_rows(self):
        from src.db.corpus import reset_face_regions, upsert_face_region
        conn = _make_corpus_db()
        _insert_file(conn)
        upsert_face_region(conn, 1, 0, None, _fake_embedding(), None, None)
        count = reset_face_regions(conn)
        assert count == 1
        remaining = conn.execute("SELECT COUNT(*) FROM file_face_regions").fetchone()[0]
        assert remaining == 0

    def test_reset_empty_safe(self):
        from src.db.corpus import reset_face_regions
        conn = _make_corpus_db()
        assert reset_face_regions(conn) == 0


# ---------------------------------------------------------------------------
# Face cluster helpers
# ---------------------------------------------------------------------------

class TestFaceClusterHelpers:
    def test_upsert_creates_new_cluster(self):
        from src.db.corpus import get_face_clusters, upsert_face_cluster
        conn = _make_corpus_db()
        cid = upsert_face_cluster(conn, None, _fake_embedding(), 1, 0.0)
        assert cid is not None
        clusters = get_face_clusters(conn)
        assert len(clusters) == 1
        assert clusters[0]["member_count"] == 1

    def test_upsert_updates_existing_cluster(self):
        from src.db.corpus import get_face_clusters, upsert_face_cluster
        conn = _make_corpus_db()
        cid = upsert_face_cluster(conn, None, _fake_embedding(), 1, 0.0)
        upsert_face_cluster(conn, cid, _fake_embedding(), 2, 0.05)
        clusters = get_face_clusters(conn)
        assert len(clusters) == 1
        assert clusters[0]["member_count"] == 2

    def test_insert_cluster_member(self):
        from src.db.corpus import insert_face_cluster_member, upsert_face_cluster
        conn = _make_corpus_db()
        _insert_file(conn)
        cid = upsert_face_cluster(conn, None, _fake_embedding(), 1, 0.0)
        insert_face_cluster_member(conn, cid, 1, 0, 0.92)
        row = conn.execute("SELECT * FROM face_cluster_members").fetchone()
        assert row["cluster_id"] == cid
        assert row["file_id"] == 1


# ---------------------------------------------------------------------------
# KB helpers: get_people_with_centroids, update_face_centroid
# ---------------------------------------------------------------------------

class TestPeopleKBHelpers:
    def test_get_people_with_centroids_empty(self):
        from src.db.kb import get_people_with_centroids
        conn = _make_kb_db()
        conn.execute("INSERT INTO people(preferred_name) VALUES ('Alice')")
        rows = get_people_with_centroids(conn, "face")
        assert rows == []

    def test_get_people_with_centroids_returns_match(self):
        from src.db.kb import get_people_with_centroids
        conn = _make_kb_db()
        emb = _fake_embedding()
        conn.execute(
            "INSERT INTO people(preferred_name, face_centroid, face_samples) VALUES (?, ?, ?)",
            ("Alice", emb, 3),
        )
        rows = get_people_with_centroids(conn, "face")
        assert len(rows) == 1
        assert rows[0]["preferred_name"] == "Alice"

    def test_update_face_centroid(self):
        from src.db.kb import update_person_centroid
        conn = _make_kb_db()
        conn.execute("INSERT INTO people(id, preferred_name) VALUES (1, 'Bob')")
        emb = _fake_embedding()
        update_person_centroid(conn, 1, emb, 5, kind="face")
        row = conn.execute("SELECT face_centroid, face_samples FROM people WHERE id=1").fetchone()
        assert bytes(row["face_centroid"]) == emb
        assert row["face_samples"] == 5

    def test_get_people_without_centroid_excluded(self):
        from src.db.kb import get_people_with_centroids
        conn = _make_kb_db()
        conn.execute("INSERT INTO people(preferred_name) VALUES ('NoCentroid')")
        conn.execute(
            "INSERT INTO people(preferred_name, face_centroid, face_samples) VALUES (?, ?, ?)",
            ("WithCentroid", _fake_embedding(), 1),
        )
        rows = get_people_with_centroids(conn, "face")
        assert len(rows) == 1
        assert rows[0]["preferred_name"] == "WithCentroid"


# ---------------------------------------------------------------------------
# Export DB helpers
# ---------------------------------------------------------------------------

class TestPeopleExportHelpers:
    def _setup_kb(self):
        conn = _make_kb_db()
        conn.execute(
            "INSERT INTO people(id, preferred_name, first_name, last_name) VALUES (1, 'Alice Smith', 'Alice', 'Smith')"
        )
        conn.execute(
            "INSERT INTO people_names(person_id, name) VALUES (1, 'Alice')"
        )
        conn.execute(
            "INSERT INTO life_events(person_id, event_type, event_date) VALUES (1, 'birth', '1990-01-01')"
        )
        return conn

    def test_get_people_for_export(self):
        from src.db.kb import get_people_for_export
        conn = self._setup_kb()
        rows = get_people_for_export(conn)
        assert len(rows) == 1
        assert rows[0]["preferred_name"] == "Alice Smith"

    def test_get_people_names_for_export(self):
        from src.db.kb import get_people_names_for_export
        conn = self._setup_kb()
        rows = get_people_names_for_export(conn)
        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"

    def test_get_life_events_for_export(self):
        from src.db.kb import get_life_events_for_export
        conn = self._setup_kb()
        rows = get_life_events_for_export(conn)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "birth"

    def test_get_people_face_centroids_for_export(self):
        from src.db.kb import get_people_face_centroids_for_export
        conn = _make_kb_db()
        emb = _fake_embedding()
        conn.execute(
            "INSERT INTO people(id, preferred_name, face_centroid, face_samples) VALUES (1, 'Bob', ?, 2)",
            (emb,),
        )
        rows = get_people_face_centroids_for_export(conn)
        assert len(rows) == 1
        assert bytes(rows[0]["face_centroid"]) == emb

    def test_get_face_regions_for_export(self):
        from src.db.corpus import get_face_regions_for_export, upsert_face_region
        conn = _make_corpus_db()
        _insert_file(conn, 1, "photo.jpg")
        upsert_face_region(conn, 1, 0, '[0,0,50,50]', _fake_embedding(), 3, 0.9)
        rows = get_face_regions_for_export(conn)
        assert len(rows) == 1
        assert rows[0].file_path == "photo.jpg"
        assert rows[0].person_id == 3


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

class TestFaceHealthChecks:
    def _make_config(self, **kwargs):
        from src.config import Config
        return Config(**kwargs)

    def test_face_detection_model_not_configured(self):
        from src.health import _check_face_detection_model
        config = self._make_config()
        result = _check_face_detection_model(config)
        assert result.id == "face_detection_model"
        assert result.severity == "warning"
        assert result.ok is False

    def test_face_detection_model_configured_missing_file(self, tmp_path):
        from src.health import _check_face_detection_model
        config = self._make_config(face_detection_model=str(tmp_path / "nonexistent.onnx"))
        result = _check_face_detection_model(config)
        assert result.ok is False

    def test_face_detection_model_configured_present(self, tmp_path):
        from src.health import _check_face_detection_model
        model_file = tmp_path / "det.onnx"
        model_file.write_bytes(b"fake")
        config = self._make_config(face_detection_model=str(model_file))
        result = _check_face_detection_model(config)
        assert result.ok is True

    def test_face_embedding_model_not_configured(self):
        from src.health import _check_face_embedding_model
        config = self._make_config()
        result = _check_face_embedding_model(config)
        assert result.id == "face_embedding_model"
        assert result.ok is False

    def test_run_checks_returns_24(self, tmp_path):
        from src.health import run_checks
        config = self._make_config()
        checks = run_checks(config, None, None, tmp_path)
        assert len(checks) == 28


# ---------------------------------------------------------------------------
# DAG registration
# ---------------------------------------------------------------------------

class TestFaceDAG:
    def test_face_in_dependencies(self):
        from src.pipeline.dag import DEPENDENCIES
        assert "face" in DEPENDENCIES
        assert "ingest" in DEPENDENCIES["face"]

    def test_face_in_invalidates(self):
        from src.pipeline.dag import INVALIDATES
        assert "face" in INVALIDATES
        assert INVALIDATES["face"] == []


# ---------------------------------------------------------------------------
# CLI: smoke test for module structure
# ---------------------------------------------------------------------------

class TestFaceCLI:
    def test_app_exists(self):
        from src.cli.face import app
        assert app is not None

    def test_download_command_registered(self):
        from src.cli.face import app
        names = [cmd.name for cmd in app.registered_commands]
        assert "download" in names


# ---------------------------------------------------------------------------
# detect_faces — coordinate handling (no ONNX model needed; patches session)
# ---------------------------------------------------------------------------

class TestDetectFacesCoordinates:
    """Tests for detect_faces coordinate-space detection and bbox normalisation.

    The buffalo_l det_10g model outputs normalized coords [0,1]; other SCRFD
    exports output pixel coords in the 640×640 input space.  The function must
    detect which format is in use and scale to original image pixels correctly.
    """

    @staticmethod
    def _make_img_bytes(w, h):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (w, h), color=(128, 128, 128)).save(buf, format="JPEG")
        return buf.getvalue()

    @staticmethod
    def _mock_session(score_val, bbox_raw):
        """Return a MagicMock InferenceSession for a single detection."""
        import numpy as np
        from unittest.mock import MagicMock
        score_arr = np.array([[score_val]], dtype=np.float32)
        bbox_arr = np.array([bbox_raw], dtype=np.float32)
        sess = MagicMock()
        sess.get_inputs.return_value = [MagicMock(name="input")]
        sess.run.return_value = [score_arr, bbox_arr]
        return sess

    def _run(self, img_w, img_h, score_val, bbox_raw):
        from unittest.mock import patch, MagicMock
        img_bytes = self._make_img_bytes(img_w, img_h)
        sess = self._mock_session(score_val, bbox_raw)
        with patch("onnxruntime.InferenceSession", return_value=sess), \
             patch("pathlib.Path.exists", return_value=True):
            from src.stages.face import detect_faces
            return detect_faces(img_bytes, "/fake/det_10g.onnx")

    def test_normalized_coords_scale_to_original_pixels(self):
        """Normalized output [0.2, 0.1, 0.5, 0.6] on 640×480 should give pixel coords."""
        faces = self._run(640, 480, score_val=0.9, bbox_raw=[0.2, 0.1, 0.5, 0.6])
        assert len(faces) == 1
        x1, y1, x2, y2 = faces[0]["bbox"]
        assert abs(x1 - 0.2 * 640) < 1.0
        assert abs(y1 - 0.1 * 480) < 1.0
        assert abs(x2 - 0.5 * 640) < 1.0
        assert abs(y2 - 0.6 * 480) < 1.0

    def test_pixel_coords_scale_by_ratio(self):
        """Pixel-space output [128, 64, 320, 384] on 1280×960 should scale by 2×."""
        import numpy as np
        from unittest.mock import patch, MagicMock
        img_bytes = self._make_img_bytes(1280, 960)
        score_arr = np.array([[0.9]], dtype=np.float32)
        bbox_arr = np.array([[128.0, 64.0, 320.0, 384.0]], dtype=np.float32)
        sess = MagicMock()
        sess.get_inputs.return_value = [MagicMock(name="input")]
        sess.run.return_value = [score_arr, bbox_arr]
        with patch("onnxruntime.InferenceSession", return_value=sess), \
             patch("pathlib.Path.exists", return_value=True):
            from src.stages.face import detect_faces
            faces = detect_faces(img_bytes, "/fake/det_10g.onnx")
        assert len(faces) == 1
        x1, _y1, x2, _y2 = faces[0]["bbox"]
        # scale_x = 1280/640 = 2.0 → 128*2=256, 320*2=640
        assert abs(x1 - 256.0) < 1.0
        assert abs(x2 - 640.0) < 1.0

    def test_inverted_y_coords_are_swapped(self):
        """Model output with y1 > y2 (e.g. near image edge) must be corrected."""
        faces = self._run(4024, 3024, score_val=0.85,
                          bbox_raw=[0.2, 0.32, 0.24, 0.28])  # y1=0.32 > y2=0.28
        assert len(faces) == 1
        _x1, y1, _x2, y2 = faces[0]["bbox"]
        assert y1 < y2

    def test_inverted_x_coords_are_swapped(self):
        """Model output with x1 > x2 must be corrected."""
        faces = self._run(4024, 3024, score_val=0.85,
                          bbox_raw=[0.28, 0.1, 0.20, 0.6])   # x1=0.28 > x2=0.20
        assert len(faces) == 1
        x1, _y1, x2, _y2 = faces[0]["bbox"]
        assert x1 < x2

    def test_degenerate_bbox_too_small_skipped(self):
        """A bbox < 4px wide after scaling must be skipped."""
        # 0.0005 * 4024 ≈ 2px — below the 4px minimum
        faces = self._run(4024, 3024, score_val=0.9,
                          bbox_raw=[0.0, 0.0, 0.0005, 0.5])
        assert len(faces) == 0

    def test_low_score_face_excluded(self):
        """Faces below the 0.5 score threshold must not be returned."""
        faces = self._run(640, 480, score_val=0.3, bbox_raw=[0.1, 0.1, 0.5, 0.6])
        assert len(faces) == 0

    def test_score_included_in_output(self):
        """Returned face dict must include the detection confidence score."""
        faces = self._run(640, 480, score_val=0.92, bbox_raw=[0.1, 0.1, 0.5, 0.6])
        assert len(faces) == 1
        assert abs(faces[0]["score"] - 0.92) < 0.01

    def test_empty_outputs_returns_no_faces(self):
        """When the model returns no matching score/bbox tensors, return []."""
        import numpy as np
        from unittest.mock import patch, MagicMock
        img_bytes = self._make_img_bytes(640, 480)
        sess = MagicMock()
        sess.get_inputs.return_value = [MagicMock(name="input")]
        sess.run.return_value = [np.zeros((5, 3), dtype=np.float32)]
        with patch("onnxruntime.InferenceSession", return_value=sess), \
             patch("pathlib.Path.exists", return_value=True):
            from src.stages.face import detect_faces
            assert detect_faces(img_bytes, "/fake/det_10g.onnx") == []
