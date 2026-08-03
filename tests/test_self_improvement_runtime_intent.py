from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.self_development_cli import (
    SelfDevelopmentResult,
)


class FakeAssistant:
    _self_improvement_runtime_request = (
        AssistantEngine._self_improvement_runtime_request
    )

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "project"
        self.root.mkdir()
        self.self_improvement_research = SimpleNamespace(
            path=tmp_path / "self_improvement_research.json"
        )

    @staticmethod
    def command_key(text: str) -> str:
        table = str.maketrans(
            "çğıöşüâîû",
            "cgiosuaiu",
        )
        return " ".join(
            str(text).casefold().translate(table).split()
        )

    def own_project_root(self) -> Path:
        return self.root


def test_routes_explicit_runtime_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_command(**kwargs):
        captured.update(kwargs)
        return SelfDevelopmentResult(
            stage="improvement_empty",
            exit_code=0,
            output="OTONOM DURUM",
        )

    monkeypatch.setattr(
        assistant_module,
        "run_self_development_command",
        fake_command,
    )

    result = FakeAssistant(
        tmp_path
    )._self_improvement_runtime_request(
        "Otonom gelişim durumu"
    )

    assert result == "OTONOM DURUM"
    assert captured["stage"] == "improvement_status"
    assert captured["trigger_id"] == (
        "assistant-natural-language"
    )


def test_routes_explicit_runtime_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_command(**kwargs):
        captured.update(kwargs)
        return SelfDevelopmentResult(
            stage="improvement_blocked",
            exit_code=0,
            output="ZINCIR CALISTI",
        )

    monkeypatch.setattr(
        assistant_module,
        "run_self_development_command",
        fake_command,
    )

    result = FakeAssistant(
        tmp_path
    )._self_improvement_runtime_request(
        "İyileştirme zincirini çalıştır"
    )

    assert result == "ZINCIR CALISTI"
    assert captured["stage"] == "improvement_run"


def test_prepare_requires_explicit_phrase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_command(**kwargs):
        captured.update(kwargs)
        return SelfDevelopmentResult(
            stage="improvement_blocked",
            exit_code=0,
            output="DENEY HAZIRLANDI",
        )

    monkeypatch.setattr(
        assistant_module,
        "run_self_development_command",
        fake_command,
    )

    result = FakeAssistant(
        tmp_path
    )._self_improvement_runtime_request(
        "Deney çalışma alanını hazırla"
    )

    assert result == "DENEY HAZIRLANDI"
    assert captured["stage"] == (
        "improvement_prepare"
    )


def test_generic_self_improvement_stays_in_research_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def forbidden_command(**kwargs):
        raise AssertionError(
            "generic research request reached runtime"
        )

    monkeypatch.setattr(
        assistant_module,
        "run_self_development_command",
        forbidden_command,
    )

    assistant = FakeAssistant(tmp_path)

    assert (
        assistant._self_improvement_runtime_request(
            "Kendini geliştir ve neden yavaş olduğunu araştır"
        )
        is None
    )


def test_result_request_stays_in_existing_research_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def forbidden_command(**kwargs):
        raise AssertionError(
            "research result request reached runtime"
        )

    monkeypatch.setattr(
        assistant_module,
        "run_self_development_command",
        forbidden_command,
    )

    assistant = FakeAssistant(tmp_path)

    assert (
        assistant._self_improvement_runtime_request(
            "Araştırma sonucu ne oldu?"
        )
        is None
    )


def test_missing_journal_store_is_reported(
    tmp_path: Path,
) -> None:
    assistant = FakeAssistant(tmp_path)
    assistant.self_improvement_research = None

    result = assistant._self_improvement_runtime_request(
        "Otonom gelişim durumu"
    )

    assert result is not None
    assert "Journal yolu" in result


def test_runtime_failure_is_rendered_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def failing_command(**kwargs):
        raise RuntimeError("runtime failure")

    monkeypatch.setattr(
        assistant_module,
        "run_self_development_command",
        failing_command,
    )

    result = FakeAssistant(
        tmp_path
    )._self_improvement_runtime_request(
        "Otonom iyileştirme durumu"
    )

    assert result is not None
    assert "RuntimeError" in result
    assert "runtime failure" in result
