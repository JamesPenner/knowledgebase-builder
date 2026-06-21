"""Unit tests for aesthetic scoring — pure-Python logic only, no ONNX inference."""
import math
import sqlite3

import pytest

from src.stages.aesthetic import assign_band


# ---------------------------------------------------------------------------
# assign_band
# ---------------------------------------------------------------------------

class TestAssignBandNima:
    def test_excellent(self):
        assert assign_band(0.80, "nima_mobilenet") == "excellent"

    def test_excellent_boundary(self):
        assert assign_band(0.75, "nima_mobilenet") == "excellent"

    def test_good(self):
        assert assign_band(0.65, "nima_mobilenet") == "good"

    def test_good_boundary(self):
        assert assign_band(0.55, "nima_mobilenet") == "good"

    def test_average(self):
        assert assign_band(0.40, "nima_mobilenet") == "average"

    def test_average_boundary(self):
        assert assign_band(0.30, "nima_mobilenet") == "average"

    def test_poor(self):
        assert assign_band(0.10, "nima_mobilenet") == "poor"

    def test_zero(self):
        assert assign_band(0.0, "nima_mobilenet") == "poor"

    def test_one(self):
        assert assign_band(1.0, "nima_mobilenet") == "excellent"


class TestAssignBandClip:
    def test_excellent(self):
        assert assign_band(0.90, "clip_vit_b32") == "excellent"

    def test_excellent_boundary(self):
        assert assign_band(0.80, "clip_vit_b32") == "excellent"

    def test_good(self):
        assert assign_band(0.70, "clip_vit_b32") == "good"

    def test_good_boundary(self):
        assert assign_band(0.62, "clip_vit_b32") == "good"

    def test_average(self):
        assert assign_band(0.45, "clip_vit_b32") == "average"

    def test_average_boundary(self):
        assert assign_band(0.30, "clip_vit_b32") == "average"

    def test_poor(self):
        assert assign_band(0.20, "clip_vit_b32") == "poor"


class TestAssignBandCombined:
    def test_excellent(self):
        assert assign_band(0.80, "combined_rank") == "excellent"

    def test_excellent_boundary(self):
        assert assign_band(0.75, "combined_rank") == "excellent"

    def test_good(self):
        assert assign_band(0.60, "combined_rank") == "good"

    def test_good_boundary(self):
        assert assign_band(0.50, "combined_rank") == "good"

    def test_average(self):
        assert assign_band(0.35, "combined_rank") == "average"

    def test_average_boundary(self):
        assert assign_band(0.25, "combined_rank") == "average"

    def test_poor(self):
        assert assign_band(0.10, "combined_rank") == "poor"

    def test_unknown_model_falls_back_to_combined(self):
        # unknown model name uses combined_rank thresholds
        assert assign_band(0.80, "unknown_model") == "excellent"
        assert assign_band(0.10, "unknown_model") == "poor"


# ---------------------------------------------------------------------------
# XMP:Rating clamping formula
# ---------------------------------------------------------------------------

class TestXmpRatingClamping:
    """max(1, ceil(score * 5)) — verifies star mapping."""

    def _rating(self, score: float) -> int:
        return max(1, math.ceil(score * 5))

    def test_zero_maps_to_one(self):
        assert self._rating(0.0) == 1

    def test_low_score_maps_to_one(self):
        assert self._rating(0.01) == 1

    def test_perfect_maps_to_five(self):
        assert self._rating(1.0) == 5

    def test_midpoint(self):
        assert self._rating(0.5) == 3

    def test_near_top(self):
        assert self._rating(0.81) == 5

    def test_boundary_0_2(self):
        assert self._rating(0.2) == 1

    def test_boundary_0_21(self):
        assert self._rating(0.21) == 2


# ---------------------------------------------------------------------------
# compute_combined_rank_scores
# ---------------------------------------------------------------------------

