"""Integration tests for GPS privacy zone masking in the write-back stage."""
import shutil
import threading

import pytest

from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import add_vocabulary_term, bump_kb_version, open_kb
from src.pipeline.progress import NullProgressReporter
from src.stages.writeback import run_writeback


def _exiftool_exe():
    exe = shutil.which("exiftool")
    if exe:
        return exe
    from pathlib import Path
    candidate = Path("tools/exiftool.exe")
    return str(candidate) if candidate.exists() else None


@pytest.fixture(autouse=True)
def require_exiftool():
    if _exiftool_exe() is None:
        pytest.skip("ExifTool not found")


def _seed(corpus_conn, kb_conn, image_path, gps_proposal=None, original_gps=None):
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, ?, 'test.jpg', '.jpg', 'image', 1, 0.0)",
        (str(image_path),),
    )
    corpus_conn.execute(
        "INSERT INTO retag_output"
        " (file_id, tags_json, refined_description, new_terms_proposed_json,"
        "  model, processed_at, retag_status)"
        " VALUES (1, '[]', NULL, '[]', 'test', datetime('now'), 'done')",
    )
    add_vocabulary_term(kb_conn, "test-tag")
    bump_kb_version(kb_conn, "vocabulary_term_added")
    kb_conn.commit()

    if gps_proposal:
        lat, lon = gps_proposal
        corpus_conn.execute(
            "INSERT INTO gps_proposals"
            " (file_id, location_name, proposed_lat, proposed_lon, status)"
            " VALUES (1, 'test', ?, ?, 'accepted')",
            (lat, lon),
        )

    if original_gps:
        lat, lon = original_gps
        corpus_conn.execute(
            "INSERT INTO file_metadata_fields (file_id, canonical_name, value)"
            " VALUES (1, 'exif_gps_lat', ?), (1, 'exif_gps_lon', ?)",
            (str(lat), str(lon)),
        )

    corpus_conn.commit()


def _write_zones_yaml(kb_folder, content: str):
    ref = kb_folder / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "privacy_zones.yaml").write_text(content, encoding="utf-8")


def _run(corpus_path, kb_path, kb_folder=None, force=True):
    run_writeback(
        corpus_path,
        kb_path,
        Config(exiftool=_exiftool_exe()),
        NullProgressReporter(),
        threading.Event(),
        force=force,
    )


# ---------------------------------------------------------------------------
# GPS proposal + zone interactions
# ---------------------------------------------------------------------------

class TestProposalGpsMasking:
    def test_proposal_in_strip_zone_deletes_gps_tags(self, tmp_path, sample_image):
        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)
        _seed(corpus_conn, kb_conn, sample_image, gps_proposal=(51.5074, -0.1278))
        corpus_conn.close()
        kb_conn.close()

        # kb_path.parent == tmp_path; write zones there
        kb_folder = kb_path.parent
        _write_zones_yaml(kb_folder, """
privacy_zones:
  - name: Home
    mode: strip
    center: [51.5074, -0.1278]
    radius_m: 10000
""")
        _run(corpus_path, kb_path)
        corpus_conn = open_corpus(corpus_path)
        from src.db.corpus import get_gps_masked_files
        masked = get_gps_masked_files(corpus_conn)
        corpus_conn.close()
        assert 1 in masked

    def test_proposal_in_coarsen_zone_logs_mask(self, tmp_path, sample_image):
        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)
        _seed(corpus_conn, kb_conn, sample_image, gps_proposal=(51.5074, -0.1278))
        corpus_conn.close()
        kb_conn.close()

        kb_folder = kb_path.parent
        _write_zones_yaml(kb_folder, """
privacy_zones:
  - name: Office
    mode: coarsen
    decimal_places: 1
    center: [51.5074, -0.1278]
    radius_m: 10000
""")
        _run(corpus_path, kb_path)
        corpus_conn = open_corpus(corpus_path)
        row = corpus_conn.execute(
            "SELECT mode, masked_lat, masked_lon FROM file_gps_masks WHERE file_id=1"
        ).fetchone()
        corpus_conn.close()
        assert row is not None
        assert row["mode"] == "coarsen"
        assert row["masked_lat"] == pytest.approx(51.5, abs=0.11)

    def test_proposal_outside_zone_not_masked(self, tmp_path, sample_image):
        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)
        _seed(corpus_conn, kb_conn, sample_image, gps_proposal=(40.0, 2.0))
        corpus_conn.close()
        kb_conn.close()

        kb_folder = kb_path.parent
        _write_zones_yaml(kb_folder, """
privacy_zones:
  - name: Home
    mode: strip
    center: [51.5074, -0.1278]
    radius_m: 500
""")
        _run(corpus_path, kb_path)
        corpus_conn = open_corpus(corpus_path)
        from src.db.corpus import get_gps_masked_files
        masked = get_gps_masked_files(corpus_conn)
        corpus_conn.close()
        assert 1 not in masked


