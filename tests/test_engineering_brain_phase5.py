from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.engineering_brain import EngineeringBudget, EngineeringPlan, EngineeringPlanStore, EngineeringStep
from artmach_assistant.core.engineering_step_handlers import EngineeringStepHandlerRegistry
from artmach_assistant.core.self_improvement_scheduler import SelfImprovementScheduler


def _plan(path: Path) -> EngineeringPlan:
    now = datetime.now(timezone.utc).isoformat()
    plan = EngineeringPlan(
        1, "engp1-phase5", "diagnose voice", "voice", "ready", now, now, "investigating", "",
        EngineeringBudget(maximum_steps=8, maximum_implementation_steps=2, maximum_commits=2, maximum_changed_files_per_step=2),
        (
            EngineeringStep("step-a", "measure", "audio", "measurement", "pending", (), ("logs",), (), ("evidence",), "low", 100),
            EngineeringStep("step-b", "investigate", "audio", "investigation", "pending", ("step-a",), (), (), ("cause",), "low", 90),
        ),
    )
    EngineeringPlanStore(path).save(plan)
    return plan


def test_handler_writes_progress_snapshot_after_completion(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    _plan(plan_path)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    registry = EngineeringStepHandlerRegistry(scheduler)
    progress = tmp_path / "progress.json"
    result = registry.execute({
        "engineering_plan_path": str(plan_path),
        "engineering_step_id": "step-a",
        "engineering_progress_path": str(progress),
        "runtime_evidence": [{"subsystem": "audio", "message": "sample rate error"}],
    })
    assert result.status == "completed"
    payload = json.loads(progress.read_text(encoding="utf-8"))
    assert payload["snapshot"]["completed_steps"] == 1
    assert payload["snapshot"]["progress_percent"] == 50


def test_blocked_step_can_replan_and_enqueue_recovery(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    _plan(plan_path)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    registry = EngineeringStepHandlerRegistry(scheduler)
    result = registry.execute({
        "engineering_plan_path": str(plan_path),
        "engineering_step_id": "step-a",
        "engineering_progress_path": str(tmp_path / "progress.json"),
        "adaptive_replanning": True,
    })
    assert result.status == "blocked"
    revised = EngineeringPlanStore(plan_path).load()
    assert len(revised.steps) == 3
    recovery = next(step for step in revised.steps if step.step_id not in {"step-a", "step-b"})
    assert recovery.action_type == "measurement"
    assert any(job.kind == "engineering" and job.status == "pending" for job in scheduler.jobs())
