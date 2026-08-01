from __future__ import annotations

from types import SimpleNamespace

import pytest

from artmach_assistant.config import AppConfig
from artmach_assistant.core.model_roles import (
    DEFAULT_CHAT_MODEL,
    ModelRoleError,
    ModelRoleResolver,
)


def test_legacy_model_migrates_only_to_code_role() -> None:
    config = SimpleNamespace(
        model="legacy-coder:7b",
        chat_model="",
        code_model="",
        chat_context_window=4096,
        chat_max_output_tokens=512,
        code_context_window=12288,
        code_max_output_tokens=8192,
    )

    resolver = ModelRoleResolver(config)

    assert resolver.chat_model == DEFAULT_CHAT_MODEL
    assert resolver.chat.source == "default_chat_model"
    assert resolver.code_model == "legacy-coder:7b"
    assert resolver.code.source == "legacy_model"


def test_explicit_chat_and_code_roles_keep_independent_limits() -> None:
    config = SimpleNamespace(
        model="old",
        chat_model="fast-chat:3b",
        code_model="careful-code:14b",
        chat_context_window=2048,
        chat_max_output_tokens=300,
        code_context_window=24000,
        code_max_output_tokens=12000,
    )

    resolver = ModelRoleResolver(config)

    assert resolver.chat.model == "fast-chat:3b"
    assert resolver.chat.context_window == 2048
    assert resolver.chat.max_output_tokens == 300
    assert resolver.code.model == "careful-code:14b"
    assert resolver.code.context_window == 24000
    assert resolver.code.max_output_tokens == 12000
    assert resolver.roles_share_model is False


def test_same_physical_model_is_reported_without_merging_roles() -> None:
    config = SimpleNamespace(
        model="",
        chat_model="shared:latest",
        code_model="shared:latest",
    )

    resolver = ModelRoleResolver(config)

    assert resolver.roles_share_model is True
    assert "ayni model" in resolver.report().casefold()
    assert resolver.chat.role == "chat"
    assert resolver.code.role == "code"


def test_invalid_model_name_is_rejected() -> None:
    config = SimpleNamespace(chat_model="bad\nmodel", code_model="code")

    with pytest.raises(ModelRoleError):
        ModelRoleResolver(config).chat


def test_config_migration_never_copies_legacy_coder_into_chat_role() -> None:
    data = AppConfig._normalise_data(
        {
            "model": "qwen2.5-coder:32b",
            "chat_model": "",
            "code_model": "",
            "chat_context_window": 999999,
            "code_context_window": 1,
        }
    )

    assert data["chat_model"] == AppConfig.chat_model
    assert data["code_model"] == "qwen2.5-coder:32b"
    assert data["chat_context_window"] == 32768
    assert data["code_context_window"] == 4096
