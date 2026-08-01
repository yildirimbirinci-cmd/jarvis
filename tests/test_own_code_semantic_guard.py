from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_semantic_guard import (
    validate_semantic_replacement,
)


def _change(old: str, new: str):
    return SimpleNamespace(
        path="core/service.py",
        old_content=old,
        new_content=new,
    )


def test_small_internal_change_preserves_public_surface() -> None:
    old = "def public(value):\n    return value + 1\n"
    new = "def public(value):\n    result = value + 1\n    return result\n"

    result = validate_semantic_replacement(
        "Hesaplama kaydını iyileştir.", [_change(old, new)]
    )

    assert result.valid


def test_accidental_public_function_loss_is_rejected() -> None:
    old = (
        "def public(value):\n    return value\n\n"
        "def another():\n    return 2\n"
    )
    new = "def public(value):\n    return value\n"

    result = validate_semantic_replacement(
        "Yanıt süresini hızlandır.", [_change(old, new)]
    )

    assert not result.valid
    assert "another" in result.report()
    assert "sembol kaybı" in result.report()


def test_unrequested_public_signature_change_is_rejected() -> None:
    old = "def public(value):\n    return value\n"
    new = "def public(value, mode=False):\n    return value\n"

    result = validate_semantic_replacement(
        "Yanıt günlüğünü iyileştir.", [_change(old, new)]
    )

    assert not result.valid
    assert "API imzası" in result.report()


def test_explicit_parameter_change_is_allowed() -> None:
    old = "def public(value):\n    return value\n"
    new = "def public(value, mode=False):\n    return value\n"

    result = validate_semantic_replacement(
        "Public fonksiyonuna mode parametresi ekle.", [_change(old, new)]
    )

    assert result.valid


def test_large_unrequested_rewrite_is_rejected() -> None:
    old = "\n".join(f"value_{i} = {i}" for i in range(140)) + "\n"
    new = "\n".join(f"replacement_{i} = {i}" for i in range(140)) + "\n"

    result = validate_semantic_replacement(
        "Tek bir günlük satırı ekle.", [_change(old, new)]
    )

    assert not result.valid
    assert "yeniden yazılıyor" in result.report()
