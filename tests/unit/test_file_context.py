"""Tests for FileContext — build_file_context() integration tests and prompt unit tests."""
from src.text.context import FileContext, build_file_context


# ---------------------------------------------------------------------------
# Helpers for seeding corpus DB rows
# ---------------------------------------------------------------------------

def _seed_source(conn):
    from src.db.corpus import add_source
    return add_source(conn, "/photos")


def _seed_file(conn, source_id, path="file.jpg", filename="file.jpg"):
    conn.execute(
        "INSERT INTO files (source_id, path, filename, ext, file_type, file_size, mtime)"
        " VALUES (?, ?, ?, '.jpg', 'images', 1000, 0.0)",
        (source_id, path, filename),
    )
    conn.commit()
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]


# ---------------------------------------------------------------------------
# build_file_context() integration tests (real SQLite in tmp_path)
# ---------------------------------------------------------------------------

class TestBuildContextDescription:
    def test_description_populated_no_transcript(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO descriptions (file_id, description_raw, description_normalized, pass1_status)"
            " VALUES (?, 'raw desc', 'norm desc', 'done')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.description == "norm desc"
        assert ctx.transcript is None

    def test_normalised_preferred_over_raw(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO descriptions (file_id, description_raw, description_normalized, pass1_status)"
            " VALUES (?, 'raw', 'normalised', 'done')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.description == "normalised"


class TestBuildContextTranscript:
    def test_attributed_transcript_from_segments(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO transcript_segments (file_id, start_ms, end_ms, text, speaker_label)"
            " VALUES (?, 0, 1000, 'Hello world', 'SPEAKER_A')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.transcript_attributed is True
        assert "SPEAKER_A: Hello world" in ctx.transcript

    def test_plain_transcript_from_segments_without_labels(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO transcript_segments (file_id, start_ms, end_ms, text, speaker_label)"
            " VALUES (?, 0, 1000, 'Hello', NULL)",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.transcript_attributed is False
        assert ctx.transcript == "Hello"

    def test_plain_transcript_fallback_to_transcriptions_table(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO transcriptions (file_id, transcript_text, transcribe_status)"
            " VALUES (?, 'plain text', 'done')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.transcript == "plain text"
        assert ctx.transcript_attributed is False


class TestBuildContextSummaryText:
    def test_done_summary_populated(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_summaries (file_id, summary_text, model, prompt_version, status)"
            " VALUES (?, 'my summary', 'test-model', 'v1', 'done')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.summary_text == "my summary"

    def test_failed_summary_returns_none(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_summaries (file_id, summary_text, model, prompt_version, status)"
            " VALUES (?, NULL, 'test-model', 'v1', 'failed')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.summary_text is None


class TestBuildContextEntityNames:
    def test_entity_names_populated(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_entity_matches"
            " (file_id, table_name, matched_value, match_source, payload_json, stale)"
            " VALUES (?, 'people', 'Alice Smith', 'text', '{}', 0)",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert "Alice Smith" in ctx.entity_names


class TestBuildContextMetadata:
    def test_metadata_date_and_location_populated(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
            " VALUES (?, 'captured_date', '2024-06-15', 'date')",
            (fid,),
        )
        corpus_db.execute(
            "INSERT INTO file_geolabels (file_id, country, state, custom_region, method, confidence)"
            " VALUES (?, 'Canada', 'BC', 'Vancouver', 'gps', 'high')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.metadata_date == "2024-06-15"
        assert "Vancouver" in ctx.metadata_location
        assert "BC" in ctx.metadata_location
        assert "Canada" in ctx.metadata_location


class TestBuildContextNullKb:
    def test_no_kb_conn_returns_empty_vocab(self, corpus_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)

        ctx = build_file_context(corpus_db, None, fid)

        assert ctx.vocab_terms == []

    def test_no_kb_conn_does_not_raise(self, corpus_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        build_file_context(corpus_db, None, fid)


class TestBuildContextEmptyFile:
    def test_empty_file_all_optional_fields_default(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert ctx.description is None
        assert ctx.transcript is None
        assert ctx.transcript_attributed is False
        assert ctx.summary_text is None
        assert ctx.derived_tags == []
        assert ctx.entity_names == []
        assert ctx.captured_fields == []
        assert ctx.metadata_date is None
        assert ctx.metadata_location is None
        assert ctx.enrichment_text == ""
        assert ctx.vocab_terms == []


# ---------------------------------------------------------------------------
# Prompt construction unit tests (no DB — inject FileContext directly)
# ---------------------------------------------------------------------------

def _make_ctx(**kwargs) -> FileContext:
    defaults = dict(
        file_id=1,
        filename="test.jpg",
        description=None,
        transcript=None,
        transcript_attributed=False,
        summary_text=None,
        derived_tags=[],
        entity_names=[],
        captured_fields=[],
        metadata_date=None,
        metadata_location=None,
        enrichment_text="",
        vocab_terms=[],
    )
    defaults.update(kwargs)
    return FileContext(**defaults)


class TestDescribePromptConstruction:
    def test_captured_date_field_appears_in_output(self):
        from src.stages.describe import _build_describe_prompt
        fields = [{"field_name": "file_date", "value": "2023-07-04", "value_type": "date"}]
        prompt = _build_describe_prompt(fields, [], focus="")
        assert "2023-07-04" in prompt

    def test_base_prompt_override_used_instead_of_default(self):
        from src.stages.describe import _build_describe_prompt, _BASE_PROMPT
        custom = "Identify all vehicles in the frame."
        prompt = _build_describe_prompt([], [], focus="", base_prompt=custom)
        assert custom in prompt
        assert _BASE_PROMPT not in prompt

    def test_default_base_prompt_used_when_not_overridden(self):
        from src.stages.describe import _build_describe_prompt, _BASE_PROMPT
        prompt = _build_describe_prompt([], [], focus="")
        assert _BASE_PROMPT in prompt


class TestRetagPromptConstruction:
    def test_vocab_terms_appear_in_prompt(self):
        from src.stages.retag import _build_prompt
        ctx = _make_ctx(
            description="A sunny beach scene.",
            vocab_terms=["beach", "summer", "outdoors"],
        )
        prompt = _build_prompt(ctx, focus="")
        assert "beach" in prompt
        assert "summer" in prompt
        assert "outdoors" in prompt

    def test_description_appears_in_prompt(self):
        from src.stages.retag import _build_prompt
        ctx = _make_ctx(description="Children playing in a park.")
        prompt = _build_prompt(ctx, focus="")
        assert "Children playing in a park." in prompt


class TestSummarizePromptConstruction:
    def test_description_and_transcript_both_appear(self):
        from src.stages.summarize import _build_user_prompt
        ctx = _make_ctx(description="A busy street.", transcript="Cars honking.")
        prompt = _build_user_prompt(ctx, target_words=100)
        assert "A busy street." in prompt
        assert "Cars honking." in prompt

    def test_transcript_only_no_description(self):
        from src.stages.summarize import _build_user_prompt
        ctx = _make_ctx(transcript="The speaker introduces the topic.")
        prompt = _build_user_prompt(ctx, target_words=100)
        assert "The speaker introduces the topic." in prompt
        assert "Visual description" not in prompt


class TestSuggestTextPool:
    def test_text_pool_includes_description_enrichment_and_summary(self):
        from src.stages.suggest import _build_file_text
        ctx = _make_ctx(
            description="A mountain landscape.",
            enrichment_text="Vancouver BC 2023",
            summary_text="A sweeping view of mountains near Vancouver.",
        )
        text = _build_file_text(ctx)
        assert "mountain landscape" in text
        assert "Vancouver BC 2023" in text
        assert "sweeping view" in text

    def test_text_pool_handles_missing_optional_fields(self):
        from src.stages.suggest import _build_file_text
        ctx = _make_ctx(enrichment_text="only enrichment")
        text = _build_file_text(ctx)
        assert "only enrichment" in text
