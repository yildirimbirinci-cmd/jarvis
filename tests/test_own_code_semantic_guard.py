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


def test_behavior_preserving_extraction_rejects_lost_side_effects() -> None:
    old = (
        "class Worker:\n"
        "    def run(self):\n"
        "        while True:\n"
        "            self.engine_end_dialogue.emit()\n"
        "            self.msleep(250)\n"
        "            self._next_mode = 'sleep'\n"
        "            break\n"
    )
    new = (
        "class Worker:\n"
        "    def run(self):\n"
        "        while True:\n"
        "            self._handle()\n"
        "\n"
        "    def _handle(self):\n"
        "        return\n"
    )

    result = validate_semantic_replacement(
        "Bloğu davranışı değiştirmeden yardımcı metoda çıkar.",
        [_change(old, new)],
    )

    assert not result.valid
    assert "engine_end_dialogue.emit" in result.report()
    assert "control:break" in result.report()


def test_behavior_preserving_extraction_accepts_moved_operations() -> None:
    old = (
        "class Worker:\n"
        "    def run(self):\n"
        "        while True:\n"
        "            self.emit()\n"
        "            break\n"
    )
    new = (
        "class Worker:\n"
        "    def run(self):\n"
        "        while True:\n"
        "            self._handle()\n"
        "            break\n"
        "\n"
        "    def _handle(self):\n"
        "        self.emit()\n"
    )

    result = validate_semantic_replacement(
        "Bloğu davranışı değiştirmeden yardımcı metoda çıkar.",
        [_change(old, new)],
    )

    assert result.valid
