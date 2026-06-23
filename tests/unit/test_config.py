import dataclasses
from pathlib import Path

import pytest
import yaml

from src.config import Config, load_config


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def test_defaults_apply_when_no_config_files():
    cfg = load_config(None)
    defaults = Config()
    assert cfg.host == defaults.host
    assert cfg.port == defaults.port
    assert cfg.workers == defaults.workers
    assert cfg.suggest_min_files == defaults.suggest_min_files


def test_per_kb_overrides_global_for_shared_key(tmp_path):
    global_cfg = _write_yaml(tmp_path / "global.yaml", {"thresholds": {"suggest_min_files": 3}})
    kb_cfg = _write_yaml(tmp_path / "kb.yaml", {"thresholds": {"suggest_min_files": 7}})

    cfg = load_config(global_cfg, kb_cfg)
    assert cfg.suggest_min_files == 7


def test_absent_per_kb_key_inherits_global(tmp_path):
    global_cfg = _write_yaml(tmp_path / "global.yaml", {"thresholds": {"suggest_min_files": 5}})
    kb_cfg = _write_yaml(tmp_path / "kb.yaml", {})

    cfg = load_config(global_cfg, kb_cfg)
    assert cfg.suggest_min_files == 5


def test_global_only_key_ignored_in_per_kb(tmp_path):
    global_cfg = _write_yaml(tmp_path / "global.yaml", {"server": {"port": 7700}})
    kb_cfg = _write_yaml(tmp_path / "kb.yaml", {"server": {"port": 9999}})

    cfg = load_config(global_cfg, kb_cfg)
    assert cfg.port == 7700


def test_per_kb_only_key_sets_focus(tmp_path):
    global_cfg = _write_yaml(tmp_path / "global.yaml", {})
    kb_cfg = _write_yaml(tmp_path / "kb.yaml", {"focus": "Transportation infrastructure"})

    cfg = load_config(global_cfg, kb_cfg)
    assert cfg.focus == "Transportation infrastructure"


def test_frozen_raises_on_mutation():
    cfg = load_config(None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.workers = 99  # type: ignore[misc]


def test_vision_mmproj_parsed_from_global(tmp_path):
    p = "/path/to/mmproj.gguf"
    cfg = load_config(_write_yaml(tmp_path / "g.yaml", {"models": {"vision_mmproj": p}}))
    assert cfg.vision_mmproj == p


def test_vision_chat_format_parsed_from_global(tmp_path):
    cfg = load_config(_write_yaml(tmp_path / "g.yaml", {"models": {"vision_chat_format": "qwen2_vl"}}))
    assert cfg.vision_chat_format == "qwen2_vl"


def test_vision_mmproj_overridable_per_kb(tmp_path):
    global_cfg = _write_yaml(tmp_path / "g.yaml", {"models": {"vision_mmproj": "/global/mmproj.gguf"}})
    kb_cfg = _write_yaml(tmp_path / "kb.yaml", {"models": {"vision_mmproj": "/kb/mmproj.gguf"}})
    cfg = load_config(global_cfg, kb_cfg)
    assert cfg.vision_mmproj == "/kb/mmproj.gguf"


def test_vision_mmproj_defaults_to_empty():
    cfg = load_config(None)
    assert cfg.vision_mmproj == ""
    assert cfg.vision_chat_format == ""


def test_invalid_per_kb_value_falls_back_to_global(tmp_path):
    global_cfg = _write_yaml(tmp_path / "global.yaml", {"thresholds": {"suggest_min_files": 4}})
    kb_cfg = _write_yaml(tmp_path / "kb.yaml", {"thresholds": {"suggest_min_files": "not_a_number"}})

    cfg = load_config(global_cfg, kb_cfg)
    # Invalid type in per-KB falls back; global supplies 4, not default 3
    assert cfg.suggest_min_files == 4
