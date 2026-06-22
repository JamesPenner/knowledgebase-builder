"""Unit tests for Stage 3c — Summarize: prompt building, chunking, LLM call."""
from unittest.mock import MagicMock

from src.stages.summarize import (
    _build_prompt,
    _call_llm,
    _chunk_transcript,
)


def _ctx(
    description=None,
    transcript=None,
    attributed=False,
    derived_tags=None,
    entity_names=None,
    normalized_filename="photo.jpg",
    captured_date="2024-01-15",
    captured_location="Vancouver, BC",
    vocab_terms=None,
):
    return {
        "description": description,
        "transcript": transcript,
        "attributed": attributed,
        "derived_tags": derived_tags or [],
        "entity_names": entity_names or [],
        "normalized_filename": normalized_filename,
        "captured_date": captured_date,
        "captured_location": captured_location,
        "vocab_terms": vocab_terms or [],
    }


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestBuildPromptCase1:
    def test_description_only_selects_case1(self):
        ctx = _ctx(description="A sunny beach.")
        prompt = _build_prompt(ctx, focus="", target_words=150)
        assert "Visual description:" in prompt
        assert "Transcript:" not in prompt
        assert "Attributed transcript:" not in prompt

    def test_description_only_contains_write_instruction(self):
        ctx = _ctx(description="A sunny beach.")
        prompt = _build_prompt(ctx, focus="", target_words=150)
        assert "150-word summary" in prompt


class TestBuildPromptCase2:
    def test_transcript_only_selects_case2(self):
        ctx = _ctx(transcript="Hello world.")
        prompt = _build_prompt(ctx, focus="", target_words=100)
        assert "Transcript:" in prompt
        assert "Visual description:" not in prompt

    def test_attributed_transcript_label(self):
        ctx = _ctx(transcript="Speaker A: hi.", attributed=True)
        prompt = _build_prompt(ctx, focus="", target_words=100)
        assert "Attributed transcript:" in prompt


class TestBuildPromptCase3:
    def test_combined_selects_case3(self):
        ctx = _ctx(description="A dog runs.", transcript="The dog barks.")
        prompt = _build_prompt(ctx, focus="", target_words=150)
        assert "Visual description:" in prompt
        assert "Transcript:" in prompt

    def test_combined_integrate_instruction(self):
        ctx = _ctx(description="A dog runs.", transcript="The dog barks.")
        prompt = _build_prompt(ctx, focus="", target_words=150)
        assert "integrating both" in prompt


class TestBuildPromptInjections:
    def test_focus_string_appears(self):
        ctx = _ctx(description="Party photos.")
        prompt = _build_prompt(ctx, focus="family events", target_words=150)
        assert "family events" in prompt

    def test_vocab_terms_appear_with_soft_guidance(self):
        ctx = _ctx(description="A photo.", vocab_terms=["celebration", "outdoors"])
        prompt = _build_prompt(ctx, focus="", target_words=150)
        assert "celebration" in prompt
        assert "outdoors" in prompt
        assert "Relevant vocabulary" in prompt

    def test_attributed_transcript_label_in_combined(self):
        ctx = _ctx(description="A meeting.", transcript="Bob: Hello.", attributed=True)
        prompt = _build_prompt(ctx, focus="", target_words=150)
        assert "Attributed transcript:" in prompt

    def test_empty_context_omits_blank_lines(self):
        ctx = _ctx(
            description="Something.",
            normalized_filename="",
            captured_date="",
            captured_location="",
            derived_tags=[],
            vocab_terms=[],
        )
        prompt = _build_prompt(ctx, focus="", target_words=150)
        assert "File: \n" not in prompt
        assert "Date: \n" not in prompt
        assert "Location: \n" not in prompt
        assert "Tags: \n" not in prompt


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


# ---------------------------------------------------------------------------
# LLM call wrapper
# ---------------------------------------------------------------------------

class TestCallLlm:
    def test_returns_stripped_text(self):
        mock_llm = MagicMock()
        mock_llm.return_value = {"choices": [{"text": "  A nice summary.  "}]}
        result = _call_llm(mock_llm, "prompt")
        assert result == "A nice summary."

    def test_empty_response_returns_empty_string(self):
        mock_llm = MagicMock()
        mock_llm.return_value = {"choices": [{"text": ""}]}
        result = _call_llm(mock_llm, "prompt")
        assert result == ""

    def test_exception_returns_empty_string(self):
        mock_llm = MagicMock(side_effect=RuntimeError("boom"))
        result = _call_llm(mock_llm, "prompt")
        assert result == ""
