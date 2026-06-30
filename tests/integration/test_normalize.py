from pathlib import Path

from src.config import Config
from src.db.corpus import add_source, open_corpus
from src.db.kb import (
    add_pattern_rule,
    add_to_stoplist,
    open_kb,
)
from src.pipeline.cancel import make_cancel_event
from src.pipeline.progress import NullProgressReporter
from src.stages.ingest import run_ingest
from src.stages.normalize import normalize_filename, run_normalize


def _make_images(directory: Path, names: list[str]) -> None:
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", (4, 4)).save(directory / name)


def _setup(tmp_path: Path, filenames: list[str]) -> tuple[Path, Path]:
    src_dir = tmp_path / "sources"
    _make_images(src_dir, filenames)
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    conn = open_corpus(corpus_path)
    add_source(conn, str(src_dir), "images", True)
    conn.close()
    cfg = Config()
    run_ingest(corpus_path, kb_path, cfg, NullProgressReporter(), make_cancel_event())
    return corpus_path, kb_path


def _run_norm(corpus_path: Path, kb_path: Path) -> None:
    run_normalize(corpus_path, kb_path, Config(), NullProgressReporter(), make_cancel_event())


# ---------------------------------------------------------------------------
# Unit-style tests on normalize_filename (no DB)
# ---------------------------------------------------------------------------

def test_capture_rule_extracts_field():
    _, captured = normalize_filename(
        "20160929_clip001.jpg",
        pattern_rules=[{"pattern": r"^\d{8}$", "is_regex": True, "action": "capture",
                        "extract_as": "file_date", "format_str": None, "keep_token": False, "value_type": "date"}],
        substitute_rules=[],
        stoplist=set(),
    )
    assert captured.get("file_date") == "20160929"


def test_capture_rule_with_format_str():
    _, captured = normalize_filename(
        "20160929_clip001.jpg",
        pattern_rules=[{"pattern": r"^(\d{8})$", "is_regex": True, "action": "capture",
                        "extract_as": "file_date", "format_str": "{1:0:4}-{1:4:6}-{1:6:8}",
                        "keep_token": False, "value_type": "date"}],
        substitute_rules=[],
        stoplist=set(),
    )
    assert captured.get("file_date") == "2016-09-29"


def test_capture_keep_token_false_removes_from_name():
    name, _ = normalize_filename(
        "20160929_footage.jpg",
        pattern_rules=[{"pattern": r"^\d{8}$", "is_regex": True, "action": "capture",
                        "extract_as": "file_date", "format_str": None, "keep_token": False, "value_type": "date"}],
        substitute_rules=[],
        stoplist=set(),
    )
    assert "20160929" not in name


def test_capture_keep_token_true_keeps_in_name():
    name, _ = normalize_filename(
        "20160929_footage.jpg",
        pattern_rules=[{"pattern": r"^\d{8}$", "is_regex": True, "action": "capture",
                        "extract_as": "file_date", "format_str": None, "keep_token": True, "value_type": "date"}],
        substitute_rules=[],
        stoplist=set(),
    )
    assert "20160929" in name


def test_reject_token_strips_from_name():
    name, _ = normalize_filename(
        "img_dsc_001.jpg",
        pattern_rules=[{"pattern": "dsc", "is_regex": False, "action": "reject"}],
        substitute_rules=[],
        stoplist=set(),
    )
    assert "dsc" not in name


def test_reject_regex_pattern():
    name, _ = normalize_filename(
        "img_dsc001_clip.jpg",
        pattern_rules=[{"pattern": r"^dsc\d+$", "is_regex": True, "action": "reject"}],
        substitute_rules=[],
        stoplist=set(),
    )
    assert "dsc001" not in name
    assert "clip" in name


def test_correction_applied():
    name, _ = normalize_filename(
        "tuckinleted_photo.jpg",
        pattern_rules=[{"pattern": "tuckinleted", "is_regex": False, "action": "replace",
                        "replace_with": "Tuck Inlet"}],
        substitute_rules=[],
        stoplist=set(),
    )
    assert "Tuck Inlet" in name
    assert "tuckinleted" not in name


def test_stoplist_filters_term():
    name, _ = normalize_filename(
        "photo_image_001.jpg",
        pattern_rules=[],
        substitute_rules=[],
        stoplist={"image"},
    )
    assert "image" not in name
    assert "photo" in name


def test_substitute_rule_applied():
    name, _ = normalize_filename(
        "hwy97c_footage.jpg",
        pattern_rules=[],
        substitute_rules=[{"pattern": r"\bhwy97c\b", "replacement": "Highway 97C", "applies_to": "filename"}],
        stoplist=set(),
    )
    assert "Highway 97C" in name


# ---------------------------------------------------------------------------
# Integration tests against real DBs
# ---------------------------------------------------------------------------

def test_run_normalize_writes_captured_field(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["20160929_clip001.jpg"])
    kb_conn = open_kb(kb_path)
    add_pattern_rule(kb_conn, pattern=r"^\d{8}$", action="capture", label="date",
                     extract_as="file_date", format_str="", value_type="date", keep_token=False)
    kb_conn.close()

    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT value FROM file_captured_fields WHERE field_name='file_date'").fetchone()
    conn.close()
    assert row is not None
    assert row["value"] == "20160929"


