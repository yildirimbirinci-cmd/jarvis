from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_command_router import (
    OwnCodeAction,
    classify_own_code_command,
)


@pytest.mark.parametrize(
    "text",
    [
        "Kendi kodunda proposal hazirla ama uygulama.",
        "Kendi kodun icin yeni proposal olustur, onayimi bekle.",
        "core/assistant.py icin taslak hazirla, dosyaya yazma.",
        "core/assistant.py icin taslak olustur ve sadece goster.",
        "Jarvis kodunda degisiklik taslagi olustur ama henuz uygulama.",
        "Kendi kaynaklarin icin kod degisikligi taslagi hazirla.",
        "core/assistant.py icin patch tasarla, canli kaynaga yazma.",
        "Jarvis kodu icin patch onerisi hazirla ve onay bekle.",
        "Kendi kodun icin degisiklik onerisi olustur.",
        "Yeni proposal uret: core/assistant.py. Simdilik uygulama.",
        "core/assistant.py icin proposal olustur, degisikligi uygulama.",
        "Kendi kodumda degil, kendi kodunda taslak uret ve once goster.",
    ],
)
def test_proposal_paraphrases_have_one_action(text: str) -> None:
    command = classify_own_code_command(text)
    assert command.action is OwnCodeAction.CREATE_PROPOSAL
    assert command.apply is False


@pytest.mark.parametrize(
    "text",
    [
        "taslagi uygula",
        "taslagi onayla",
        "proposal uygula",
        "proposali uygula",
        "proposal onayla",
        "bekleyen taslagi uygula",
        "bekleyen proposali uygula",
        "patchi uygula",
    ],
)
def test_apply_paraphrases_are_explicit_apply_only(text: str) -> None:
    command = classify_own_code_command(text)
    assert command.action is OwnCodeAction.APPLY_PENDING
    assert command.apply is True


@pytest.mark.parametrize(
    "text",
    [
        "proposal hazirla ama uygulama",
        "taslak olustur, henuz uygulama",
        "patch tasarla fakat canli kaynaga yazma",
        "degisiklik onerisi olustur ve onayimi bekle",
    ],
)
def test_negated_apply_can_never_be_apply(text: str) -> None:
    text = "Kendi kodun icin " + text
    command = classify_own_code_command(text)
    assert command.action is OwnCodeAction.CREATE_PROPOSAL
    assert command.apply is False


@pytest.mark.parametrize(
    "text",
    [
        "Kendi muhendislik durumunu raporla, hicbir kodu degistirme.",
        "Mevcut self-development oturumunu goster, yalnizca kayitli durumu raporla.",
        "Own-code engineering cycle durumunu incele, yeni plan olusturma.",
        "Kendi kod gelistirme durumunu goster, mevcut kayitli durumu raporla.",
    ],
)
def test_engineering_state_paraphrases_are_read_only(text: str) -> None:
    command = classify_own_code_command(text)
    assert command.action is OwnCodeAction.REPORT_ENGINEERING_STATE
    assert command.read_only is True
    assert command.apply is False


@pytest.mark.parametrize(
    "text",
    [
        "Git durumunu raporla: git rev-parse HEAD ve git status --porcelain.",
        "Git bilgisini goster, HEAD commit ve uncommitted dosyalari raporla.",
        "Git branch --show-current ve git status --porcelain gercek ciktilarini goster.",
    ],
)
def test_git_state_paraphrases_are_authoritative_read_only(text: str) -> None:
    command = classify_own_code_command(text)
    assert command.action is OwnCodeAction.REPORT_GIT_STATE
    assert command.read_only is True


def test_direct_proposal_dispatch_never_calls_apply(tmp_path: Path) -> None:
    target = tmp_path / "core" / "assistant.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine._is_active_own_code_source_path = lambda path: path == "core/assistant.py"
    engine._is_test_path = lambda _path: False
    engine.prepare_own_code_plan = lambda _text: (_ for _ in ()).throw(
        AssertionError("explicit file target must not fall back to plan")
    )
    calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def prepare(_text: str, **kwargs) -> str:
        calls.append(
            (
                tuple(kwargs.get("approved_paths", ())),
                tuple(kwargs.get("approved_symbols", ())),
            )
        )
        return "PROPOSAL"

    engine.prepare_own_code_proposal = prepare
    engine._own_code_approval_request = lambda _text: (_ for _ in ()).throw(
        AssertionError("create proposal must never enter apply flow")
    )

    result = engine._structured_own_code_command_request(
        "Kendi kodunda core/assistant.py icin proposal hazirla ama uygulama."
    )
    assert result == "PROPOSAL"
    assert calls == [(("core/assistant.py",), ())]


def test_create_proposal_outranks_stale_plan_follow_up(tmp_path: Path) -> None:
    target = tmp_path / "core" / "assistant.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine._is_active_own_code_source_path = lambda path: path == "core/assistant.py"
    engine._is_test_path = lambda _path: False
    engine.prepare_own_code_proposal = lambda *_args, **_kwargs: "NEW_PROPOSAL"
    engine.prepare_own_code_plan = lambda *_args, **_kwargs: "PLAN"
    engine._handle_own_code_plan_follow_up = lambda _text: (_ for _ in ()).throw(
        AssertionError("stale plan handler must not consume create proposal")
    )

    assert (
        engine._structured_own_code_command_request(
            "core/assistant.py icin yeni proposal olustur, uygulama, onay bekle."
        )
        == "NEW_PROPOSAL"
    )
