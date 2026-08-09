from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_intent import OwnCodeIntentKind, classify_own_code_intent


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.last_action_context = None
    return engine


def test_new_proposal_request_with_do_not_apply_is_not_plan_approval() -> None:
    engine = _engine()
    engine._load_own_code_plan = lambda: {
        "status": "awaiting_approval",
        "instruction": "old plan",
        "candidate_files": ["core/old.py"],
    }
    engine._handle_own_code_plan_follow_up = lambda _text: (_ for _ in ()).throw(
        AssertionError("must not enter plan approval/apply flow")
    )

    text = (
        "Yeni bir kod degisikligi taslagi olustur. Hedef dosya: core/assistant.py. "
        "Yalnizca docstring metnini daha acik hale getir. Davranisi degistirme. "
        "Degisikligi uygulama. Sadece yeni proposal olustur ve onay bekle."
    )
    assert engine._own_code_plan_request(text) is None


def test_plain_plan_approval_still_routes_to_follow_up() -> None:
    engine = _engine()
    engine._load_own_code_plan = lambda: {
        "status": "awaiting_approval",
        "instruction": "x",
        "candidate_files": ["core/assistant.py"],
    }
    engine._handle_own_code_plan_follow_up = lambda _text: "APPROVAL"
    assert engine._own_code_plan_request("plani onayla") == "APPROVAL"


def test_new_proposal_is_change_intent_not_read_only() -> None:
    intent = classify_own_code_intent(
        "Jarvis kendi kodunda yeni proposal olustur, degisikligi uygulama"
    )
    assert intent.kind is OwnCodeIntentKind.CHANGE
