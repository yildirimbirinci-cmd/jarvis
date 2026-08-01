from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core.project_development_executor import ProjectDevelopmentExecutor
from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_development_planner import ProjectDevelopmentPlanner
from artmach_assistant.core.workspace import WorkspaceError


class WorkspaceStub:
    def contextual_snapshot(self, query, **kwargs):
        if "cache" in query.casefold() or "önbellek" in query.casefold():
            return "DOSYA: core/cache.py\nDOSYA: tests/test_cache.py"
        return ""


class RuntimeStub:
    def __init__(self):
        self.calls = []

    def prepare_edit(self, instruction, **kwargs):
        self.calls.append((instruction, kwargs))
        return SimpleNamespace(
            summary="Önbellek düzeltmesi",
            files=(SimpleNamespace(path="core/cache.py"), SimpleNamespace(path="tests/test_cache.py")),
        )


def test_executor_resolves_plan_item_and_prepares_bounded_multifile_draft(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.add_issue(root, "Önbellek hatasında eski veri dönüyor")
    memory.add_acceptance_criterion(root, "Önbellek testleri geçmeli")
    planner = ProjectDevelopmentPlanner(memory, WorkspaceStub())
    plan = planner.create_plan(root)
    runtime = RuntimeStub()
    executor = ProjectDevelopmentExecutor(memory, planner, runtime)

    target, proposal = executor.prepare(root, plan.items[0].plan_id)

    assert target.item_id.startswith("PLN-")
    assert target.candidate_paths == ("core/cache.py", "tests/test_cache.py")
    assert tuple(item.path for item in proposal.files) == ("core/cache.py", "tests/test_cache.py")
    instruction, kwargs = runtime.calls[0]
    assert target.item_id in instruction
    assert kwargs["approved_paths"] == target.candidate_paths
    assert "Önbellek testleri geçmeli" in kwargs["evidence_context"]


def test_executor_resolves_active_task_and_rejects_completed_task(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    task = memory.add_task(root, "Önbellek katmanını düzelt")
    planner = ProjectDevelopmentPlanner(memory, WorkspaceStub())
    executor = ProjectDevelopmentExecutor(memory, planner, RuntimeStub())

    target = executor.resolve(root, task.entry_id)
    assert target.is_task is True
    assert target.candidate_paths == ("core/cache.py", "tests/test_cache.py")

    memory.complete_task(root, task.entry_id)
    with pytest.raises(WorkspaceError, match="etkin değil"):
        executor.resolve(root, task.entry_id)


def test_executor_rejects_stale_plan_identifier(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    planner = ProjectDevelopmentPlanner(memory, WorkspaceStub())
    executor = ProjectDevelopmentExecutor(memory, planner, RuntimeStub())
    with pytest.raises(WorkspaceError, match="güncel proje planında bulunamadı"):
        executor.resolve(root, "PLN-AAAAAAAAAA")


def test_executor_enforces_strict_task_order(tmp_path: Path):
    from artmach_assistant.core.project_development_progress import ProjectDevelopmentProgress

    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    first = memory.add_task(root, "Önbellek katmanını düzelt")
    second = memory.add_task(root, "Önbellek testlerini genişlet")
    planner = ProjectDevelopmentPlanner(memory, WorkspaceStub())
    progress = ProjectDevelopmentProgress(tmp_path / "progress", memory)
    progress.initialize(root, strict_order=True)
    executor = ProjectDevelopmentExecutor(memory, planner, RuntimeStub(), progress)

    with pytest.raises(Exception, match="sırayla"):
        executor.prepare(root, second.entry_id)

    target, _proposal = executor.prepare(root, first.entry_id)
    assert target.item_id == first.entry_id
    assert progress.current_task(root).entry_id == first.entry_id
