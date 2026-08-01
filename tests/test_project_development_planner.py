from pathlib import Path

from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_development_planner import ProjectDevelopmentPlanner


class WorkspaceStub:
    def contextual_snapshot(self, query, **kwargs):
        if "ses" in query.casefold():
            return "DOSYA: core/voice_service.py\nDOSYA: tests/test_voice.py"
        return ""


def test_plan_uses_active_evidence_and_acceptance(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.set_goal(root, "Kesilebilir yerel sesli asistan")
    issue = memory.add_issue(root, "Ses üretimi kullanıcı araya girdiğinde durmuyor")
    memory.add_acceptance_criterion(root, "Ses kesme gecikmesi 300 ms altında olmalı")
    plan = ProjectDevelopmentPlanner(memory, WorkspaceStub()).create_plan(root)
    assert len(plan.items) == 1
    item = plan.items[0]
    assert issue.entry_id in item.source_entry_ids
    assert item.plan_id.startswith("PLN-")
    assert "core/voice_service.py" in item.candidate_paths
    assert "300 ms" in item.acceptance


def test_plan_does_not_duplicate_existing_active_task(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    text = "Yerel test modunu tamamla"
    memory.add_requirement(root, text)
    memory.add_task(root, f"Gereksinimi uygula: {text}")
    plan = ProjectDevelopmentPlanner(memory, WorkspaceStub()).create_plan(root)
    assert plan.items == ()


def test_persist_plan_creates_bounded_tasks(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.add_requirement(root, "Dosya işlemlerini geri alınabilir yap")
    planner = ProjectDevelopmentPlanner(memory, WorkspaceStub())
    ids = planner.persist_plan_tasks(planner.create_plan(root))
    assert len(ids) == 1
    assert ids[0].startswith("TSK-")
    assert memory.load(root).entry(ids[0]) is not None
