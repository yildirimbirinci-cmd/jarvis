from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine_without_pending_edit() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine.project_improvements = None

    # This test covers routing only. Do not enter the real proposal,
    # workspace, model or repository machinery.
    engine.prepare_own_code_proposal = (
        lambda _instruction, **_kwargs: "PROPOSAL_ROUTE_REACHED"
    )
    return engine


def test_diagnostic_followup_is_not_treated_as_patch_application() -> None:
    engine = _engine_without_pending_edit()

    result = engine._own_code_approval_request(
        "Ucuncu cozumle devam et. Kodu degistirmeden sorunu kontrollu "
        "bicimde yeniden uret, her ses asamasinin suresini olc, eski "
        "olaylari ayir, gercek kok nedeni belirle ve duzeltme taslagini "
        "onayima sun."
    )

    assert result is None


def test_explicit_short_apply_command_still_reaches_approval_handler() -> None:
    engine = _engine_without_pending_edit()

    result = engine._own_code_approval_request("uygula")

    assert result is not None
    assert (
        "PROPOSAL_ROUTE_REACHED" in result
        or "bekleyen" in result.casefold()
    )
