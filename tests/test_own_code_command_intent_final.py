from __future__ import annotations

from pathlib import Path

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_intent import (
    OwnCodeIntentKind,
    classify_own_code_intent,
)


def test_real_user_review_phrases_are_read_only() -> None:
    phrases = (
        "kendi kod dosyalarını incele",
        "kodlarını incele",
        "geliştirm yapmıyoruz sadece kodlarını incele",
        "kndi kod dosyalarının bir özetini çıkart",
    )
    kinds = [classify_own_code_intent(text).kind for text in phrases]
    assert kinds == [
        OwnCodeIntentKind.REVIEW,
        OwnCodeIntentKind.REVIEW,
        OwnCodeIntentKind.REVIEW,
        OwnCodeIntentKind.SUMMARY,
    ]


def test_review_follow_up_uses_active_own_code_context() -> None:
    intent = classify_own_code_intent(
        "gerekli düzeltmeleri göster",
        active_own_editor=True,
    )
    assert intent.kind is OwnCodeIntentKind.REVIEW


def test_read_only_request_supersedes_stale_clarification_plan(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module, "OWN_CODE_PLAN_FILE", tmp_path / "plan.json"
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.last_action_context = None
    engine._save_own_code_plan({
        "status": "needs_clarification",
        "instruction": "kendi kodlarını geliştir",
        "question": "Hangi davranış değişmeli?",
    })
    engine.own_code_review_report = lambda: "READ_ONLY_REPORT"

    answer = engine._own_code_read_only_request("kendi kod dosyalarını incele")

    assert answer == "READ_ONLY_REPORT"
    plan = engine._load_own_code_plan()
    assert plan is not None
    assert plan["status"] == "superseded_by_read_only_request"


def test_unrelated_voice_sentence_does_not_fill_stale_plan(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module, "OWN_CODE_PLAN_FILE", tmp_path / "plan.json"
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.last_action_context = None
    engine.self_repair_sessions = None
    engine._active_self_repair_session = lambda: None
    engine._save_own_code_plan({
        "status": "needs_clarification",
        "instruction": "kendi kodlarını geliştir",
        "question": "Hangi davranış değişmeli?",
    })

    assert engine._handle_own_code_plan_follow_up("Haa, ses işinizlanmış.") is None
    assert engine._load_own_code_plan()["status"] == "needs_clarification"


def test_early_read_only_route_runs_before_old_plan(monkeypatch) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.last_action_context = None
    engine.normalize_address = lambda text: text
    engine._reserved_self_repair_request = lambda _text: None
    engine._own_code_read_only_request = lambda _text: "READ_ONLY_FIRST"

    answer = engine.handle_local_command("kendi kod dosyalarını incele")

    assert answer == "READ_ONLY_FIRST"
