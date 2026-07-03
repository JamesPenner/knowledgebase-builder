"""Verify that ModelLoadError is the same class across all stage modules."""

import pytest
from src.llm.session import ModelLoadError


def test_face_raises_shared_model_load_error():
    from src.stages.face import ModelLoadError as FaceErr
    assert FaceErr is ModelLoadError


def test_voice_raises_shared_model_load_error():
    from src.stages.voice import ModelLoadError as VoiceErr
    assert VoiceErr is ModelLoadError


def test_transcribe_raises_shared_model_load_error():
    from src.stages.transcribe import ModelLoadError as TranscribeErr
    assert TranscribeErr is ModelLoadError


def test_aesthetic_raises_shared_model_load_error():
    from src.stages.aesthetic import ModelLoadError as AestheticErr
    assert AestheticErr is ModelLoadError


def test_catch_face_error_as_session_error():
    from src.stages.face import ModelLoadError as FaceErr
    with pytest.raises(ModelLoadError):
        raise FaceErr("missing model")


def test_catch_voice_error_as_session_error():
    from src.stages.voice import ModelLoadError as VoiceErr
    with pytest.raises(ModelLoadError):
        raise VoiceErr("missing model")
