from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.own_code_command_router import (
    OwnCodeAction,
    classify_own_code_command,
)
from artmach_assistant.core.own_code_language_intelligence import (
    activate_learned_phrase,
    deactivate_learned_phrase,
    learned_phrase_match,
    validate_learned_phrase,
)


def test_confirmed_phrase_can_be_validated_and_activated(tmp_path: Path) -> None:
    store = tmp_path / "user_language.json"
    decision = activate_learned_phrase(
        store,
        phrase="taslagi bir cikar",
        intent="CREATE_PROPOSAL",
    )
    assert decision.active
    payload = json.loads(store.read_text(encoding="utf-8"))
    assert payload["active"]["CREATE_PROPOSAL"] == ["taslagi bir cikar"]


def test_activated_phrase_routes_without_core_corpus_entry(tmp_path: Path) -> None:
    store = tmp_path / "user_language.json"
    activate_learned_phrase(
        store,
        phrase="once bi degisikligi ser",
        intent="CREATE_PROPOSAL",
    )
    command = classify_own_code_command(
        "once bi degisikligi ser",
        learned_store_path=store,
    )
    assert command.action is OwnCodeAction.CREATE_PROPOSAL
    assert command.apply is False


def test_core_conflict_blocks_wrong_learning(tmp_path: Path) -> None:
    decision = validate_learned_phrase(
        phrase="taslagi uygula",
        intent="CREATE_PROPOSAL",
    )
    assert not decision.active
    assert "conflicts with core intent" in decision.reason


def test_ambiguous_apply_learning_is_blocked() -> None:
    decision = validate_learned_phrase(
        phrase="devam et",
        intent="APPLY_PENDING",
    )
    assert not decision.active
    assert "not explicit" in decision.reason


def test_negative_apply_learning_is_blocked() -> None:
    decision = validate_learned_phrase(
        phrase="apply etme, once goster",
        intent="APPLY_PENDING",
    )
    assert not decision.active


def test_explicit_apply_learning_can_be_activated(tmp_path: Path) -> None:
    store = tmp_path / "user_language.json"
    decision = activate_learned_phrase(
        store,
        phrase="hazir degisikligi canliya gecir",
        intent="APPLY_PENDING",
    )
    assert decision.active
    command = classify_own_code_command(
        "hazir degisikligi canliya gecir",
        learned_store_path=store,
    )
    assert command.action is OwnCodeAction.APPLY_PENDING
    assert command.apply is True


def test_deactivation_removes_live_routing(tmp_path: Path) -> None:
    store = tmp_path / "user_language.json"
    activate_learned_phrase(
        store,
        phrase="taslagi bir cikar",
        intent="CREATE_PROPOSAL",
    )
    assert learned_phrase_match(
        "taslagi bir cikar",
        store_path=store,
    ).intent == "CREATE_PROPOSAL"
    assert deactivate_learned_phrase(
        store,
        phrase="taslagi bir cikar",
        intent="CREATE_PROPOSAL",
    )
    assert learned_phrase_match(
        "taslagi bir cikar",
        store_path=store,
    ).intent == ""


def test_unrelated_unknown_phrase_still_does_not_escalate(tmp_path: Path) -> None:
    store = tmp_path / "user_language.json"
    command = classify_own_code_command(
        "sen bilirsin bir seyler yap",
        learned_store_path=store,
    )
    assert command.action is OwnCodeAction.NONE
    assert command.apply is False
