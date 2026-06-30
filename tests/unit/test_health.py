"""Unit tests for src/health.py — each check function in isolation."""
import sqlite3
from unittest.mock import patch

from src.config import Config
from src.health import (
    HealthCheck,
    _check_aesthetic_clip,
    _check_aesthetic_nima,
    _check_audio_model,
    _check_corpus_files,
    _check_dates_yaml,
    _check_derive_rules_yaml,
    _check_exiftool,
    _check_exiftool_config,
    _check_ffmpeg,
    _check_field_map,
    _check_focus,
    _check_library_yaml,
    _check_sources,
    _check_spacy_model,
    _check_taxonomy_yaml,
    _check_text_model,
    _check_unknown_fields,
    _check_vision_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**kwargs) -> Config:
    return Config(**kwargs)


def _mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Group A — Environment (error)
# ---------------------------------------------------------------------------

def test_check_exiftool_ok(tmp_path):
    exe = tmp_path / "exiftool.exe"
    exe.write_bytes(b"")
    chk = _check_exiftool(_cfg(exiftool=str(exe)))
    assert chk.ok
    assert chk.severity == "error"
    assert chk.id == "exiftool"


def test_check_exiftool_missing():
    chk = _check_exiftool(_cfg(exiftool="nonexistent_exiftool_xyz.exe"))
    assert not chk.ok
    assert chk.severity == "error"
    assert chk.fix


