from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_improvement_runtime import ProjectImprovementRuntime


def test_relevant_project_memory_uses_turkish_query_and_hides_secrets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.set_goal(project, "Windows masaüstü asistanı")
    memory.add_requirement(project, "Rapor ekranı koyu tema kullanmalı")
    memory.add_issue(project, "Hoparlör örnekleme oranı bazı aygıtlarda uyuşmuyor")
    memory.add_decision(project, "Ses çıkışı başarısız olursa güvenli fallback kullan")
    memory.add_requirement(project, "api_key=super-secret hiçbir rapora yazılmamalı")

    context = memory.relevant_model_context(
        project, "örnekleme oranı hatasını düzelt", limit=5000
    )

    assert "örnekleme oranı" in context
    assert "güvenli fallback" in context
    assert "koyu tema" not in context
    assert "super-secret" not in context
    assert "[GIZLENDI]" in memory.model_context(project)


def test_assistant_injects_project_memory_only_for_project_questions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    own = tmp_path / "jarvis"
    project.mkdir()
    own.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.set_goal(project, "Yeni masaüstü programını tamamla")
    memory.add_issue(project, "Dosya taraması yavaş")

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = SimpleNamespace(project_context_char_limit=8000)
    engine.project_memory = memory
    engine.workspace = SimpleNamespace(require_root=lambda: project)
    engine.own_project_root = lambda: own

    assert engine._conversation_project_context("Bugün hava nasıl?") == ""
    context = engine._conversation_project_context("Projede dosya taraması neden yavaş?")
    assert "Dosya taraması yavaş" in context
    assert str(project) in context


def test_improvement_runtime_passes_current_instruction_to_context_provider(
    tmp_path: Path,
) -> None:
    captured = {}

    def provider(root: Path, query: str) -> str:
        captured["root"] = root
        captured["query"] = query
        return "relevant memory"

    runtime = ProjectImprovementRuntime(
        workspace=SimpleNamespace(),
        editor=SimpleNamespace(),
        builder=SimpleNamespace(),
        researcher=SimpleNamespace(),
        dialogue=SimpleNamespace(),
        config=SimpleNamespace(project_context_char_limit=8000),
        own_root_provider=lambda: tmp_path,
        code_model_provider=lambda: "coder",
        project_context_provider=provider,
    )

    value = runtime._project_context(tmp_path, "örnekleme sorununu düzelt")

    assert value == "relevant memory"
    assert captured == {"root": tmp_path, "query": "örnekleme sorununu düzelt"}
