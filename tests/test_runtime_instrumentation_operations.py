from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_instrumentation(monkeypatch):
    instrumentation = importlib.import_module("artmach_assistant.core.runtime_instrumentation")
    instrumentation.reset_runtime_instrumentation_for_tests()
    yield
    instrumentation.reset_runtime_instrumentation_for_tests()


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def require_root(self) -> Path:
        return self.root


def _runtime_types():
    instrumentation = importlib.import_module("artmach_assistant.core.runtime_instrumentation")
    build_module = importlib.import_module("artmach_assistant.core.build_manager")
    filesystem_module = importlib.import_module("artmach_assistant.core.filesystem_tool_service")
    research_module = importlib.import_module("artmach_assistant.core.research_manager")
    task_module = importlib.import_module("artmach_assistant.core.task_orchestrator")
    return instrumentation, build_module, filesystem_module, research_module, task_module


def test_filesystem_task_build_and_research_operations_emit_events(monkeypatch, tmp_path: Path) -> None:
    instrumentation, build_module, filesystem_module, research_module, task_module = _runtime_types()
    BuildManager = build_module.BuildManager
    FileSystemToolService = filesystem_module.FileSystemToolService
    ResearchManager = research_module.ResearchManager
    TaskOrchestrator = task_module.TaskOrchestrator
    events: list[dict[str, object]] = []

    def recorder(**payload):
        events.append(payload)
        return True

    failed_profile = build_module.BuildProfile("pytest", ["python", "-m", "pytest"], "tests")
    monkeypatch.setattr(
        BuildManager,
        "run",
        lambda self, profile, timeout=600: build_module.BuildResult(profile, 2, "two tests failed"),
    )
    monkeypatch.setattr(
        ResearchManager,
        "search",
        lambda self, query, max_results=6: research_module.ResearchResult(
            query,
            [research_module.ResearchSource("Official docs", "https://example.com", "summary")],
        ),
    )

    instrumentation.configure_runtime_instrumentation(recorder, workspace_provider=lambda: tmp_path)
    instrumentation.install_runtime_instrumentation()

    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    filesystem = FileSystemToolService([tmp_path])
    copied = filesystem.copy(source, destination)
    assert copied.destination.exists()

    orchestrator = TaskOrchestrator(
        history_file=tmp_path / "task_history.json",
        active_file=tmp_path / "active_task.json",
    )
    record, token = orchestrator.start("test task", "test")
    execute = orchestrator.wrap(record.task_id, token, lambda: 42)
    assert execute() == 42
    orchestrator.finish(record.task_id)

    build = BuildManager(_Workspace(tmp_path))
    result = build.run(failed_profile)
    assert result.succeeded is False
    pipeline = build.run_pipeline()
    assert pipeline.succeeded is False

    research = ResearchManager().search("official architecture guidance")
    assert len(research.sources) == 1

    actions = {str(event["action"]): event for event in events}
    assert actions["copy"]["status"] == "completed"
    assert actions["copy"]["metadata"]["source_name"] == "source.txt"
    assert str(tmp_path) not in repr(actions["copy"]["metadata"])
    assert actions["execute_task"]["status"] == "completed"
    assert actions["execute_task"]["metadata"]["task_name_chars"] == len("test task")
    assert actions["run_profile"]["status"] == "failed"
    assert actions["run_profile"]["metadata"]["return_code"] == 2
    assert actions["run_pipeline"]["status"] == "failed"
    assert actions["run_pipeline"]["metadata"]["failed_profile_count"] == 1
    assert actions["web_search"]["metadata"]["source_count"] == 1


def test_task_cancellation_is_recorded_without_changing_exception(monkeypatch, tmp_path: Path) -> None:
    instrumentation, _, _, _, task_module = _runtime_types()
    TaskOrchestrator = task_module.TaskOrchestrator
    events: list[dict[str, object]] = []

    def recorder(**payload):
        events.append(payload)
        return True

    instrumentation.configure_runtime_instrumentation(recorder, workspace_provider=lambda: tmp_path)
    instrumentation.install_runtime_instrumentation()
    orchestrator = TaskOrchestrator(
        history_file=tmp_path / "task_history.json",
        active_file=tmp_path / "active_task.json",
    )
    record, token = orchestrator.start("cancel task", "test")
    token.cancel()
    execute = orchestrator.wrap(record.task_id, token, lambda: None)

    with pytest.raises(InterruptedError):
        execute()

    event = next(item for item in events if item["action"] == "execute_task")
    assert event["status"] == "cancelled"
