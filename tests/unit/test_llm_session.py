"""Unit tests for src/llm/session.py — no GPU, no model files; llama_cpp is mocked."""
import gc
import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_llama_cpp():
    """Inject a mock llama_cpp module so tests run without the real package."""
    mock_mod = MagicMock()
    original = sys.modules.get("llama_cpp")
    sys.modules["llama_cpp"] = mock_mod
    sys.modules["llama_cpp.llama_chat_format"] = mock_mod.llama_chat_format
    yield mock_mod
    if original is None:
        sys.modules.pop("llama_cpp", None)
        sys.modules.pop("llama_cpp.llama_chat_format", None)
    else:
        sys.modules["llama_cpp"] = original


def _make_chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ---------------------------------------------------------------------------
# Chat format detection
# ---------------------------------------------------------------------------

def test_resolve_format_qwen2_from_model_path():
    from src.llm.session import _resolve_chat_format
    fmt = _resolve_chat_format("mmproj-model-f16.gguf", "qwen2-vl-7b-instruct-q4.gguf")
    assert fmt == "qwen2_vl"


def test_resolve_format_moondream_from_mmproj():
    from src.llm.session import _resolve_chat_format
    fmt = _resolve_chat_format("moondream2-mmproj-f16.gguf", "generic-model-q4.gguf")
    assert fmt == "moondream"


def test_resolve_format_gemma_from_model_path():
    from src.llm.session import _resolve_chat_format
    fmt = _resolve_chat_format("mmproj-model-f16.gguf", "gemma-3-9b-it-q4.gguf")
    assert fmt == "gemma3"


def test_resolve_format_fallback():
    from src.llm.session import _resolve_chat_format
    fmt = _resolve_chat_format("mmproj-unknown-f16.gguf", "mystery-model-q4.gguf")
    assert fmt == "llava"


# ---------------------------------------------------------------------------
# Retry behaviour — TextSession
# ---------------------------------------------------------------------------

def test_generate_returns_on_first_nonempty(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.return_value = _make_chat_response("result")

    from src.llm.session import TextSession
    with TextSession("any/path.gguf", max_retries=2) as session:
        result = session.generate("sys", "user")

    assert result == "result"
    assert mock_llm.create_chat_completion.call_count == 1


def test_generate_retries_on_empty_string(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.side_effect = [
        _make_chat_response(""),
        _make_chat_response("result"),
    ]

    from src.llm.session import TextSession
    with TextSession("any/path.gguf", max_retries=1) as session:
        result = session.generate("sys", "user")

    assert result == "result"
    assert mock_llm.create_chat_completion.call_count == 2


def test_generate_returns_empty_after_exhausted_retries(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.return_value = _make_chat_response("")

    from src.llm.session import TextSession
    with TextSession("any/path.gguf", max_retries=2) as session:
        result = session.generate("sys", "user")

    assert result == ""
    assert mock_llm.create_chat_completion.call_count == 3


def test_generate_no_retry_on_exception(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.side_effect = RuntimeError("OOM")

    from src.llm.session import TextSession
    with TextSession("any/path.gguf", max_retries=2) as session:
        with pytest.raises(RuntimeError):
            session.generate("sys", "user")

    assert mock_llm.create_chat_completion.call_count == 1


# ---------------------------------------------------------------------------
# VRAM release
# ---------------------------------------------------------------------------

def test_exit_deletes_model_and_collects(mock_llama_cpp, monkeypatch):
    collected = []
    monkeypatch.setattr(gc, "collect", lambda: collected.append(1))

    from src.llm.session import TextSession
    with TextSession("any/path.gguf"):
        pass

    assert len(collected) >= 1


# ---------------------------------------------------------------------------
# ModelLoadError
# ---------------------------------------------------------------------------

def test_text_session_raises_model_load_error_on_bad_path(mock_llama_cpp):
    mock_llama_cpp.Llama.side_effect = RuntimeError("file not found")

    from src.llm.session import ModelLoadError, TextSession
    with pytest.raises(ModelLoadError):
        with TextSession("bad/path.gguf"):
            pass


def test_vision_session_raises_model_load_error_on_bad_path(mock_llama_cpp):
    mock_llama_cpp.Llama.side_effect = RuntimeError("insufficient VRAM")

    from src.llm.session import ModelLoadError, VisionSession
    with pytest.raises(ModelLoadError):
        with VisionSession("bad/model.gguf"):
            pass


# ---------------------------------------------------------------------------
# Retag prompt — no llama2 template
# ---------------------------------------------------------------------------

def test_retag_build_prompt_no_llama2_template():
    from src.stages.retag import _build_prompt
    from src.text.context import FileContext
    ctx = FileContext(
        file_id=1, filename="test.jpg", description="A beautiful sunset.",
        transcript=None, transcript_attributed=False, summary_text=None,
        derived_tags=[], entity_names=[], captured_fields=[],
        metadata_date=None, metadata_location=None,
        enrichment_text="", vocab_terms=["nature", "sky"],
    )
    result = _build_prompt(ctx, focus="")
    assert "[INST]" not in result
    assert "<<SYS>>" not in result


# ---------------------------------------------------------------------------
# Extra: message structure
# ---------------------------------------------------------------------------

def test_text_session_no_system_message_when_system_empty(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.return_value = _make_chat_response("ok")

    from src.llm.session import TextSession
    with TextSession("any/path.gguf") as session:
        session.generate("", "user msg")

    messages = mock_llm.create_chat_completion.call_args[1]["messages"]
    assert all(m["role"] != "system" for m in messages)
    assert messages[0]["role"] == "user"


def test_vision_session_generate_with_images_includes_image_url(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.return_value = _make_chat_response("a photo")

    from src.llm.session import VisionSession
    with VisionSession("any/model.gguf") as session:
        session.generate("", "what is this?", images=[b"fake-jpeg"])

    messages = mock_llm.create_chat_completion.call_args[1]["messages"]
    user_content = messages[-1]["content"]
    assert isinstance(user_content, list)
    assert any(c.get("type") == "image_url" for c in user_content)
    assert any(c.get("type") == "text" for c in user_content)


def test_vision_session_generate_without_images_sends_string_content(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.return_value = _make_chat_response("text result")

    from src.llm.session import VisionSession
    with VisionSession("any/model.gguf") as session:
        session.generate("sys", "user text")

    messages = mock_llm.create_chat_completion.call_args[1]["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")
    assert user_msg["content"] == "user text"


def test_generate_max_tokens_and_temperature_passed_to_llm(mock_llama_cpp):
    mock_llm = mock_llama_cpp.Llama.return_value
    mock_llm.create_chat_completion.return_value = _make_chat_response("ok")

    from src.llm.session import TextSession
    with TextSession("any/path.gguf") as session:
        session.generate("sys", "user", max_tokens=256, temperature=0.5)

    kwargs = mock_llm.create_chat_completion.call_args[1]
    assert kwargs["max_tokens"] == 256
    assert kwargs["temperature"] == 0.5
