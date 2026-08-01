from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.self_development_cli import run_self_development


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