def test_check_ffmpeg_ok(tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    chk = _check_ffmpeg(_cfg(ffmpeg=str(exe)))
    assert chk.ok


def test_check_ffmpeg_missing():
    chk = _check_ffmpeg(_cfg(ffmpeg="nonexistent_ffmpeg_xyz.exe"))
    assert not chk.ok
    assert chk.fix


# ---------------------------------------------------------------------------
# Group B — Optional tools (warning)
# ---------------------------------------------------------------------------

def test_check_vision_model_configured_and_present(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"")
    chk = _check_vision_model(_cfg(vision_model=str(model)))
    assert chk.ok
    assert chk.severity == "warning"


def test_check_vision_model_configured_but_missing():
    chk = _check_vision_model(_cfg(vision_model="/no/such/model.gguf"))
    assert not chk.ok
    assert chk.fix


def test_check_vision_model_not_configured_no_dir():
    chk = _check_vision_model(_cfg(vision_model=""))
    assert not chk.ok
    assert chk.id == "vision_model"


def test_check_vision_model_with_mmproj_both_present(tmp_path):
    model = tmp_path / "vision.gguf"
    model.write_bytes(b"")
    mmproj = tmp_path / "mmproj-model-f16.gguf"
    mmproj.write_bytes(b"")
    chk = _check_vision_model(_cfg(vision_model=str(model), vision_mmproj=str(mmproj)))
    assert chk.ok
    assert "mmproj-model-f16.gguf" in chk.detail


def test_check_vision_model_with_mmproj_missing(tmp_path):
    model = tmp_path / "vision.gguf"
    model.write_bytes(b"")
    chk = _check_vision_model(_cfg(vision_model=str(model), vision_mmproj="/no/such/mmproj.gguf"))
    assert not chk.ok
    assert "mmproj" in chk.detail
    assert chk.fix


def test_check_text_model_configured_and_present(tmp_path):
    model = tmp_path / "text.gguf"
    model.write_bytes(b"")
    chk = _check_text_model(_cfg(text_model=str(model)))
    assert chk.ok


def test_check_text_model_missing():
    chk = _check_text_model(_cfg(text_model="/no/such/text.gguf"))
    assert not chk.ok


def test_check_audio_model_pywhispercpp_missing():
    with patch("src.health.importlib.util.find_spec", return_value=None):
        chk = _check_audio_model(_cfg(audio_model="base"))
    assert not chk.ok
    assert chk.id == "audio_model"
    assert chk.severity == "warning"
    assert "pywhispercpp" in chk.detail
    assert chk.fix


def test_check_audio_model_not_configured():
    with patch("src.health.importlib.util.find_spec", return_value=object()):
        chk = _check_audio_model(_cfg(audio_model=""))
    assert not chk.ok
    assert "not configured" in chk.detail
    assert chk.fix


def test_check_audio_model_file_path_present(tmp_path):
    model = tmp_path / "whisper-large.gguf"
    model.write_bytes(b"")
    with patch("src.health.importlib.util.find_spec", return_value=object()):
        chk = _check_audio_model(_cfg(audio_model=str(model)))
    assert chk.ok
    assert chk.severity == "warning"


def test_check_audio_model_file_path_missing():
    with patch("src.health.importlib.util.find_spec", return_value=object()):
        chk = _check_audio_model(_cfg(audio_model="/no/such/whisper.gguf"))
    assert not chk.ok
    assert chk.fix


def test_check_audio_model_named_model():
    with patch("src.health.importlib.util.find_spec", return_value=object()):
        chk = _check_audio_model(_cfg(audio_model="base"))
    assert chk.ok
    assert "base" in chk.detail


def test_check_aesthetic_nima_not_configured():
    chk = _check_aesthetic_nima(Config())
    assert not chk.ok
    assert chk.id == "aesthetic_nima"
    assert chk.severity == "warning"
    assert chk.fix


def test_check_aesthetic_nima_configured_present(tmp_path):
    model = tmp_path / "nima.onnx"
    model.write_bytes(b"")
    chk = _check_aesthetic_nima(Config(aesthetic_nima=str(model)))
    assert chk.ok
    assert chk.severity == "warning"


def test_check_aesthetic_nima_configured_missing():
    chk = _check_aesthetic_nima(Config(aesthetic_nima="/no/such/nima.onnx"))
    assert not chk.ok
    assert chk.fix


def test_check_aesthetic_clip_not_configured():
    chk = _check_aesthetic_clip(Config())
    assert not chk.ok
    assert chk.id == "aesthetic_clip"
    assert chk.severity == "warning"
    assert chk.fix


def test_check_aesthetic_clip_configured_present(tmp_path):
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()
    (clip_dir / "visual.onnx").write_bytes(b"")
    (clip_dir / "linear.npz").write_bytes(b"")
    chk = _check_aesthetic_clip(Config(aesthetic_clip=str(clip_dir)))
    assert chk.ok
    assert chk.severity == "warning"


def test_check_aesthetic_clip_configured_missing_dir():
    chk = _check_aesthetic_clip(Config(aesthetic_clip="/no/such/dir"))
    assert not chk.ok
    assert chk.fix


def test_check_aesthetic_clip_configured_missing_visual(tmp_path):
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()
    (clip_dir / "linear.npz").write_bytes(b"")
    chk = _check_aesthetic_clip(Config(aesthetic_clip=str(clip_dir)))
    assert not chk.ok
    assert "visual.onnx" in chk.detail


def test_check_aesthetic_clip_configured_missing_linear(tmp_path):
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()
    (clip_dir / "visual.onnx").write_bytes(b"")
    chk = _check_aesthetic_clip(Config(aesthetic_clip=str(clip_dir)))
    assert not chk.ok
    assert "linear.npz" in chk.detail


def test_check_spacy_model():
    chk = _check_spacy_model()
    assert isinstance(chk, HealthCheck)
    assert chk.severity == "warning"
    assert chk.id == "spacy_model"
    # result depends on environment; just check structure
    assert isinstance(chk.ok, bool)
    if not chk.ok:
        assert "spacy download" in chk.fix


def test_check_field_map_present(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "field_map.csv").write_text("CanonicalName,ExifTool_Tag\n", encoding="utf-8")
    chk = _check_field_map(tmp_path)
    assert chk.ok
    assert chk.severity == "warning"


def test_check_field_map_missing(tmp_path):
    chk = _check_field_map(tmp_path)
    assert not chk.ok
    assert chk.fix


# ---------------------------------------------------------------------------
# Group C — KB state (info)
# ---------------------------------------------------------------------------

def test_check_sources_ok():
    conn = _mem_db()
    conn.execute(
        "CREATE TABLE sources (id INTEGER PRIMARY KEY, removed_at TEXT)"
    )
    conn.execute("INSERT INTO sources (removed_at) VALUES (NULL)")
    chk = _check_sources(conn)
    assert chk.ok
    assert chk.severity == "info"
    conn.close()


def test_check_sources_empty():
    conn = _mem_db()
    conn.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY, removed_at TEXT)")
    chk = _check_sources(conn)
    assert not chk.ok
    conn.close()


def test_check_sources_none_conn():
    chk = _check_sources(None)
    assert not chk.ok
    assert "unavailable" in chk.detail


def test_check_corpus_files_ok():
    conn = _mem_db()
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO files VALUES (1)")
    chk = _check_corpus_files(conn)
    assert chk.ok
    conn.close()


def test_check_corpus_files_empty():
    conn = _mem_db()
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY)")
    chk = _check_corpus_files(conn)
    assert not chk.ok
    conn.close()


