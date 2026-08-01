from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"


def _load_config_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    spec = importlib.util.spec_from_file_location("artmach_test_config", CONFIG_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module, "CONFIG_FILE", tmp_path / "config.json")
    return module


@pytest.mark.parametrize(
    "raw",
    [
        '{"model":"first","model":"second"}',
        '{"voice_owner_threshold":NaN}',
        '{"voice_owner_threshold":Infinity}',
        '[]',
    ],
)
def test_load_rejects_invalid_json_objects(monkeypatch, tmp_path, raw):
    module = _load_config_module(monkeypatch, tmp_path)
    module.CONFIG_FILE.write_text(raw, encoding="utf-8")

    config = module.AppConfig.load()

    assert config == module.AppConfig()


def test_load_rejects_oversized_config(monkeypatch, tmp_path):
    module = _load_config_module(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "CONFIG_MAX_BYTES", 8)
    module.CONFIG_FILE.write_text('{"model":"too-large"}', encoding="utf-8")

    assert module.AppConfig.load() == module.AppConfig()


def test_save_is_atomic_and_writes_standard_json(monkeypatch, tmp_path):
    module = _load_config_module(monkeypatch, tmp_path)
    config = module.AppConfig(model="test-model", voice_owner_threshold=0.91)

    config.save()

    payload = json.loads(module.CONFIG_FILE.read_text(encoding="utf-8"))
    assert payload["model"] == "test-model"
    assert payload["voice_owner_threshold"] == 0.91
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_replace_preserves_existing_config(monkeypatch, tmp_path):
    module = _load_config_module(monkeypatch, tmp_path)
    module.CONFIG_FILE.write_text('{"model":"existing"}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        module.AppConfig(model="new").save()

    assert module.CONFIG_FILE.read_text(encoding="utf-8") == '{"model":"existing"}'
    assert list(tmp_path.glob("*.tmp")) == []


def test_legacy_model_migrates_only_to_code_role(monkeypatch, tmp_path):
    module = _load_config_module(monkeypatch, tmp_path)
    module.CONFIG_FILE.write_text('{"model":"legacy-coder"}', encoding="utf-8")

    config = module.AppConfig.load()

    assert config.code_model == "legacy-coder"
    assert config.chat_model == module.AppConfig.chat_model
    assert config.chat_model != config.code_model


def test_model_and_context_limits_are_normalized(monkeypatch, tmp_path):
    module = _load_config_module(monkeypatch, tmp_path)
    module.CONFIG_FILE.write_text(
        json.dumps(
            {
                "chat_model": "chat",
                "code_model": "code",
                "chat_context_window": 1,
                "chat_max_output_tokens": 999999,
                "code_context_window": 1,
                "code_max_output_tokens": 999999,
                "dialogue_recent_message_limit": 9,
                "dialogue_recent_char_limit": 1,
                "dialogue_summary_char_limit": 999999,
                "project_context_char_limit": 1,
            }
        ),
        encoding="utf-8",
    )

    config = module.AppConfig.load()

    assert config.chat_context_window == 1024
    assert config.chat_max_output_tokens == 4096
    assert config.code_context_window == 4096
    assert config.code_max_output_tokens == 32768
    assert config.dialogue_recent_message_limit == 8
    assert config.dialogue_recent_char_limit == 2000
    assert config.dialogue_summary_char_limit == 30000
    assert config.project_context_char_limit == 1000


def test_corrupt_primary_recovers_valid_backup(monkeypatch, tmp_path):
    module = _load_config_module(monkeypatch, tmp_path)
    module.CONFIG_FILE.write_text('{"chat_model":', encoding="utf-8")
    module._config_backup_file().write_text(
        '{"chat_model":"backup-chat","code_model":"backup-code"}',
        encoding="utf-8",
    )

    config = module.AppConfig.load()

    assert config.chat_model == "backup-chat"
    assert config.code_model == "backup-code"