def test_run_normalize_is_idempotent(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["20160929_clip001.jpg", "20170415_clip002.jpg"])
    kb_conn = open_kb(kb_path)
    add_pattern_rule(kb_conn, pattern=r"^\d{8}$", action="capture", label="date",
                     extract_as="file_date", format_str="", value_type="date", keep_token=False)
    kb_conn.close()

    _run_norm(corpus_path, kb_path)
    conn = open_corpus(corpus_path)
    count1 = conn.execute("SELECT COUNT(*) FROM file_captured_fields").fetchone()[0]
    normalized1 = conn.execute("SELECT filename_normalized FROM files LIMIT 1").fetchone()[0]
    conn.close()

    _run_norm(corpus_path, kb_path)
    conn = open_corpus(corpus_path)
    count2 = conn.execute("SELECT COUNT(*) FROM file_captured_fields").fetchone()[0]
    normalized2 = conn.execute("SELECT filename_normalized FROM files LIMIT 1").fetchone()[0]
    conn.close()

    assert count2 == count1
    assert normalized2 == normalized1


def test_run_normalize_new_rule_picked_up_on_rerun(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["20160929_bc5_clip.jpg"])
    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    assert conn.execute("SELECT value FROM file_captured_fields WHERE field_name='route_number'").fetchone() is None
    conn.close()

    kb_conn = open_kb(kb_path)
    add_pattern_rule(kb_conn, pattern=r"^bc\d+$", action="capture", label="route",
                     extract_as="route_number", format_str="", value_type="code", keep_token=False)
    kb_conn.close()

    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT value FROM file_captured_fields WHERE field_name='route_number'").fetchone()
    conn.close()
    assert row is not None


def test_run_normalize_reject_strips_from_filename_normalized(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["img_dsc_001.jpg"])
    kb_conn = open_kb(kb_path)
    add_pattern_rule(kb_conn, pattern="dsc", action="reject", is_regex=False, label="camera-prefix")
    kb_conn.close()

    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT filename_normalized FROM files WHERE filename='img_dsc_001.jpg'").fetchone()
    conn.close()
    assert row is not None
    assert "dsc" not in (row["filename_normalized"] or "")


def test_run_normalize_correction_in_filename(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["tuckinleted_photo.jpg"])
    kb_conn = open_kb(kb_path)
    add_pattern_rule(kb_conn, pattern="tuckinleted", action="replace", is_regex=False,
                     replace_with="Tuck Inlet", replace_type="correction")
    kb_conn.close()

    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT filename_normalized FROM files WHERE filename='tuckinleted_photo.jpg'").fetchone()
    conn.close()
    assert row is not None
    assert "Tuck Inlet" in (row["filename_normalized"] or "")


def test_run_normalize_stoplist_filters(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["photo_image_landscape.jpg"])
    kb_conn = open_kb(kb_path)
    add_to_stoplist(kb_conn, "image", scope="global", source="domain")
    kb_conn.close()

    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT filename_normalized FROM files WHERE filename='photo_image_landscape.jpg'").fetchone()
    conn.close()
    assert row is not None
    assert "image" not in (row["filename_normalized"] or "")
    assert "photo" in (row["filename_normalized"] or "")


def test_run_normalize_updates_checkpoint(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["clip_001.jpg"])
    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute("SELECT * FROM pipeline_checkpoints WHERE stage='normalize'").fetchone()
    conn.close()
    assert row is not None
    assert row["files_processed"] > 0


def test_run_normalize_normalizes_keywords(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["photo_001.jpg"])
    kb_conn = open_kb(kb_path)
    add_pattern_rule(kb_conn, pattern="tuckinleted", action="replace", is_regex=False,
                     replace_with="Tuck Inlet", replace_type="correction")
    kb_conn.close()

    corpus_conn = open_corpus(corpus_path)
    file_id = corpus_conn.execute("SELECT id FROM files LIMIT 1").fetchone()["id"]
    corpus_conn.execute(
        "INSERT INTO file_metadata_keywords (file_id, canonical_name, keyword) VALUES (?, 'keywords', 'tuckinleted')",
        (file_id,),
    )
    corpus_conn.commit()
    corpus_conn.close()

    _run_norm(corpus_path, kb_path)

    conn = open_corpus(corpus_path)
    row = conn.execute(
        "SELECT normalized_keyword FROM file_metadata_keywords WHERE keyword='tuckinleted'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["normalized_keyword"] == "Tuck Inlet"


def test_run_normalize_auto_resolves_tokens_matching_pattern_rule(tmp_path):
    corpus_path, kb_path = _setup(tmp_path, ["img_1234567890.jpg"])

    # Seed an analyse_token that matches the reject rule
    corpus_conn = open_corpus(corpus_path)
    corpus_conn.execute(
        """
        INSERT INTO analyse_tokens
            (token, pattern_class, semantic_type, frequency, file_count,
             proposed_action, proposed_extract_as, is_cross_source, depth_position)
        VALUES ('1234567890', 'numeric', 'id', 1, 1, 'reject', NULL, 0, 0)
        """,
    )
    # Seed a token that should NOT be resolved (no matching rule)
    corpus_conn.execute(
        """
        INSERT INTO analyse_tokens
            (token, pattern_class, semantic_type, frequency, file_count,
             proposed_action, proposed_extract_as, is_cross_source, depth_position)
        VALUES ('img', 'word', 'descriptor', 1, 1, 'keep', NULL, 0, 0)
        """,
    )
    corpus_conn.commit()
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    add_pattern_rule(kb_conn, pattern=r"\d{10}$", action="reject", is_regex=True, label="guid-reject")
    kb_conn.close()

    _run_norm(corpus_path, kb_path)

    corpus_conn = open_corpus(corpus_path)
    rows = corpus_conn.execute(
        "SELECT token, status FROM analyse_tokens ORDER BY token"
    ).fetchall()
    corpus_conn.close()

    statuses = {r["token"]: r["status"] for r in rows}
    assert statuses["1234567890"] == "decided"
    assert statuses["img"] == "pending"
