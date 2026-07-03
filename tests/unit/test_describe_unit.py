"""Unit tests for describe stage helpers (no model, no DB)."""
from src.stages.describe import (
    _build_describe_prompt,
    _IMAGE_EXTS,
    _VIDEO_EXTS,
)
from src.llm.session import _resolve_chat_format


def test_build_prompt_with_focus_only():
    prompt = _build_describe_prompt([], [], focus="Transportation infrastructure")
    assert "DOMAIN FOCUS: Transportation infrastructure" in prompt
    assert "Describe this image" in prompt


def test_build_prompt_empty_no_context():
    prompt = _build_describe_prompt([], [], focus="")
    assert prompt.strip() != ""
    assert "Describe" in prompt
    assert "DOMAIN FOCUS" not in prompt


def test_build_prompt_date_field():
    fields = [{"field_name": "file_date_full", "value": "2016-09-29", "value_type": "date"}]
    prompt = _build_describe_prompt(fields, [], focus="")
    assert "filmed on 2016-09-29" in prompt


def test_build_prompt_time_field():
    fields = [{"field_name": "file_time", "value": "09:48:14", "value_type": "time"}]
    prompt = _build_describe_prompt(fields, [], focus="")
    assert "Recorded at 09:48:14" in prompt


def test_build_prompt_code_field():
    fields = [{"field_name": "contract_number", "value": "BC-2019-0042", "value_type": "code"}]
    prompt = _build_describe_prompt(fields, [], focus="")
    assert "Project code: BC-2019-0042" in prompt


def test_build_prompt_derived_tags():
    prompt = _build_describe_prompt([], ["Christmas Day", "1986"], focus="")
    assert "Christmas Day" in prompt
    assert "1986" in prompt
    assert "Confirmed context" in prompt


def test_build_prompt_all_parts():
    fields = [{"field_name": "date", "value": "2020-01-01", "value_type": "date"}]
    tags = ["Winter"]
    prompt = _build_describe_prompt(fields, tags, focus="Domain X")
    assert "DOMAIN FOCUS" in prompt
    assert "2020-01-01" in prompt
    assert "Winter" in prompt
    assert "Describe" in prompt


def test_build_prompt_skips_empty_values():
    fields = [{"field_name": "date", "value": "", "value_type": "date"}]
    prompt = _build_describe_prompt(fields, [], focus="")
    assert "filmed on" not in prompt


def test_build_prompt_skips_numeric_type():
    fields = [{"field_name": "file_size", "value": "1024", "value_type": "numeric"}]
    prompt = _build_describe_prompt(fields, [], focus="")
    assert "1024" not in prompt


def test_image_ext_set_contains_common_types():
    assert ".jpg" in _IMAGE_EXTS
    assert ".jpeg" in _IMAGE_EXTS
    assert ".png" in _IMAGE_EXTS
    assert ".mp4" not in _IMAGE_EXTS


def test_image_exts_includes_raw_formats():
    raw_formats = {".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2"}
    missing = raw_formats - _IMAGE_EXTS
    assert not missing, f"_IMAGE_EXTS missing raw formats: {missing}"


def test_image_exts_superset_of_ingest():
    from src.stages.ingest import _IMAGE_EXTS as ingest_exts
    missing = ingest_exts - _IMAGE_EXTS
    assert not missing, f"describe._IMAGE_EXTS missing formats from ingest: {missing}"


def test_video_ext_set_contains_common_types():
    assert ".mp4" in _VIDEO_EXTS
    assert ".mov" in _VIDEO_EXTS
    assert ".jpg" not in _VIDEO_EXTS


# ---------------------------------------------------------------------------
# _resolve_chat_format
# ---------------------------------------------------------------------------

def test_resolve_chat_format_qwen2_from_mmproj():
    fmt = _resolve_chat_format("mmproj-Qwen2.5-VL-3B-f16.gguf", "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf")
    assert fmt == "qwen2_vl"


def test_resolve_chat_format_qwen2_from_model_when_mmproj_generic():
    # mmproj has a generic name; model name carries the clue
    fmt = _resolve_chat_format("mmproj-model-f16.gguf", "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf")
    assert fmt == "qwen2_vl"


def test_resolve_chat_format_gemma_from_mmproj():
    fmt = _resolve_chat_format("mmproj-gemma-4-12B-it-QAT-BF16.gguf", "gemma-4-12B-it-QAT-Q4_0.gguf")
    assert fmt == "gemma3"


def test_resolve_chat_format_moondream():
    fmt = _resolve_chat_format("moondream2-mmproj-f16.gguf", "moondream2-text-model-f16.gguf")
    assert fmt == "moondream"


def test_resolve_chat_format_fallback_to_llava():
    fmt = _resolve_chat_format("mmproj-unknown-model-f16.gguf", "mystery-model-Q4_K_M.gguf")
    assert fmt == "llava"


def test_resolve_chat_format_qwen3_does_not_match_qwen2_vl():
    # Qwen3.5 is a text/vision model but the pattern "qwen2" must not fire on "qwen3"
    fmt = _resolve_chat_format(
        "Qwen3.5-9B-Claude-4.6-HighIQ-INSTRUCT.mmproj-f16.gguf",
        "Qwen3.5-9B-Claude-4.6-HighIQ-INSTRUCT.Q4_K_S.gguf",
    )
    assert fmt == "llava"
