from __future__ import annotations

from types import SimpleNamespace

import artmach_assistant.core.self_development_cli as cli_module
from artmach_assistant.core.self_development_cli import (
    run_self_development,
    run_self_development_command,
)
from artmach_assistant.core.self_improvement_runtime_cli import (
    RuntimeCommandResult,
)


class FakeEngine:
    def __init__(self, *, proposal_ready: bool = True, apply_result: str = "uygulandı") -> None:
        self.editor = SimpleNamespace(pending=None)
        self.proposal_ready = proposal_ready
        self.apply_result = apply_result
        self.calls: list[tuple[str, str]] = []

    def prepare_own_code_plan(self, instruction: str) -> str:
        self.calls.append(("plan", instruction))
        return "PLAN HAZIR"

    def _handle_own_code_plan_follow_up(self, text: str) -> str:
        self.calls.append(("approve", text))
        if self.proposal_ready:
            self.editor.pending = object()
            return "TASLAK HAZIR"
        return "TASLAK HAZIRLANAMADI"

    def apply_pending_own_code_proposal(self) -> str:
        self.calls.append(("apply", ""))
        return self.apply_result


def test_plan_stage_never_prepares_or_applies_patch() -> None:
    engine = FakeEngine()
    result = run_self_development(
        "yanıt gecikmesini azalt",
        stage="plan",
        engine_factory=lambda: engine,
    )
    assert result.exit_code == 0
    assert result.stage == "plan"
    assert engine.calls == [("plan", "yanıt gecikmesini azalt")]


def test_propose_stage_stops_before_file_application() -> None:
    engine = FakeEngine()
    result = run_self_development(
        "yanıt gecikmesini azalt",
        stage="propose",
        engine_factory=lambda: engine,
    )
    assert result.exit_code == 0
    assert result.stage == "proposal"
    assert [name for name, _ in engine.calls] == ["plan", "approve"]


def test_apply_stage_requires_explicit_stage_and_runs_existing_guarded_apply() -> None:
    engine = FakeEngine(apply_result="Değişiklik doğrulandı ve uygulandı.")
    result = run_self_development(
        "yanıt gecikmesini azalt",
        stage="apply",
        engine_factory=lambda: engine,
    )
    assert result.exit_code == 0
    assert result.stage == "applied"
    assert [name for name, _ in engine.calls] == ["plan", "approve", "apply"]


def test_missing_proposal_fails_closed() -> None:
    engine = FakeEngine(proposal_ready=False)
    result = run_self_development(
        "yanıt gecikmesini azalt",
        stage="apply",
        engine_factory=lambda: engine,
    )
    assert result.exit_code == 1
    assert result.stage == "proposal_failed"
    assert [name for name, _ in engine.calls] == ["plan", "approve"]


def test_rollback_result_is_reported_as_failure() -> None:
    engine = FakeEngine(apply_result="Yeni test hatası bulundu ve değişiklik geri alındı.")
    result = run_self_development(
        "yanıt gecikmesini azalt",
        stage="apply",
        engine_factory=lambda: engine,
    )
    assert result.exit_code == 1
    assert result.stage == "apply_failed"


def test_command_dispatcher_preserves_legacy_plan() -> None:
    engine = FakeEngine()

    result = run_self_development_command(
        "yan?t gecikmesini azalt",
        stage="plan",
        engine_factory=lambda: engine,
    )

    assert result.stage == "plan"
    assert result.exit_code == 0
    assert engine.calls == [
        ("plan", "yan?t gecikmesini azalt")
    ]


def test_command_dispatcher_preserves_explicit_apply() -> None:
    engine = FakeEngine(
        apply_result="Değişiklik doğrulandı ve uygulandı."
    )

    result = run_self_development_command(
        "yan?t gecikmesini azalt",
        stage="apply",
        engine_factory=lambda: engine,
    )

    assert result.stage == "applied"
    assert result.exit_code == 0
    assert [name for name, _ in engine.calls] == [
        "plan",
        "approve",
        "apply",
    ]


def test_improvement_command_never_builds_legacy_engine(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_runtime(command: str, **kwargs):
        calls.append({"command": command, **kwargs})
        return RuntimeCommandResult(
            command=command,
            exit_code=0,
            status="blocked",
            output="SAFE RUNTIME",
        )

    monkeypatch.setattr(
        cli_module,
        "run_self_improvement_runtime",
        fake_runtime,
    )

    def forbidden_engine():
        raise AssertionError(
            "legacy engine must not be created"
        )

    result = run_self_development_command(
        stage="improvement_run",
        engine_factory=forbidden_engine,
        project_root="C:/project",
        journal_path="C:/journal.json",
        runtime_root="C:/runtime",
    )

    assert result.stage == "improvement_blocked"
    assert result.output == "SAFE RUNTIME"
    assert calls[0]["command"] == "run"


def test_improvement_prepare_maps_to_runtime_command(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_runtime(command: str, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return RuntimeCommandResult(
            command=command,
            exit_code=0,
            status="blocked",
            output="PREPARED",
        )

    monkeypatch.setattr(
        cli_module,
        "run_self_improvement_runtime",
        fake_runtime,
    )

    result = run_self_development_command(
        stage="improvement_prepare",
        project_root="C:/project",
        journal_path="C:/journal.json",
        runtime_root="C:/runtime",
        candidate_id="candidate-1",
    )

    assert result.exit_code == 0
    assert captured["command"] == "prepare"
    assert captured["candidate_id"] == "candidate-1"


def test_improvement_complete_forwards_result_paths(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_runtime(command: str, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return RuntimeCommandResult(
            command=command,
            exit_code=0,
            status="completed",
            output="COMPLETED",
        )

    monkeypatch.setattr(
        cli_module,
        "run_self_improvement_runtime",
        fake_runtime,
    )

    results = (
        "C:/results/one.json",
        "C:/results/two.json",
    )
    result = run_self_development_command(
        stage="improvement_complete",
        project_root="C:/project",
        journal_path="C:/journal.json",
        runtime_root="C:/runtime",
        experiment_result_paths=results,
        trigger_id="manual-test",
    )

    assert result.stage == "improvement_completed"
    assert captured["command"] == "complete"
    assert captured["experiment_result_paths"] == results
    assert captured["trigger_id"] == "manual-test"


def test_improvement_status_uses_same_dispatcher(
    monkeypatch,
) -> None:
    def fake_runtime(command: str, **kwargs):
        return RuntimeCommandResult(
            command=command,
            exit_code=0,
            status="empty",
            output="NO RUNS",
        )

    monkeypatch.setattr(
        cli_module,
        "run_self_improvement_runtime",
        fake_runtime,
    )

    result = run_self_development_command(
        stage="improvement_status",
        project_root="C:/project",
        journal_path="C:/journal.json",
        runtime_root="C:/runtime",
    )

    assert result.stage == "improvement_empty"
    assert result.exit_code == 0


def test_improvement_command_requires_explicit_paths() -> None:
    result = run_self_development_command(
        stage="improvement_run",
        project_root="C:/project",
    )

    assert result.stage == "invalid"
    assert result.exit_code == 2
    assert "journal_path" in result.output
    assert "runtime_root" in result.output


def test_dispatcher_rejects_unknown_stage() -> None:
    result = run_self_development_command(
        "anything",
        stage="delete_everything",
    )

    assert result.stage == "invalid"
    assert result.exit_code == 2
    assert "Bilinmeyen a?ama" in result.output

