from __future__ import annotations

from pathlib import Path
from types import MethodType

import pytest

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.runtime_observability import (
    RuntimeEventStore,
    RuntimeHealthAnalyzer,
)
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


REPAIRABLE_ERRORS = (
    "TypeError",
    "AttributeError",
    "NameError",
    "ImportError",
    "ModuleNotFoundError",
)


def _record_repeated_failure(
    tmp_path: Path,
    error_type: str,
    *,
    count: int = 3,
):
    project = tmp_path / f"{error_type}_{count}"
    project.mkdir()
    store = RuntimeEventStore(tmp_path / f"{error_type}_{count}.json")
    metadata = {
        "action_path": "core/assistant.py",
        "action_symbol": "AssistantEngine.handle",
    }
    for _ in range(count):
        store.record(
            component="TaskOrchestrator",
            action="execute_task",
            status="failed",
            workspace=project,
            scope="own_code",
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.wrap.execute",
            error_type=error_type,
            message=f"controlled {error_type}",
            metadata=metadata,
        )
    report = RuntimeHealthAnalyzer(store).analyze(workspace=project)
    return project, report


def _engine(
    tmp_path: Path,
    project: Path,
    finding,
) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.self_repair_sessions = SelfRepairSessionStore(
        tmp_path / f"{finding.error_type}_self_repair_session.json"
    )
    engine._development_root = MethodType(
        lambda self, *, own_code: project,
        engine,
    )
    engine._current_source_fingerprint = MethodType(
        lambda self: f"stage4-{finding.error_type}-source",
        engine,
    )
    engine._load_own_code_plan = MethodType(lambda self: None, engine)

    def _prepare(self, finding_id: str) -> str:
        assert finding_id == finding.finding_id
        self._self_repair_store().create(
            finding_id=finding.finding_id,
            instruction=f"controlled {finding.error_type} repair entry",
            approved_paths=finding.affected_paths,
            approved_symbols=finding.affected_symbols,
            evidence=f"controlled {finding.error_type} evidence",
            acceptance=finding.acceptance_criteria,
            source_fingerprint=self._current_source_fingerprint(),
        )
        return "planned"

    engine.prepare_runtime_improvement_implementation = MethodType(_prepare, engine)
    return engine


@pytest.mark.parametrize("error_type", REPAIRABLE_ERRORS)
def test_repairable_runtime_error_matrix_enters_planned_state(
    tmp_path: Path,
    error_type: str,
) -> None:
    project, report = _record_repeated_failure(tmp_path, error_type, count=3)
    finding = next(
        item
        for item in report.findings
        if item.category == "repeated_runtime_failure"
    )

    assert finding.error_type == error_type
    assert finding.affected_paths == ("core/assistant.py",)
    assert finding.affected_symbols == ("AssistantEngine.handle",)

    engine = _engine(tmp_path, project, finding)
    session = engine._prepare_automatic_runtime_failure_entry(finding)

    assert session is not None
    assert session.state == "planned"
    assert session.finding_id == finding.finding_id
    assert session.approved_paths == ("core/assistant.py",)
    assert session.approved_symbols == ("AssistantEngine.handle",)
    assert session.attempts == 0


@pytest.mark.parametrize(
    "error_type",
    ("RuntimeError", "ValueError", "KeyError", "OSError"),
)
def test_non_allowlisted_runtime_errors_do_not_auto_enter(
    tmp_path: Path,
    error_type: str,
) -> None:
    project, report = _record_repeated_failure(tmp_path, error_type, count=3)
    finding = next(
        item
        for item in report.findings
        if item.category == "repeated_runtime_failure"
    )
    engine = _engine(tmp_path, project, finding)

    assert finding.error_type == error_type
    assert engine._prepare_automatic_runtime_failure_entry(finding) is None
    assert engine.self_repair_sessions.load() is None


def test_single_failure_does_not_cross_repeated_failure_threshold(
    tmp_path: Path,
) -> None:
    _, report = _record_repeated_failure(tmp_path, "TypeError", count=1)

    assert not any(
        finding.category == "repeated_runtime_failure"
        for finding in report.findings
    )


def test_two_failures_cross_current_repeated_failure_threshold(
    tmp_path: Path,
) -> None:
    _, report = _record_repeated_failure(tmp_path, "TypeError", count=2)

    finding = next(
        item
        for item in report.findings
        if item.category == "repeated_runtime_failure"
    )
    assert finding.error_type == "TypeError"
    assert finding.occurrence_count == 2
    assert finding.affected_paths == ("core/assistant.py",)
    assert finding.affected_symbols == ("AssistantEngine.handle",)
