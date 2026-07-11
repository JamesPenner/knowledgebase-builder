"""Integration tests for Stage 1.8 (Classify)."""
import json
import threading


from src.config import Config
from src.db.corpus import open_corpus
from src.db.kb import open_kb
from src.pipeline.progress import NullProgressReporter
from src.stages.classify import run_classify


def _seed_file(corpus_conn, captured: dict | None = None, metadata: dict | None = None) -> int:
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', '.jpg', 'image', 1, 0.0)"
    )
    if captured:
        for field_name, value in captured.items():
            corpus_conn.execute(
                "INSERT INTO file_captured_fields (file_id, field_name, value)"
                " VALUES (1, ?, ?)",
                (field_name, value),
            )
    if metadata:
        for canonical, value in metadata.items():
            corpus_conn.execute(
                "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
                " VALUES (1, ?, ?, 'str')",
                (canonical, value),
            )
    corpus_conn.commit()
    return 1


def _get_tags(corpus_conn, file_id: int = 1) -> set[str]:
    rows = corpus_conn.execute(
        "SELECT tag FROM file_derived_tags WHERE file_id = ?", (file_id,)
    ).fetchall()
    return {r["tag"] for r in rows}


def test_classify_christmas_from_captured_date(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "2023-12-25", "file_date_precision": "full"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Christmas Day" in tags


def test_classify_season_summer(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "2022-07-15"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Summer" in tags


def test_classify_landscape(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, metadata={"exif_width": "4000", "exif_height": "2000"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Landscape" in tags
    assert "Portrait" not in tags


def test_classify_telephoto(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, metadata={"focal_length_35mm": "200"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "telephoto" in tags or "super_telephoto" in tags


def test_classify_skips_low_precision_for_fixed_event(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    # Only year precision — Christmas needs full precision
    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "2023", "file_date_precision": "year"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Christmas Day" not in tags


# ---------------------------------------------------------------------------
# KB.AM1 — Knowledge Settings domain filtering
# ---------------------------------------------------------------------------

def test_classify_dates_disabled_suppresses_calendar_tag_not_technical(tmp_path):
    from src.db.kb import set_knowledge_category_enabled

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(
        corpus_conn,
        captured={"file_date": "2023-12-25", "file_date_precision": "full"},
        metadata={"exif_width": "4000", "exif_height": "2000"},
    )
    corpus_conn.close()

    kb_conn = open_kb(kb_path)
    set_knowledge_category_enabled(kb_conn, "dates", False)
    kb_conn.close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Christmas Day" not in tags
    assert "Landscape" in tags


def _seed_person_with_birthday(kb_conn, corpus_conn, *, birth_date="2023-12-25", file_id=1):
    from src.db.corpus import upsert_entity_match
    from src.db.kb import add_life_event, add_person_name, upsert_person

    pid = upsert_person(kb_conn, "Alice Johnson", first_name="Alice", last_name="Johnson")
    add_person_name(kb_conn, pid, "Alice Johnson", is_metadata_form=True)
    add_life_event(kb_conn, pid, "birth", birth_date)
    kb_conn.commit()

    upsert_entity_match(
        corpus_conn, file_id, "people", "alice johnson", "text", json.dumps({"person_id": pid})
    )
    corpus_conn.commit()
    return pid


def test_classify_life_event_requires_both_people_and_dates(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "2023-12-25", "file_date_precision": "full"})
    kb_conn = open_kb(kb_path)
    _seed_person_with_birthday(kb_conn, corpus_conn)
    kb_conn.close()
    corpus_conn.close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Alice Johnson's Birthday" in tags


def test_classify_life_event_suppressed_when_people_disabled(tmp_path):
    from src.db.kb import set_knowledge_category_enabled

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "2023-12-25", "file_date_precision": "full"})
    kb_conn = open_kb(kb_path)
    _seed_person_with_birthday(kb_conn, corpus_conn)
    set_knowledge_category_enabled(kb_conn, "people", False)
    kb_conn.close()
    corpus_conn.close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Alice Johnson's Birthday" not in tags
    # Calendar tags for this date are unaffected by the People toggle.
    assert "Christmas Day" in tags


def test_classify_life_event_suppressed_when_dates_disabled(tmp_path):
    from src.db.kb import set_knowledge_category_enabled

    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "2023-12-25", "file_date_precision": "full"})
    kb_conn = open_kb(kb_path)
    _seed_person_with_birthday(kb_conn, corpus_conn)
    set_knowledge_category_enabled(kb_conn, "dates", False)
    kb_conn.close()
    corpus_conn.close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Alice Johnson's Birthday" not in tags


def test_classify_decade_tag(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "1985-06-10"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "1980s" in tags


def test_classify_updates_checkpoint(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn)
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    row = corpus_conn.execute(
        "SELECT * FROM pipeline_checkpoints WHERE stage = 'classify'"
    ).fetchone()
    corpus_conn.close()
    assert row is not None
    assert row["files_processed"] == 1


def test_classify_exif_date_fallback(tmp_path):
    """classify uses exif_date_taken when file_date is absent."""
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, metadata={"exif_date_taken": "2023-12-25"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "Christmas Day" in tags


def test_classify_is_idempotent(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"

    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn, captured={"file_date": "2022-07-04"})
    corpus_conn.close()
    open_kb(kb_path).close()

    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())

    corpus_conn = open_corpus(corpus_path)
    count = corpus_conn.execute("SELECT COUNT(*) FROM file_derived_tags WHERE file_id = 1").fetchone()[0]
    corpus_conn.close()
    # Running twice should not double the tags
    assert count > 0
    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    corpus_conn = open_corpus(corpus_path)
    count2 = corpus_conn.execute("SELECT COUNT(*) FROM file_derived_tags WHERE file_id = 1").fetchone()[0]
    corpus_conn.close()
    assert count2 == count


# ---------------------------------------------------------------------------
# get_fields_for_classify derived fields
# ---------------------------------------------------------------------------

def _seed_file_ext(corpus_conn, ext=".jpg") -> int:
    corpus_conn.execute(
        "INSERT INTO sources (path, file_type, recursive) VALUES ('/', 'images', 1)"
    )
    corpus_conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (1, '/img.jpg', 'img.jpg', ?, 'images', 1, 0.0)",
        (ext,),
    )
    corpus_conn.commit()
    return 1


def test_get_fields_file_format_from_ext(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn, ext=".jpg")
    fields = get_fields_for_classify(conn, 1)
    assert fields["file_format"] == "jpg"
    conn.close()


def test_get_fields_file_format_raw(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn, ext=".ARW")
    fields = get_fields_for_classify(conn, 1)
    assert fields["file_format"] == "arw"
    conn.close()


def test_get_fields_gps_present_set_when_lat_present(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn)
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'exif_gps_lat', '49.2827', 'float')"
    )
    conn.commit()
    fields = get_fields_for_classify(conn, 1)
    assert fields.get("gps_present") == "true"
    conn.close()


