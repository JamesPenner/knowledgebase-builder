"""Unit tests for Stage 3c — Summarize: prompt building and chunking."""
from src.stages.summarize import (
    _build_system_prompt,
    _build_user_prompt,
    _chunk_transcript,
)
from src.text.context import FileContext


def _ctx(
    description=None,
    transcript=None,
    transcript_attributed=False,
    derived_tags=None,
    entity_names=None,
    filename="photo.jpg",
    metadata_date="2024-01-15",
    metadata_location="Vancouver, BC",
    vocab_terms=None,
) -> FileContext:
    return FileContext(
        file_id=1,
        filename=filename,
        description=description,
        transcript=transcript,
        transcript_attributed=transcript_attributed,
        summary_text=None,
        derived_tags=derived_tags or [],
        entity_names=entity_names or [],
        captured_fields=[],
        metadata_date=metadata_date,
        metadata_location=metadata_location,
        enrichment_text="",
        vocab_terms=vocab_terms or [],
    )


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestBuildUserPromptCase1:
    def test_description_only_selects_case1(self):
        ctx = _ctx(description="A sunny beach.")
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "Visual description:" in prompt
        assert "Transcript:" not in prompt
        assert "Attributed transcript:" not in prompt

    def test_description_only_contains_write_instruction(self):
        ctx = _ctx(description="A sunny beach.")
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "150-word summary" in prompt


class TestBuildUserPromptCase2:
    def test_transcript_only_selects_case2(self):
        ctx = _ctx(transcript="Hello world.")
        prompt = _build_user_prompt(ctx, target_words=100)
        assert "Transcript:" in prompt
        assert "Visual description:" not in prompt

    def test_attributed_transcript_label(self):
        ctx = _ctx(transcript="Speaker A: hi.", transcript_attributed=True)
        prompt = _build_user_prompt(ctx, target_words=100)
        assert "Attributed transcript:" in prompt


class TestBuildUserPromptCase3:
    def test_combined_selects_case3(self):
        ctx = _ctx(description="A dog runs.", transcript="The dog barks.")
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "Visual description" in prompt
        assert "Transcript" in prompt

    def test_combined_transcript_is_authoritative(self):
        ctx = _ctx(description="A dog runs.", transcript="The dog barks.")
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "authoritative" in prompt

    def test_combined_description_is_inferred(self):
        ctx = _ctx(description="A dog runs.", transcript="The dog barks.")
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "inferred" in prompt


class TestBuildUserPromptInjections:
    def test_vocab_terms_appear_with_soft_guidance(self):
        ctx = _ctx(description="A photo.", vocab_terms=["celebration", "outdoors"])
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "celebration" in prompt
        assert "outdoors" in prompt
        assert "Relevant vocabulary" in prompt

    def test_attributed_transcript_label_in_combined(self):
        ctx = _ctx(description="A meeting.", transcript="Bob: Hello.", transcript_attributed=True)
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "Attributed transcript" in prompt

    def test_empty_context_omits_blank_lines(self):
        ctx = _ctx(
            description="Something.",
            filename="",
            metadata_date="",
            metadata_location="",
            derived_tags=[],
            vocab_terms=[],
        )
        prompt = _build_user_prompt(ctx, target_words=150)
        assert "File: \n" not in prompt
        assert "Date: \n" not in prompt
        assert "Location: \n" not in prompt
        assert "Tags: \n" not in prompt


class TestBuildSystemPrompt:
    def test_focus_string_appears(self):
        system = _build_system_prompt(focus="family events")
        assert "family events" in system

    def test_no_focus_omits_domain_line(self):
        system = _build_system_prompt(focus="")
        assert "DOMAIN FOCUS" not in system

    def test_always_contains_base_instruction(self):
        system = _build_system_prompt(focus="")
        assert "summarization assistant" in system


# ---------------------------------------------------------------------------
# Chunk splitting
# ---------------------------------------------------------------------------

class TestChunkTranscript:
    def test_below_threshold_returns_single_chunk(self):
        text = " ".join(f"word{i}" for i in range(100))
        chunks = _chunk_transcript(text, max_tokens=500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_above_threshold_produces_multiple_chunks(self):
        text = " ".join(f"word{i}" for i in range(200))
        chunks = _chunk_transcript(text, max_tokens=100)
        assert len(chunks) > 1

    def test_overlap_preserves_words_at_boundary(self):
        words = [f"w{i}" for i in range(30)]
        text = " ".join(words)
        chunks = _chunk_transcript(text, max_tokens=15, overlap_ratio=0.2)
        assert len(chunks) >= 2
        # Last words of chunk[0] should appear at start of chunk[1]
        c0_words = chunks[0].split()
        c1_words = chunks[1].split()
        overlap = max(1, round(15 * 0.2))
        assert c0_words[-overlap:] == c1_words[:overlap]
