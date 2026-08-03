from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.engineering_brain import EngineeringPlanSchedulerBridge, EngineeringPlanStore, EngineeringTaskDecomposer
from artmach_assistant.core.engineering_step_handlers import EngineeringStepHandlerRegistry
from artmach_assistant.core.research_orchestrator import ResearchOrchestrator
from artmach_assistant.core.self_improvement_scheduler import SelfImprovementScheduler


def _report(status: str = "needs_evidence") -> dict[str, object]:
    return {
        "request": "Ses sorunlarını gider",
        "domain": "voice",
        "status": status,
        "subsystems": ["audio_output"],
        "findings": [],
        "planner_task": None,
    }


def test_research_strategy_steps_precede_measurement(tmp_path: Path) -> None:
    report = _report()
    strategy = ResearchOrchestrator().plan(report)
    plan = EngineeringTaskDecomposer().build(report, research_strategy=strategy.to_dict())
    assert plan.steps[0].action_type == "research"
    measurement = next(item for item in plan.steps if item.action_type == "measurement")
    assert plan.steps[0].step_id in measurement.depends_on


def test_runtime_research_advances_to_measurement(tmp_path: Path) -> None:
    report = _report()
    strategy = ResearchOrchestrator().plan(report)
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(report, research_strategy=strategy.to_dict())
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    job = EngineeringPlanSchedulerBridge().enqueue_ready_work(
        plan,
        scheduler,
        base_payload={
            "project_root": str(tmp_path),
            "research_strategy": strategy.to_dict(),
            "runtime_evidence": [{"subsystem": "audio_output", "summary": "invalid sample rate"}],
        },
        plan_path=store.path,
    )[0]
    result = EngineeringStepHandlerRegistry(scheduler).execute(job.payload)
    assert result.status == "completed"
    assert any(item.payload.get("engineering_action_type") == "measurement" for item in scheduler.jobs(statuses=["pending"]))


def test_web_research_blocks_without_permission(tmp_path: Path) -> None:
    report = {"request": "3ds Max API araştır", "domain": "3ds_max", "status": "investigating", "subsystems": ["api"]}
    strategy = ResearchOrchestrator().plan(report)
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(report, research_strategy=strategy.to_dict())
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    jobs = EngineeringPlanSchedulerBridge().enqueue_ready_work(
        plan, scheduler,
        base_payload={"project_root": str(tmp_path), "research_strategy": strategy.to_dict()},
        plan_path=store.path,
    )
    web_job = next(job for job in jobs if "Birincil" in job.payload["step_title"])
    result = EngineeringStepHandlerRegistry(scheduler).execute(web_job.payload)
    assert result.status == "blocked"
    artifact = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert artifact["permission_required"] is True


def test_web_research_uses_injected_provider_after_permission(tmp_path: Path) -> None:
    report = {"request": "3ds Max API araştır", "domain": "3ds_max", "status": "investigating", "subsystems": ["api"]}
    strategy = ResearchOrchestrator().plan(report, allow_web_research=True)
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(report, research_strategy=strategy.to_dict())
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    jobs = EngineeringPlanSchedulerBridge().enqueue_ready_work(
        plan, scheduler,
        base_payload={"project_root": str(tmp_path), "research_strategy": strategy.to_dict(), "allow_web_research": True},
        plan_path=store.path,
    )
    web_job = next(job for job in jobs if "Birincil" in job.payload["step_title"])
    registry = EngineeringStepHandlerRegistry(
        scheduler,
        web_research_provider=lambda request, inputs: {"sources": [{"url": "official", "title": request}], "findings": ["api"]},
    )
    result = registry.execute(web_job.payload)
    assert result.status == "completed"
