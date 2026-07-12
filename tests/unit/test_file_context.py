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


class TestBuildContextEntityNamesFiltering:
    def test_people_table_excluded_when_people_disabled(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_entity_matches"
            " (file_id, table_name, matched_value, match_source, payload_json, stale)"
            " VALUES (?, 'people', 'Alice Smith', 'text', '{}', 0)",
            (fid,),
        )
        corpus_db.execute(
            "INSERT INTO file_entity_matches"
            " (file_id, table_name, matched_value, match_source, payload_json, stale)"
            " VALUES (?, 'locations', 'Vancouver', 'text', '{}', 0)",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"places", "dates"})
        )

        assert "Alice Smith" not in ctx.entity_names
        assert "Vancouver" in ctx.entity_names

    def test_locations_table_excluded_when_places_disabled(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_entity_matches"
            " (file_id, table_name, matched_value, match_source, payload_json, stale)"
            " VALUES (?, 'people', 'Alice Smith', 'text', '{}', 0)",
            (fid,),
        )
        corpus_db.execute(
            "INSERT INTO file_entity_matches"
            " (file_id, table_name, matched_value, match_source, payload_json, stale)"
            " VALUES (?, 'locations', 'Vancouver', 'text', '{}', 0)",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"people", "dates"})
        )

        assert "Alice Smith" in ctx.entity_names
        assert "Vancouver" not in ctx.entity_names

    def test_custom_entity_table_unaffected_by_toggles(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_entity_matches"
            " (file_id, table_name, matched_value, match_source, payload_json, stale)"
            " VALUES (?, 'pets', 'Rex', 'text', '{}', 0)",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid, enabled_categories=frozenset())

        assert "Rex" in ctx.entity_names


class TestBuildContextMetadataFiltering:
    def test_location_blanked_when_places_disabled(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_geolabels (file_id, country, state, custom_region, method, confidence)"
            " VALUES (?, 'Canada', 'BC', 'Vancouver', 'gps', 'high')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"people", "dates"})
        )

        assert ctx.metadata_location is None

    def test_metadata_date_unaffected_by_dates_toggle(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_metadata_fields (file_id, canonical_name, value, value_type)"
            " VALUES (?, 'captured_date', '2024-06-15', 'date')",
            (fid,),
        )
        corpus_db.commit()

        ctx_all_enabled = build_file_context(corpus_db, kb_db, fid)
        ctx_dates_off = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"people", "places"})
        )
        ctx_nothing_enabled = build_file_context(corpus_db, kb_db, fid, enabled_categories=frozenset())

        assert ctx_all_enabled.metadata_date == "2024-06-15"
        assert ctx_dates_off.metadata_date == "2024-06-15"
        assert ctx_nothing_enabled.metadata_date == "2024-06-15"


class TestBuildContextDerivedTagsFiltering:
    def test_calendar_tag_excluded_when_dates_disabled(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_derived_tags (file_id, tag, category, source, rule_id)"
            " VALUES (?, 'Summer', 'calendar', 'classify', 1)",
            (fid,),
        )
        corpus_db.execute(
            "INSERT INTO file_derived_tags (file_id, tag, category, source, rule_id)"
            " VALUES (?, 'Sharp', 'technical', 'classify', 2)",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"people", "places"})
        )

        assert "Summer" not in ctx.derived_tags
        assert "Sharp" in ctx.derived_tags

    def test_life_event_tag_requires_both_people_and_dates(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_derived_tags (file_id, tag, category, source, rule_id)"
            " VALUES (?, 'Birthday', 'life_event', 'classify', 3)",
            (fid,),
        )
        corpus_db.commit()

        ctx_no_dates = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"people", "places"})
        )
        ctx_no_people = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"dates", "places"})
        )
        ctx_both = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"people", "dates", "places"})
        )

        assert "Birthday" not in ctx_no_dates.derived_tags
        assert "Birthday" not in ctx_no_people.derived_tags
        assert "Birthday" in ctx_both.derived_tags

    def test_stale_tag_suppressed_after_toggle_flipped_post_classify(self, corpus_db, kb_db):
        # Simulates: classify ran while "dates" was enabled and wrote a
        # calendar tag; "dates" is later disabled without re-running
        # classify. The stale row is still sitting in file_derived_tags —
        # filtering has to happen at read time, not rely on classify having
        # respected the setting when it wrote the tag.
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        corpus_db.execute(
            "INSERT INTO file_derived_tags (file_id, tag, category, source, rule_id)"
            " VALUES (?, 'Christmas', 'calendar', 'classify', 4)",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"people", "places"})
        )

        assert "Christmas" not in ctx.derived_tags


