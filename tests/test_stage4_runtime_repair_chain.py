from __future__ import annotations

from pathlib import Path
from types import MethodType

import pytest

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.autonomous_repair_policy import (
    AUTO_ALLOWED,
    assess_autonomous_runtime_repair,
)
from artmach_assistant.core.runtime_observability import (
    RuntimeEventStore,
    RuntimeHealthAnalyzer,
)
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


@pytest.mark.parametrize("error_type", ["ImportError", "ModuleNotFoundError"])
def test_import_contract_failure_enters_repair_chain_with_real_action_target(
    tmp_path: Path,
    error_type: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "core").mkdir()
    (project / "core" / "assistant.py").write_text(
        "class AssistantEngine:\n    def handle(self):\n        return None\n",
        encoding="utf-8",
    )

    store = RuntimeEventStore(tmp_path / "runtime_events.json")
    metadata = {
        "action_path": "core/assistant.py",
        "action_symbol": "AssistantEngine.handle",
    }
    for _ in range(3):
        store.record(
            component="TaskOrchestrator",
            action="execute_task",
            status="failed",
            workspace=project,
            scope="own_code",
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.wrap.execute",
            error_type=error_type,
            message="controlled import contract failure",
            metadata=metadata,
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=project)
    finding = next(
        item for item in report.findings
        if item.category == "repeated_runtime_failure"
    )

    assert finding.affected_paths == ("core/assistant.py",)
    assert finding.affected_symbols == ("AssistantEngine.handle",)
    assert error_type in finding.explanation

    decision = assess_autonomous_runtime_repair(finding)
    assert decision.status == AUTO_ALLOWED
    assert decision.approved_paths == ("core/assistant.py",)
    assert decision.approved_symbols == ("AssistantEngine.handle",)

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.self_repair_sessions = SelfRepairSessionStore(
        tmp_path / "self_repair_session.json"
    )
    engine._last_runtime_health_report = report

    def _find_runtime_finding(self, finding_id: str):
        return finding if finding_id.upper() == finding.finding_id.upper() else None

    def _development_root(self, *, own_code: bool):
        assert own_code is True
        return project

    def _current_source_fingerprint(self):
        return "stage4-contract-fingerprint"

    def _load_own_code_plan(self):
        return None

    engine._find_runtime_finding = MethodType(_find_runtime_finding, engine)
    engine._development_root = MethodType(_development_root, engine)
    engine._current_source_fingerprint = MethodType(
        _current_source_fingerprint, engine
    )
    engine._load_own_code_plan = MethodType(_load_own_code_plan, engine)

    output = engine.prepare_runtime_improvement_implementation(
        finding.finding_id
    )
    session = engine.self_repair_sessions.load()

    assert session is not None
    assert session.finding_id == finding.finding_id
    assert session.approved_paths == ("core/assistant.py",)
    assert session.approved_symbols == ("AssistantEngine.handle",)
    assert "core/task_orchestrator.py" not in session.approved_paths
    assert "TaskOrchestrator.wrap.execute" not in session.approved_symbols
    assert session.state == "planned"
    assert finding.finding_id in output


def test_non_repairable_runtime_error_keeps_wrapper_target(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = RuntimeEventStore(tmp_path / "runtime_events.json")
    metadata = {
        "action_path": "core/assistant.py",
        "action_symbol": "AssistantEngine.handle",
    }
    for _ in range(3):
        store.record(
            component="TaskOrchestrator",
            action="execute_task",
            status="failed",
            workspace=project,
            scope="own_code",
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.wrap.execute",
            error_type="RuntimeError",
            message="controlled generic runtime failure",
            metadata=metadata,
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=project)
    finding = next(
        item for item in report.findings
        if item.category == "repeated_runtime_failure"
    )

    assert finding.affected_paths == ("core/task_orchestrator.py",)
    assert finding.affected_symbols == ("TaskOrchestrator.wrap.execute",)
