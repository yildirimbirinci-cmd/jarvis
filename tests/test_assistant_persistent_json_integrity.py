from __future__ import annotations

from artmach_assistant.core import assistant as assistant_module


def test_learned_dialogues_reject_duplicate_keys(tmp_path, monkeypatch):
    target = tmp_path / "learned_dialogues.json"
    target.write_text('{"hello":{"response":"first"},"hello":{"response":"second"}}', encoding="utf-8")
    monkeypatch.setattr(assistant_module, "LEARNED_DIALOGUES_FILE", target)

    assert assistant_module.AssistantEngine._load_learned_dialogues() == {}


def test_learned_dialogues_reject_non_finite_numbers(tmp_path, monkeypatch):
    target = tmp_path / "learned_dialogues.json"
    target.write_text('{"hello":{"score":NaN}}', encoding="utf-8")
    monkeypatch.setattr(assistant_module, "LEARNED_DIALOGUES_FILE", target)

    assert assistant_module.AssistantEngine._load_learned_dialogues() == {}


def test_own_validation_rejects_oversized_payload(tmp_path, monkeypatch):
    target = tmp_path / "own_code_validation.json"
    target.write_text('{"success":true,"output":"' + ('x' * 40000) + '"}', encoding="utf-8")
    monkeypatch.setattr(assistant_module, "OWN_CODE_VALIDATION_FILE", target)

    assert assistant_module.AssistantEngine._load_own_validation() is None


def test_own_authority_rejects_duplicate_enabled_key(tmp_path, monkeypatch):
    target = tmp_path / "own_code_authority.json"
    target.write_text('{"enabled":true,"enabled":false}', encoding="utf-8")
    monkeypatch.setattr(assistant_module, "OWN_CODE_AUTHORITY_FILE", target)

    assert assistant_module.AssistantEngine.has_own_code_authority() is False
