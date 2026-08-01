from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory


def test_project_memory_persists_goal_decisions_tasks_and_acceptance(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")

    memory.set_goal(project, "Create a safe desktop assistant")
    requirement = memory.add_requirement(project, "Run without GitHub")
    decision = memory.add_decision(
        project,
        "Use local Ollama models",
        rationale="The program must work offline",
    )
    task = memory.add_task(project, "Add runtime diagnostics")
    memory.add_issue(project, "Voice output can select an invalid sample rate")
    acceptance = memory.add_acceptance_criterion(
        project,
        "A failed change must roll back automatically",
    )
    completed = memory.complete_task(project, task.entry_id)

    state = memory.load(project)
    assert state.goal == "Create a safe desktop assistant"
    assert state.entry(requirement.entry_id) is not None
    assert state.entry(decision.entry_id).rationale == "The program must work offline"
    assert completed.status == "completed"
    assert state.entry(acceptance.entry_id) is not None
    context = memory.model_context(project)
    assert "Run without GitHub" in context
    assert "Use local Ollama models" in context
    assert "Add runtime diagnostics" not in context
    assert "failed change" in context


def test_duplicate_active_entry_returns_existing_record(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")

    first = memory.add_requirement(project, "Keep the public API stable")
    second = memory.add_requirement(project, "  Keep the public API stable  ")

    assert second.entry_id == first.entry_id
    assert len(memory.load(project).by_kind("requirement")) == 1


def test_project_memories_are_isolated_by_workspace(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")

    memory.set_goal(first_root, "First goal")
    memory.set_goal(second_root, "Second goal")

    assert memory.load(first_root).goal == "First goal"
    assert memory.load(second_root).goal == "Second goal"
    assert memory.path_for(first_root) != memory.path_for(second_root)


def test_corrupt_project_memory_is_quarantined(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    path = memory.path_for(project)
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"root":', encoding="utf-8")

    state = memory.load(project)

    assert state.goal == ""
    assert not path.exists()
    assert list(path.parent.glob(path.stem + ".corrupt_*.json"))


def test_relevant_model_context_selects_matching_entries_and_keeps_boundaries(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.set_goal(project, "Build a reliable local assistant")
    memory.add_requirement(project, "Piper output sample rate must match the speaker")
    memory.add_requirement(project, "Database export must support CSV")
    memory.add_decision(project, "Do not change public APIs without approval")
    memory.add_issue(project, "Piper playback fails at 22050 Hz")
    memory.add_issue(project, "Thumbnail cache can grow too large")
    memory.add_acceptance_criterion(project, "All existing tests must still pass")

    context = memory.relevant_model_context(
        project,
        "Piper sample rate playback error",
    )

    assert "Piper output sample rate" in context
    assert "Piper playback fails" in context
    assert "Database export" not in context
    assert "Thumbnail cache" not in context
    assert "Do not change public APIs" in context
    assert "All existing tests" in context


def test_relevant_context_matches_turkish_unicode_terms(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.add_issue(project, "Hoparlör örnekleme oranı uyumsuz")
    memory.add_issue(project, "Dosya önizleme önbelleği büyük")

    context = memory.relevant_model_context(project, "örnekleme oranını düzelt")

    assert "Hoparlör örnekleme oranı" in context
    assert "önizleme önbelleği" not in context
