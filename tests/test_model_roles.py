from __future__ import annotations

from types import SimpleNamespace

import pytest

from artmach_assistant.core.model_roles import (
    DEFAULT_CHAT_MODEL,
    ModelRoleError,
    ModelRoleResolver,
)


def test_legacy_model_is_used_only_for_code_role() -> None:
    resolver = ModelRoleResolver(SimpleNamespace(model="legacy-coder"))

    assert resolver.chat_model == DEFAULT_CHAT_MODEL
    assert resolver.chat.source == "default_chat_model"
    assert resolver.code_model == "legacy-coder"
    assert resolver.code.source == "legacy_model"


def test_explicit_model_roles_and_limits_are_independent() -> None:
    resolver = ModelRoleResolver(
        SimpleNamespace(
            model="legacy-coder",
            chat_model="fast-chat",
            code_model="deep-coder",
            chat_context_window=7000,
            chat_max_output_tokens=700,
            code_context_window=24000,
            code_max_output_tokens=12000,
        )
    )

    assert resolver.chat.model == "fast-chat"
    assert resolver.chat.context_window == 7000
    assert resolver.chat.max_output_tokens == 700
    assert resolver.code.model == "deep-coder"
    assert resolver.code.context_window == 24000
    assert resolver.code.max_output_tokens == 12000
    assert resolver.roles_share_model is False


def test_role_limits_are_clamped_without_cross_role_fallback() -> None:
    resolver = ModelRoleResolver(
        SimpleNamespace(
            chat_model="chat",
            code_model="code",
            chat_context_window=1,
            chat_max_output_tokens=999999,
            code_context_window=1,
            code_max_output_tokens=999999,
        )
    )

    assert resolver.chat.context_window == 1024
    assert resolver.chat.max_output_tokens == 4096
    assert resolver.code.context_window == 4096
    assert resolver.code.max_output_tokens == 32768


def test_invalid_model_name_is_rejected() -> None:
    resolver = ModelRoleResolver(SimpleNamespace(chat_model="bad\nmodel"))

    with pytest.raises(ModelRoleError, match="gecersiz"):
        _ = resolver.chat


def test_report_warns_when_two_roles_use_same_named_model() -> None:
    resolver = ModelRoleResolver(
        SimpleNamespace(chat_model="shared", code_model="shared")
    )

    assert resolver.roles_share_model is True
    assert "Uyari" in resolver.report()


def test_app_config_migration_never_copies_legacy_coder_into_chat_role() -> None:
    from artmach_assistant.config import AppConfig

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