def _make_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE files (
            id        INTEGER PRIMARY KEY,
            path      TEXT,
            file_type TEXT,
            canonical_id INTEGER
        );
        CREATE TABLE file_aesthetic (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id    INTEGER NOT NULL,
            model_name TEXT    NOT NULL,
            score      REAL,
            band       TEXT,
            scored_at  DATETIME DEFAULT (datetime('now')),
            UNIQUE (file_id, model_name)
        );
    """)
    return conn


def _insert_file(conn, file_id, path="img.jpg"):
    conn.execute("INSERT INTO files(id, path, file_type) VALUES (?, ?, 'image')", (file_id, path))


def _insert_score(conn, file_id, model_name, score):
    conn.execute(
        "INSERT INTO file_aesthetic(file_id, model_name, score, band) VALUES (?, ?, ?, 'good')",
        (file_id, model_name, score),
    )


class TestComputeCombinedRankScores:
    def test_two_models_two_files(self):
        from src.db.corpus import compute_combined_rank_scores

        conn = _make_memory_db()
        _insert_file(conn, 1)
        _insert_file(conn, 2)
        # NIMA: file1=0.3 (rank 0/1=0.0), file2=0.9 (rank 1/1=1.0)
        _insert_score(conn, 1, "nima_mobilenet", 0.3)
        _insert_score(conn, 2, "nima_mobilenet", 0.9)
        # CLIP: file1=0.4 (rank 0/1=0.0), file2=0.8 (rank 1/1=1.0)
        _insert_score(conn, 1, "clip_vit_b32", 0.4)
        _insert_score(conn, 2, "clip_vit_b32", 0.8)

        count = compute_combined_rank_scores(conn)
        assert count == 2

        rows = {r["file_id"]: r["score"] for r in conn.execute(
            "SELECT file_id, score FROM file_aesthetic WHERE model_name='combined_rank'"
        ).fetchall()}
        assert rows[1] == pytest.approx(0.0)
        assert rows[2] == pytest.approx(1.0)

    def test_single_model_returns_zero(self):
        from src.db.corpus import compute_combined_rank_scores

        conn = _make_memory_db()
        _insert_file(conn, 1)
        _insert_score(conn, 1, "nima_mobilenet", 0.5)

        count = compute_combined_rank_scores(conn)
        assert count == 0

    def test_no_scores_returns_zero(self):
        from src.db.corpus import compute_combined_rank_scores

        conn = _make_memory_db()
        count = compute_combined_rank_scores(conn)
        assert count == 0

    def test_rank_norm_middle_file(self):
        from src.db.corpus import compute_combined_rank_scores

        conn = _make_memory_db()
        for i in (1, 2, 3):
            _insert_file(conn, i)
        # NIMA: 3 files in ascending order → ranks 0.0, 0.5, 1.0
        _insert_score(conn, 1, "nima_mobilenet", 0.1)
        _insert_score(conn, 2, "nima_mobilenet", 0.5)
        _insert_score(conn, 3, "nima_mobilenet", 0.9)
        # CLIP: same ranking order
        _insert_score(conn, 1, "clip_vit_b32", 0.2)
        _insert_score(conn, 2, "clip_vit_b32", 0.5)
        _insert_score(conn, 3, "clip_vit_b32", 0.8)

        compute_combined_rank_scores(conn)
        rows = {r["file_id"]: r["score"] for r in conn.execute(
            "SELECT file_id, score FROM file_aesthetic WHERE model_name='combined_rank'"
        ).fetchall()}
        assert rows[2] == pytest.approx(0.5)

    def test_idempotent_recompute(self):
        from src.db.corpus import compute_combined_rank_scores

        conn = _make_memory_db()
        _insert_file(conn, 1)
        _insert_file(conn, 2)
        _insert_score(conn, 1, "nima_mobilenet", 0.2)
        _insert_score(conn, 2, "nima_mobilenet", 0.8)
        _insert_score(conn, 1, "clip_vit_b32", 0.3)
        _insert_score(conn, 2, "clip_vit_b32", 0.7)

        compute_combined_rank_scores(conn)
        count2 = compute_combined_rank_scores(conn)
        assert count2 == 2  # second run upserts same values, count still 2


# ---------------------------------------------------------------------------
# get_aesthetic_scores_for_export
# ---------------------------------------------------------------------------

class TestGetAestheticScoresForExport:
    def _setup(self) -> sqlite3.Connection:
        conn = _make_memory_db()
        for i, path in enumerate(["a.jpg", "b.jpg", "c.jpg"], start=1):
            _insert_file(conn, i, path)
        _insert_score(conn, 1, "nima_mobilenet", 0.8)
        _insert_score(conn, 2, "nima_mobilenet", 0.4)
        _insert_score(conn, 3, "nima_mobilenet", 0.2)
        _insert_score(conn, 1, "combined_rank", 0.9)
        _insert_score(conn, 2, "combined_rank", 0.5)
        _insert_score(conn, 3, "combined_rank", 0.1)
        return conn

    def test_no_filter_returns_all(self):
        from src.db.corpus import get_aesthetic_scores_for_export
        conn = self._setup()
        rows = get_aesthetic_scores_for_export(conn)
        assert len(rows) == 3

    def test_min_score_filters_combined_rank(self):
        from src.db.corpus import get_aesthetic_scores_for_export
        conn = self._setup()
        rows = get_aesthetic_scores_for_export(conn, model_name="combined_rank", min_score=0.5)
        assert len(rows) == 2
        paths = {r["file_path"] for r in rows}
        assert "a.jpg" in paths
        assert "b.jpg" in paths

    def test_min_score_high_threshold(self):
        from src.db.corpus import get_aesthetic_scores_for_export
        conn = self._setup()
        rows = get_aesthetic_scores_for_export(conn, model_name="combined_rank", min_score=0.95)
        assert len(rows) == 0

    def test_min_score_on_nima(self):
        from src.db.corpus import get_aesthetic_scores_for_export
        conn = self._setup()
        rows = get_aesthetic_scores_for_export(conn, model_name="nima_mobilenet", min_score=0.5)
        assert len(rows) == 1
        assert rows[0]["file_path"] == "a.jpg"

    def test_nima_score_in_output(self):
        from src.db.corpus import get_aesthetic_scores_for_export
        conn = self._setup()
        rows = get_aesthetic_scores_for_export(conn)
        by_path = {r["file_path"]: r for r in rows}
        assert by_path["a.jpg"]["nima_score"] == pytest.approx(0.8)

    def test_combined_rank_in_output(self):
        from src.db.corpus import get_aesthetic_scores_for_export
        conn = self._setup()
        rows = get_aesthetic_scores_for_export(conn)
        by_path = {r["file_path"]: r for r in rows}
        assert by_path["a.jpg"]["combined_rank"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# reset_aesthetic_scores
# ---------------------------------------------------------------------------

class TestResetAestheticScores:
    def test_reset_all(self):
        from src.db.corpus import reset_aesthetic_scores

        conn = _make_memory_db()
        _insert_file(conn, 1)
        _insert_score(conn, 1, "nima_mobilenet", 0.5)
        _insert_score(conn, 1, "clip_vit_b32", 0.6)

        count = reset_aesthetic_scores(conn)
        assert count == 2
        remaining = conn.execute("SELECT COUNT(*) FROM file_aesthetic").fetchone()[0]
        assert remaining == 0

    def test_reset_by_model(self):
        from src.db.corpus import reset_aesthetic_scores

        conn = _make_memory_db()
        _insert_file(conn, 1)
        _insert_score(conn, 1, "nima_mobilenet", 0.5)
        _insert_score(conn, 1, "clip_vit_b32", 0.6)

        count = reset_aesthetic_scores(conn, model_name="nima_mobilenet")
        assert count == 1
        remaining = conn.execute(
            "SELECT model_name FROM file_aesthetic"
        ).fetchone()["model_name"]
        assert remaining == "clip_vit_b32"

    def test_reset_empty_is_safe(self):
        from src.db.corpus import reset_aesthetic_scores

        conn = _make_memory_db()
        count = reset_aesthetic_scores(conn)
        assert count == 0


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestAestheticConfig:
    def test_aesthetic_nima_default_empty(self):
        from src.config import Config
        assert Config().aesthetic_nima == ""

    def test_aesthetic_clip_default_empty(self):
        from src.config import Config
        assert Config().aesthetic_clip == ""

    def test_load_config_aesthetic_nima(self, tmp_path):
        from src.config import load_config

        cfg = tmp_path / "config.yaml"
        cfg.write_text("models:\n  aesthetic_nima: /path/to/nima.onnx\n")
        config = load_config(cfg)
        assert config.aesthetic_nima == "/path/to/nima.onnx"

    def test_load_config_aesthetic_clip(self, tmp_path):
        from src.config import load_config

        cfg = tmp_path / "config.yaml"
        cfg.write_text("models:\n  aesthetic_clip: /path/to/clip_dir\n")
        config = load_config(cfg)
        assert config.aesthetic_clip == "/path/to/clip_dir"

    def test_load_config_both_models(self, tmp_path):
        from src.config import load_config

        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "models:\n"
            "  aesthetic_nima: /nima.onnx\n"
            "  aesthetic_clip: /clip\n"
        )
        config = load_config(cfg)
        assert config.aesthetic_nima == "/nima.onnx"
        assert config.aesthetic_clip == "/clip"