def test_check_vocabulary_ok():
    conn = _mem_db()
    conn.execute("CREATE TABLE vocabulary (id INTEGER PRIMARY KEY, term TEXT)")
    conn.execute("INSERT INTO vocabulary (term) VALUES ('dog')")
    chk = _check_vocabulary(conn)
    assert chk.ok
    conn.close()


def test_check_vocabulary_empty():
    conn = _mem_db()
    conn.execute("CREATE TABLE vocabulary (id INTEGER PRIMARY KEY, term TEXT)")
    chk = _check_vocabulary(conn)
    assert not chk.ok
    conn.close()


def test_check_focus_set():
    chk = _check_focus(_cfg(focus="family history photographs"))
    assert chk.ok


def test_check_focus_not_set():
    chk = _check_focus(_cfg(focus=""))
    assert not chk.ok
    assert chk.fix


def test_check_unknown_fields_no_exif():
    conn = _mem_db()
    conn.execute("CREATE TABLE file_exif (file_id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE file_metadata_fields "
        "(id INTEGER PRIMARY KEY, raw_field_name TEXT, canonical_name TEXT)"
    )
    chk = _check_unknown_fields(conn)
    assert chk.ok
    assert "N/A" in chk.detail
    conn.close()


def test_check_unknown_fields_all_mapped():
    conn = _mem_db()
    conn.execute("CREATE TABLE file_exif (file_id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO file_exif VALUES (1)")
    conn.execute(
        "CREATE TABLE file_metadata_fields "
        "(id INTEGER PRIMARY KEY, raw_field_name TEXT, canonical_name TEXT)"
    )
    conn.execute(
        "INSERT INTO file_metadata_fields (raw_field_name, canonical_name) VALUES ('EXIF:Make', 'exif_camera_make')"
    )
    chk = _check_unknown_fields(conn)
    assert chk.ok
    conn.close()


def test_check_unknown_fields_has_unmapped():
    conn = _mem_db()
    conn.execute("CREATE TABLE file_exif (file_id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO file_exif VALUES (1)")
    conn.execute(
        "CREATE TABLE file_metadata_fields "
        "(id INTEGER PRIMARY KEY, raw_field_name TEXT, canonical_name TEXT)"
    )
    conn.execute(
        "INSERT INTO file_metadata_fields (raw_field_name, canonical_name) VALUES ('XMP:Custom', NULL)"
    )
    conn.execute(
        "INSERT INTO file_metadata_fields (raw_field_name, canonical_name) VALUES ('XMP:Other', '')"
    )
    chk = _check_unknown_fields(conn)
    assert not chk.ok
    assert "2" in chk.detail
    conn.close()


# ---------------------------------------------------------------------------
# Group D — KB scaffold files (info)
# ---------------------------------------------------------------------------

def test_check_library_yaml_ok(tmp_path):
    (tmp_path / "library.yaml").write_text("scan:\n  default_file_types: all\n", encoding="utf-8")
    chk = _check_library_yaml(tmp_path)
    assert chk.ok
    assert chk.severity == "info"


def test_check_library_yaml_missing(tmp_path):
    chk = _check_library_yaml(tmp_path)
    assert not chk.ok
    assert chk.fix


def test_check_library_yaml_invalid_yaml(tmp_path):
    (tmp_path / "library.yaml").write_text("key: [unclosed", encoding="utf-8")
    chk = _check_library_yaml(tmp_path)
    assert not chk.ok
    assert "parse error" in chk.detail


def test_check_exiftool_config_present(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "ExifTool_Config").write_text("%Image::ExifTool::UserDefined = ();\n", encoding="utf-8")
    chk = _check_exiftool_config(tmp_path)
    assert chk.ok
    assert chk.severity == "info"


def test_check_exiftool_config_missing(tmp_path):
    chk = _check_exiftool_config(tmp_path)
    assert not chk.ok
    assert "FamilyArchive" in chk.detail


def test_check_dates_yaml_ok(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "dates.yaml").write_text("enabled: true\n", encoding="utf-8")
    chk = _check_dates_yaml(tmp_path)
    assert chk.ok


def test_check_dates_yaml_missing(tmp_path):
    (tmp_path / "reference").mkdir()
    chk = _check_dates_yaml(tmp_path)
    assert not chk.ok


def test_check_derive_rules_yaml_ok(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "derive_rules.yaml").write_text("field_rules: []\n", encoding="utf-8")
    chk = _check_derive_rules_yaml(tmp_path)
    assert chk.ok