def test_get_fields_gps_present_absent_when_no_lat(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn)
    fields = get_fields_for_classify(conn, 1)
    assert "gps_present" not in fields
    conn.close()


def test_get_fields_hour_of_day_from_date_taken(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn)
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'exif_date_taken', '2023-06-15T14:30:00', 'str')"
    )
    conn.commit()
    fields = get_fields_for_classify(conn, 1)
    assert fields["hour_of_day"] == "14"
    conn.close()


def test_get_fields_flash_fired_derivation(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn)
    # EXIF Flash value 1 = flash fired (bit 0 set)
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'flash', '1', 'int')"
    )
    conn.commit()
    fields = get_fields_for_classify(conn, 1)
    assert fields["flash_fired"] == "1"
    conn.close()


def test_get_fields_no_flash_derivation(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn)
    # EXIF Flash value 16 = no flash, flash did not fire (bit 0 clear)
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'flash', '16', 'int')"
    )
    conn.commit()
    fields = get_fields_for_classify(conn, 1)
    assert fields["flash_fired"] == "0"
    conn.close()


def test_get_fields_aspect_ratio_derived(tmp_path):
    from src.db.corpus import get_fields_for_classify
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn)
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'exif_width', '3000', 'int')"
    )
    conn.execute(
        "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
        " VALUES (1, 'exif_height', '2000', 'int')"
    )
    conn.commit()
    fields = get_fields_for_classify(conn, 1)
    assert abs(float(fields["aspect_ratio"]) - 1.5) < 0.001
    conn.close()


def test_get_fields_quality_metrics_exposed(tmp_path):
    from src.db.corpus import get_fields_for_classify, upsert_quality_score
    conn = open_corpus(tmp_path / "corpus.db")
    _seed_file_ext(conn)
    upsert_quality_score(conn, 1, 500.0, 0.35, 0.05, 0.02, 1,
                         luminance_std_dev=0.15, saturation_mean=0.40, dominant_hue=220.0)
    conn.commit()
    fields = get_fields_for_classify(conn, 1)
    assert abs(float(fields["exposure"]) - 0.35) < 0.001
    assert abs(float(fields["luminance_std_dev"]) - 0.15) < 0.001
    assert abs(float(fields["saturation_mean"]) - 0.40) < 0.001
    assert abs(float(fields["dominant_hue"]) - 220.0) < 0.001
    conn.close()


def test_classify_fires_tonality_from_quality(tmp_path):
    from src.db.corpus import upsert_quality_score
    corpus_path = tmp_path / "corpus.db"
    kb_path = tmp_path / "knowledge.db"
    corpus_conn = open_corpus(corpus_path)
    _seed_file(corpus_conn)
    # Low-key exposure score → low_key tag
    upsert_quality_score(corpus_conn, 1, 100.0, 0.10, 0.0, 0.0, 1)
    corpus_conn.commit()
    corpus_conn.close()
    open_kb(kb_path).close()
    run_classify(corpus_path, kb_path, Config(), NullProgressReporter(), threading.Event())
    corpus_conn = open_corpus(corpus_path)
    tags = _get_tags(corpus_conn)
    corpus_conn.close()
    assert "low_key" in tags