# ---------------------------------------------------------------------------
# Original extracted GPS masking
# ---------------------------------------------------------------------------

class TestOriginalGpsMasking:
    def test_original_gps_in_strip_zone_logged(self, tmp_path, sample_image):
        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)
        _seed(corpus_conn, kb_conn, sample_image, original_gps=(51.5074, -0.1278))
        corpus_conn.close()
        kb_conn.close()

        kb_folder = kb_path.parent
        _write_zones_yaml(kb_folder, """
privacy_zones:
  - name: Home
    mode: strip
    center: [51.5074, -0.1278]
    radius_m: 10000
""")
        _run(corpus_path, kb_path)
        corpus_conn = open_corpus(corpus_path)
        from src.db.corpus import get_gps_masked_files
        masked = get_gps_masked_files(corpus_conn)
        corpus_conn.close()
        assert 1 in masked

    def test_original_gps_in_coarsen_zone_logged(self, tmp_path, sample_image):
        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)
        _seed(corpus_conn, kb_conn, sample_image, original_gps=(51.5074, -0.1278))
        corpus_conn.close()
        kb_conn.close()

        kb_folder = kb_path.parent
        _write_zones_yaml(kb_folder, """
privacy_zones:
  - name: Office
    mode: coarsen
    decimal_places: 2
    center: [51.5074, -0.1278]
    radius_m: 10000
""")
        _run(corpus_path, kb_path)
        corpus_conn = open_corpus(corpus_path)
        row = corpus_conn.execute(
            "SELECT mode FROM file_gps_masks WHERE file_id=1"
        ).fetchone()
        corpus_conn.close()
        assert row is not None
        assert row["mode"] == "coarsen"


# ---------------------------------------------------------------------------
# No-zones / empty configurations
# ---------------------------------------------------------------------------

class TestNoZonesConfiguration:
    def test_no_zones_file_unchanged_behaviour(self, tmp_path, sample_image):
        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)
        _seed(corpus_conn, kb_conn, sample_image, gps_proposal=(51.5, -0.1))
        corpus_conn.close()
        kb_conn.close()

        # No privacy_zones.yaml — no kb_folder / reference dir
        _run(corpus_path, kb_path)
        corpus_conn = open_corpus(corpus_path)
        from src.db.corpus import get_gps_masked_files
        masked = get_gps_masked_files(corpus_conn)
        corpus_conn.close()
        assert masked == set()

    def test_empty_zones_list_unchanged_behaviour(self, tmp_path, sample_image):
        corpus_path = tmp_path / "corpus.db"
        kb_path = tmp_path / "knowledge.db"
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)
        _seed(corpus_conn, kb_conn, sample_image, gps_proposal=(51.5, -0.1))
        corpus_conn.close()
        kb_conn.close()

        kb_folder = kb_path.parent
        _write_zones_yaml(kb_folder, "privacy_zones: []")
        _run(corpus_path, kb_path)
        corpus_conn = open_corpus(corpus_path)
        from src.db.corpus import get_gps_masked_files
        masked = get_gps_masked_files(corpus_conn)
        corpus_conn.close()
        assert masked == set()


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def test_0014_gps_masks_migration(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    conn = open_corpus(corpus_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "file_gps_masks" in tables
