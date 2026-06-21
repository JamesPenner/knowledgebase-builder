"""Unit tests for describe stage helpers (no model, no DB)."""
from src.stages.describe import _build_describe_prompt, _IMAGE_EXTS, _VIDEO_EXTS


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


def test_video_ext_set_contains_common_types():
    assert ".mp4" in _VIDEO_EXTS
    assert ".mov" in _VIDEO_EXTS
    assert ".jpg" not in _VIDEO_EXTS
