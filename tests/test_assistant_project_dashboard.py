from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_gui_apply_path_uses_task_aware_project_application() -> None:
    engine = object.__new__(AssistantEngine)
    runtime = SimpleNamespace(has_pending_project_edit=True)
    engine.project_improvements = runtime
    engine.apply_pending_project_proposal = lambda: "task-aware"  # type: ignore[method-assign]
    assert engine.apply_pending_edit() == "task-aware"


def test_prepare_project_item_returns_proposal_and_tracks_task(monkeypatch, tmp_path) -> None:
    engine = object.__new__(AssistantEngine)
    engine._pending_development_item_id = ""
    engine.project_development_planner = object()
    engine.project_development_progress = object()
    engine._development_root = lambda own_code=False: tmp_path  # type: ignore[method-assign]
    engine._project_memory_service = lambda: object()  # type: ignore[method-assign]
    engine._project_improvement_runtime = lambda: object()  # type: ignore[method-assign]
    proposal = object()
    target = SimpleNamespace(item_id="TSK-1234567890", is_task=True)

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def prepare(self, root, item_id):
            assert item_id == "TSK-1234567890"
            return target, proposal

    monkeypatch.setitem(
        AssistantEngine.prepare_project_development_item.__globals__,
        "ProjectDevelopmentExecutor",
        FakeExecutor,
    )
    assert engine.prepare_project_development_item("TSK-1234567890") is proposal
    assert engine._pending_development_item_id == "TSK-1234567890"
