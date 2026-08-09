from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.own_code_command_router import (
    OwnCodeAction,
    classify_own_code_command,
)
from artmach_assistant.core.own_code_language_intelligence import activate_learned_phrase


def test_learned_user_language_gauntlet(tmp_path: Path) -> None:
    store = tmp_path / "user_language.json"
    learned = {
        "CREATE_PROPOSAL": (
            "taslagi bir cikar",
            "once bi degisikligi ser",
            "ne yapacagini bi dok",
        ),
        "CREATE_PLAN": (
            "once yol haritasini cikar",
            "bi planini dok",
        ),
        "REPORT_ENGINEERING_STATE": (
            "muhendislik nabzini goster",
            "gelistirme durumunu bi soyle",
        ),
    }
    expected = {
        "CREATE_PROPOSAL": OwnCodeAction.CREATE_PROPOSAL,
        "CREATE_PLAN": OwnCodeAction.CREATE_PLAN,
        "REPORT_ENGINEERING_STATE": OwnCodeAction.REPORT_ENGINEERING_STATE,
    }
    for intent, phrases in learned.items():
        for phrase in phrases:
            decision = activate_learned_phrase(store, phrase=phrase, intent=intent)
            assert decision.active, (intent, phrase, decision.reason)

    for intent, phrases in learned.items():
        for phrase in phrases:
            command = classify_own_code_command(
                phrase,
                learned_store_path=store,
            )
            assert command.action is expected[intent], phrase
            assert command.apply is False, phrase
