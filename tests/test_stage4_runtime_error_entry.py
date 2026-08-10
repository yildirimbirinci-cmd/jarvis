from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.runtime_observability import RuntimeEventStore, RuntimeHealthAnalyzer


def _record_failure(
    store: RuntimeEventStore,
    workspace: Path,
    *,
    error_type: str,
    action_path: str = "core/assistant.py",
    action_symbol: str = "AssistantEngine.handle",
    message: str = "boom",
) -> None:
    store.record(
        component="TaskOrchestrator",
        action="execute_task",
        status="failed",
        workspace=workspace,
        scope="task",
        source_path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
        message=message,
        error_type=error_type,
        metadata={
            "action_path": action_path,
            "action_symbol": action_symbol,
            "action_started": True,
            "action_completed": True,
        },
    )


def test_type_error_promotes_real_action_target(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(2):
        _record_failure(store, tmp_path, error_type="TypeError")

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.category == "repeated_runtime_failure"
    assert finding.affected_paths == ("core/assistant.py",)
    assert finding.affected_symbols == ("AssistantEngine.handle",)


def test_attribute_and_name_error_promote_real_action_target(tmp_path: Path) -> None:
    for error_type in ("AttributeError", "NameError"):
        store = RuntimeEventStore(tmp_path / f"{error_type}.json")
        for _ in range(2):
            _record_failure(store, tmp_path, error_type=error_type)
        finding = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path).findings[0]
        assert finding.affected_paths == ("core/assistant.py",)
        assert finding.affected_symbols == ("AssistantEngine.handle",)


def test_import_error_family_promotes_real_action_target(tmp_path: Path) -> None:
    for error_type in ("ImportError", "ModuleNotFoundError"):
        store = RuntimeEventStore(tmp_path / f"{error_type}.json")
        for _ in range(2):
            _record_failure(store, tmp_path, error_type=error_type)
        finding = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path).findings[0]
        assert finding.affected_paths == ("core/assistant.py",)
        assert finding.affected_symbols == ("AssistantEngine.handle",)


def test_non_repairable_runtime_error_keeps_wrapper_target(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(2):
        _record_failure(store, tmp_path, error_type="RuntimeError")

    finding = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path).findings[0]

    assert finding.affected_paths == ("core/task_orchestrator.py",)
    assert finding.affected_symbols == ("TaskOrchestrator.wrap.execute",)


def test_unsafe_action_path_falls_back_to_wrapper_target(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(2):
        _record_failure(
            store,
            tmp_path,
            error_type="TypeError",
            action_path="../outside.py",
        )

    finding = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path).findings[0]

    assert finding.affected_paths == ("core/task_orchestrator.py",)
    assert finding.affected_symbols == ("TaskOrchestrator.wrap.execute",)


def test_missing_action_symbol_falls_back_to_wrapper_target(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(2):
        _record_failure(
            store,
            tmp_path,
            error_type="AttributeError",
            action_symbol="",
        )

    finding = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path).findings[0]

    assert finding.affected_paths == ("core/task_orchestrator.py",)
    assert finding.affected_symbols == ("TaskOrchestrator.wrap.execute",)


def test_different_action_targets_do_not_merge_into_one_failure(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(2):
        _record_failure(
            store,
            tmp_path,
            error_type="TypeError",
            action_path="core/assistant.py",
            action_symbol="AssistantEngine.handle",
            message="same failure",
        )
        _record_failure(
            store,
            tmp_path,
            error_type="TypeError",
            action_path="core/task_runtime.py",
            action_symbol="TaskRuntime.execute",
            message="same failure",
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)

    failure_findings = [
        item for item in report.findings if item.category == "repeated_runtime_failure"
    ]
    assert len(failure_findings) == 2
    assert {item.affected_paths for item in failure_findings} == {
        ("core/assistant.py",),
        ("core/task_runtime.py",),
    }


def test_single_repairable_failure_does_not_cross_threshold(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    _record_failure(store, tmp_path, error_type="NameError")

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)

    assert report.failed_count == 1
    assert not [item for item in report.findings if item.category == "repeated_runtime_failure"]
