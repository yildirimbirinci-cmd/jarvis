from __future__ import annotations

import itertools
import pytest

from artmach_assistant.core.own_code_command_router import (
    OwnCodeAction,
    classify_own_code_command,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bir taslak cikar ama uygulama.", OwnCodeAction.CREATE_PROPOSAL),
        ("Taslagi bir cikar, once bana sor.", OwnCodeAction.CREATE_PROPOSAL),
        ("Degisikligi taslak olarak goster.", OwnCodeAction.CREATE_PROPOSAL),
        ("Show me the patch first.", OwnCodeAction.CREATE_PROPOSAL),
        ("Prepare a proposal but do not apply.", OwnCodeAction.CREATE_PROPOSAL),
        ("Draft the change and wait for my approval.", OwnCodeAction.CREATE_PROPOSAL),
        ("Propozal hazirla ama uygulama.", OwnCodeAction.CREATE_PROPOSAL),
        ("Taslak hazrla, benden onay al.", OwnCodeAction.CREATE_PROPOSAL),
        ("Prepare a plan.", OwnCodeAction.CREATE_PLAN),
        ("Plan the change.", OwnCodeAction.CREATE_PLAN),
        ("Apply the pending proposal.", OwnCodeAction.APPLY_PENDING),
        ("Apply the patch.", OwnCodeAction.APPLY_PENDING),
        ("Approve the plan.", OwnCodeAction.APPROVE_PLAN),
        ("Reject the proposal.", OwnCodeAction.REJECT_PENDING),
        ("Report current engineering state.", OwnCodeAction.REPORT_ENGINEERING_STATE),
        ("Show git status.", OwnCodeAction.REPORT_GIT_STATE),
    ],
)
def test_phase2_language_examples(text: str, expected: OwnCodeAction) -> None:
    assert classify_own_code_command(text).action is expected


def test_mixed_language_proposal_gauntlet() -> None:
    subjects = (
        "Kendi kodunda",
        "Jarvis kodunda",
        "core/assistant.py icin",
        "For core/assistant.py",
    )
    requests = (
        "proposal hazirla",
        "taslak cikar",
        "prepare a proposal",
        "draft the change",
    )
    guards = (
        "ama uygulama",
        "wait for my approval",
        "show me first",
        "benden onay al",
    )
    count = 0
    for subject, request, guard in itertools.product(subjects, requests, guards):
        text = f"{subject} {request} {guard}"
        command = classify_own_code_command(text)
        assert command.action is OwnCodeAction.CREATE_PROPOSAL, text
        assert command.apply is False, text
        count += 1
    assert count == 64


def test_proposal_apply_contrast_matrix() -> None:
    proposal_cases = (
        "proposal hazirla ama uygulama",
        "prepare a proposal but do not apply",
        "taslak cikar ve onayimi bekle",
        "show me the patch first",
    )
    apply_cases = (
        "proposal uygula",
        "apply the proposal",
        "taslagi uygula",
        "apply the patch",
    )
    for text in proposal_cases:
        command = classify_own_code_command(text)
        assert command.action is OwnCodeAction.CREATE_PROPOSAL, text
        assert command.apply is False, text
    for text in apply_cases:
        command = classify_own_code_command(text)
        assert command.action is OwnCodeAction.APPLY_PENDING, text
        assert command.apply is True, text


def test_ambiguous_commands_do_not_escalate_to_apply() -> None:
    for text in (
        "bununla devam et",
        "bir seyler yap",
        "bakalim ne olacak",
        "go ahead",
        "continue",
        "do it",
    ):
        command = classify_own_code_command(text)
        assert command.action is not OwnCodeAction.APPLY_PENDING, text
        assert command.apply is False, text


def test_turkish_surface_variants_map_to_same_proposal_intent() -> None:
    pairs = (
        ("taslak cikar ama uygulama", "taslagi cikar ama uygulama"),
        ("proposal uygula", "proposali uygula"),
        ("patch uygula", "patchi uygula"),
        ("plan onayla", "plani onayla"),
    )
    for left, right in pairs:
        left_command = classify_own_code_command(left)
        right_command = classify_own_code_command(right)
        assert left_command.action is right_command.action
        assert left_command.apply is right_command.apply