class TestBuildContextTranscriptSpeakerFiltering:
    def _seed_voice(self, conn, file_id, cluster_label=None):
        cur = conn.execute(
            "INSERT INTO voice_speaker_clusters (centroid, member_count, spread, label)"
            " VALUES (X'00', 1, 0.1, ?)",
            (cluster_label,),
        )
        cluster_id = cur.lastrowid
        conn.execute(
            "INSERT INTO file_voice_segments"
            " (file_id, segment_index, start_ms, end_ms, speaker_label, cluster_id)"
            " VALUES (?, 0, 0, 1000, 'SPEAKER_00', ?)",
            (file_id, cluster_id),
        )
        conn.commit()

    def test_person_resolved_label_used_when_people_enabled(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        self._seed_voice(corpus_db, fid, cluster_label="Cluster A")
        corpus_db.execute(
            "INSERT INTO transcript_segments (file_id, start_ms, end_ms, text, speaker_label)"
            " VALUES (?, 0, 1000, 'Hello world', 'Alice')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(corpus_db, kb_db, fid)

        assert "Alice: Hello world" in ctx.transcript

    def test_falls_back_to_cluster_label_when_people_disabled(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        self._seed_voice(corpus_db, fid, cluster_label="Cluster A")
        corpus_db.execute(
            "INSERT INTO transcript_segments (file_id, start_ms, end_ms, text, speaker_label)"
            " VALUES (?, 0, 1000, 'Hello world', 'Alice')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"places", "dates"})
        )

        assert "Alice" not in ctx.transcript
        assert "Cluster A: Hello world" in ctx.transcript

    def test_falls_back_to_raw_label_when_no_cluster_label_and_people_disabled(self, corpus_db, kb_db):
        src = _seed_source(corpus_db)
        fid = _seed_file(corpus_db, src)
        self._seed_voice(corpus_db, fid, cluster_label=None)
        corpus_db.execute(
            "INSERT INTO transcript_segments (file_id, start_ms, end_ms, text, speaker_label)"
            " VALUES (?, 0, 1000, 'Hello world', 'Alice')",
            (fid,),
        )
        corpus_db.commit()

        ctx = build_file_context(
            corpus_db, kb_db, fid, enabled_categories=frozenset({"places", "dates"})
        )

        assert "Alice" not in ctx.transcript
        assert "SPEAKER_00: Hello world" in ctx.transcript


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
    def test_metadata_text_includes_enrichment_and_tags(self):
        from src.stages.suggest import _build_metadata_text
        ctx = _make_ctx(
            enrichment_text="Vancouver BC 2023",
            derived_tags=["outdoor", "sunny"],
        )
        text = _build_metadata_text(ctx)
        assert "Vancouver BC 2023" in text
        assert "outdoor" in text

    def test_prose_text_includes_description_summary_transcript(self):
        from src.stages.suggest import _build_prose_text
        ctx = _make_ctx(
            description="A mountain landscape.",
            summary_text="A sweeping view of mountains near Vancouver.",
            transcript="The guide points to the peak.",
        )
        text = _build_prose_text(ctx)
        assert "mountain landscape" in text
        assert "sweeping view" in text
        assert "guide points" in text

    def test_metadata_text_handles_missing_optional_fields(self):
        from src.stages.suggest import _build_metadata_text
        ctx = _make_ctx(enrichment_text="only enrichment")
        text = _build_metadata_text(ctx)
        assert "only enrichment" in text

    def test_prose_text_empty_when_no_prose_sources(self):
        from src.stages.suggest import _build_prose_text
        ctx = _make_ctx(enrichment_text="canyon river", derived_tags=["sunny"])
        assert _build_prose_text(ctx).strip() == ""
