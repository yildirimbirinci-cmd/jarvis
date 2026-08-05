from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


@dataclass
class Finding:
    finding_id: str = "RUN-06578E9EDE"
    category: str = "repeated_slow_operation"
    severity: str = "medium"
    confidence: float = 0.92
    occurrence_count: int = 8
    affected_paths: tuple[str, ...] = ("core/task_orchestrator.py",)
    affected_symbols: tuple[str, ...] = ("TaskOrchestrator.wrap.execute",)


def test_autonomous_repair_runs_plan_proposal_and_apply() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    finding = Finding()
    states = [
        SimpleNamespace(active=True, state="planned"),
        SimpleNamespace(active=True, state="proposal_ready"),
        SimpleNamespace(active=False, state="completed"),
    ]
    store = SimpleNamespace(load=lambda: states.pop(0))
    engine._find_runtime_finding = lambda finding_id: finding
    engine._self_repair_store = lambda: store
    engine.prepare_runtime_improvement_implementation = lambda finding_id: "planned"
    engine._prepare_active_self_repair_proposal = lambda session: "proposal ready"
    engine._apply_active_self_repair_proposal = lambda session: "applied"
    engine._self_repair_status = lambda session: "completed"

    rendered = engine.run_autonomous_runtime_repair(finding.finding_id)

    assert "AUTO_ALLOWED" in rendered
    assert "planned" in rendered
    assert "proposal ready" in rendered
    assert "applied" in rendered
    assert "completed" in rendered


def test_run_fix_route_uses_autonomous_repair() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    finding = Finding()
    engine._find_runtime_finding = lambda finding_id: finding
    engine.run_autonomous_runtime_repair = lambda finding_id: "autonomous-result"

    rendered = engine._reserved_self_repair_request(
        "RUN-06578E9EDE bulgusunu duzelt"
    )

    assert rendered == "autonomous-result"