def test_check_taxonomy_yaml_ok(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "taxonomy.yaml").write_text("event: []\n", encoding="utf-8")
    chk = _check_taxonomy_yaml(tmp_path)
    assert chk.ok


# ---------------------------------------------------------------------------
# run_checks integration — returns all 18 checks
# ---------------------------------------------------------------------------

def test_run_checks_returns_24(tmp_path):
    from src.health import run_checks as _run
    cfg = _cfg()
    checks = _run(cfg, None, None, tmp_path)
    assert len(checks) == 28
    ids = [c.id for c in checks]
    assert "exiftool" in ids
    assert "audio_model" in ids
    assert "taxonomy_yaml" in ids
    assert "geolocate_data" in ids
    assert "privacy_zones" in ids
    assert "face_detection_model" in ids
    assert "face_embedding_model" in ids
    assert "validation_freshness" in ids


def test_privacy_zones_check_no_yaml(tmp_path):
    from src.health import _check_privacy_zones
    kb_folder = tmp_path / "kb"
    (kb_folder / "reference").mkdir(parents=True)
    result = _check_privacy_zones(kb_folder)
    assert result.ok is True
    assert result.id == "privacy_zones"


def test_privacy_zones_check_valid_yaml(tmp_path):
    from src.health import _check_privacy_zones
    kb_folder = tmp_path / "kb"
    ref = kb_folder / "reference"
    ref.mkdir(parents=True)
    (ref / "privacy_zones.yaml").write_text(
        "privacy_zones:\n  - name: Home\n    mode: strip\n    center: [51.5, -0.1]\n    radius_m: 500\n",
        encoding="utf-8",
    )
    result = _check_privacy_zones(kb_folder)
    assert result.ok is True


def test_privacy_zones_check_missing_zone_file(tmp_path):
    from src.health import _check_privacy_zones
    kb_folder = tmp_path / "kb"
    ref = kb_folder / "reference"
    ref.mkdir(parents=True)
    (ref / "privacy_zones.yaml").write_text(
        "privacy_zones:\n  - name: Zone\n    mode: strip\n    file: geo/custom/missing.geojson\n",
        encoding="utf-8",
    )
    result = _check_privacy_zones(kb_folder)
    assert result.ok is False
    assert "missing.geojson" in result.detail


# ---------------------------------------------------------------------------
# Group F — Validation freshness
# ---------------------------------------------------------------------------

def _make_validation_db() -> sqlite3.Connection:
    conn = _mem_db()
    conn.execute(
        """
        CREATE TABLE validation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT, files_checked INTEGER, ok_count INTEGER,
            changed_count INTEGER, moved_count INTEGER, missing_count INTEGER
        )
        """
    )
    return conn


def test_check_validation_freshness_no_conn():
    from src.health import _check_validation_freshness
    chk = _check_validation_freshness(None)
    assert chk.ok is True
    assert chk.severity == "info"
    assert chk.id == "validation_freshness"


def test_check_validation_freshness_no_run():
    from src.health import _check_validation_freshness
    conn = _make_validation_db()
    chk = _check_validation_freshness(conn)
    assert chk.ok is True
    assert chk.severity == "info"
    assert "consider running" in chk.detail
    conn.close()


def test_check_validation_freshness_clean_run():
    from src.health import _check_validation_freshness
    conn = _make_validation_db()
    conn.execute(
        "INSERT INTO validation_runs VALUES (1, '2026-06-21T00:00:00Z', 50, 50, 0, 0, 0)"
    )
    chk = _check_validation_freshness(conn)
    assert chk.ok is True
    assert chk.severity == "info"
    assert "50" in chk.detail
    conn.close()


def test_check_validation_freshness_changed():
    from src.health import _check_validation_freshness
    conn = _make_validation_db()
    conn.execute(
        "INSERT INTO validation_runs VALUES (1, '2026-06-21T00:00:00Z', 10, 8, 2, 0, 0)"
    )
    chk = _check_validation_freshness(conn)
    assert chk.ok is False
    assert chk.severity == "warning"
    assert "changed" in chk.detail
    conn.close()


def test_check_validation_freshness_missing():
    from src.health import _check_validation_freshness
    conn = _make_validation_db()
    conn.execute(
        "INSERT INTO validation_runs VALUES (1, '2026-06-21T00:00:00Z', 10, 8, 0, 0, 2)"
    )
    chk = _check_validation_freshness(conn)
    assert chk.ok is False
    assert chk.severity == "warning"
    assert "missing" in chk.detail
    conn.close()


def _check_vocabulary(conn):
    from src.health import _check_vocabulary as _cv
    return _cv(conn)
