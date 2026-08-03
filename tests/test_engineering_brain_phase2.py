from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.engineering_brain import (
    EngineeringPlanSchedulerBridge,
    EngineeringPlanStore,
    EngineeringTaskDecomposer,
)
from artmach_assistant.core.engineering_step_handlers import EngineeringStepHandlerRegistry
from artmach_assistant.core.self_improvement_scheduler import SelfImprovementScheduler
from artmach_assistant.core.self_improvement_supervisor import SelfImprovementSupervisor


def _report(status: str, *, actionable: bool = False) -> dict[str, object]:
    investigation: dict[str, object] = {
        "hypotheses": [
            {"cause": "invalid_sample_rate", "subsystem": "audio_output", "rank_score": 95, "evidence_ids": ["e1"]},
            {"cause": "wrong_device", "subsystem": "audio_output", "rank_score": 70, "evidence_ids": ["e2"]},
        ],
        "root_cause": None,
    }
    planner_task = None
    if actionable:
        investigation["root_cause"] = {"cause": "invalid_sample_rate", "subsystem": "audio_output"}
        planner_task = {
            "title": "Fix sample rate",
            "affected_files": ["core/voice.py"],
            "test_plan": ["tests/test_voice.py"],
            "evidence_ids": ["e1"],
            "risk": "low",
            "diagnostic_subsystem": "audio_output",
        }
    return {
        "request": "Ses sorunlarını gider",
        "domain": "voice",
        "status": status,
        "subsystems": ["audio_output"],
        "investigation": investigation,
        "planner_task": planner_task,
    }


def test_bridge_enqueues_measurement_as_engineering_job(tmp_path: Path) -> None:
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(_report("needs_evidence"))
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    jobs = EngineeringPlanSchedulerBridge().enqueue_ready_work(
        plan,
        scheduler,
        base_payload={"project_root": str(tmp_path)},
        plan_path=store.path,
    )
    assert len(jobs) == 1
    assert jobs[0].kind == "engineering"
    assert jobs[0].payload["engineering_action_type"] == "measurement"


def test_measurement_completion_advances_to_no_implementation_when_plan_only_measurement(tmp_path: Path) -> None:
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(_report("needs_evidence"))
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    bridge = EngineeringPlanSchedulerBridge()
    job = bridge.enqueue_ready_work(
        plan,
        scheduler,
        base_payload={
            "project_root": str(tmp_path),
            "runtime_evidence": [{"subsystem": "audio_output", "summary": "failure", "confidence": 90}],
        },
        plan_path=store.path,
    )[0]
    registry = EngineeringStepHandlerRegistry(scheduler)
    result = registry.execute(job.payload)
    assert result.status == "completed"
    assert store.load().status == "completed"
    assert json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))["evidence"]


def test_investigation_steps_advance_to_synthesis_and_block_without_root_cause(tmp_path: Path) -> None:
    report_path = tmp_path / "diagnostic.json"
    report_path.write_text(json.dumps(_report("investigating")), encoding="utf-8")
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(_report("investigating"))
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    bridge = EngineeringPlanSchedulerBridge()
    base = {"project_root": str(tmp_path), "diagnostic_report_path": str(report_path)}
    bridge.enqueue_ready_work(plan, scheduler, base_payload=base, plan_path=store.path)
    registry = EngineeringStepHandlerRegistry(scheduler)
    for job in list(scheduler.jobs(statuses=["pending"])):
        result = registry.execute(job.payload)
        scheduler.finish(job.job_id, result.status)
    synthesis_jobs = [job for job in scheduler.jobs(statuses=["pending"]) if job.payload.get("engineering_action_type") == "synthesis"]
    assert len(synthesis_jobs) == 1
    synthesis = registry.execute(synthesis_jobs[0].payload)
    assert synthesis.status == "blocked"
    assert "root cause" in synthesis.message


def test_code_analysis_completion_enqueues_implementation_cycle(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "voice.py").write_text("VALUE = 1\n", encoding="utf-8")
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(_report("actionable", actionable=True))
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    job = EngineeringPlanSchedulerBridge().enqueue_ready_work(
        plan, scheduler, base_payload={"project_root": str(tmp_path)}, plan_path=store.path,
    )[0]
    result = EngineeringStepHandlerRegistry(scheduler).execute(job.payload)
    assert result.status == "completed"
    cycle_jobs = [item for item in scheduler.jobs(statuses=["pending"]) if item.kind == "cycle"]
    assert len(cycle_jobs) == 1
    assert cycle_jobs[0].payload["engineering_step_id"] == plan.steps[1].step_id


def test_supervisor_runs_engineering_handler_backward_compatibly(tmp_path: Path) -> None:
    called: list[str] = []
    supervisor = SelfImprovementSupervisor(
        tmp_path,
        cycle_handler=lambda _p: {"status": "blocked"},
        promotion_handler=lambda _p: {"status": "blocked"},
        approval_handler=lambda _p: {"status": "waiting_approval"},
        engineering_handler=lambda payload: called.append(str(payload["name"])) or {"status": "completed"},
        idle_seconds=0,
    )
    supervisor.enqueue_engineering({"name": "measure"})
    result = supervisor.tick()
    assert result.status == "completed"
    assert called == ["measure"]


def test_scheduler_recovers_running_engineering_job(tmp_path: Path) -> None:
    state = tmp_path / "queue.json"
    scheduler = SelfImprovementScheduler(state)
    job = scheduler.enqueue("engineering", {"step": "one"})
    scheduler.mark_running(job.job_id)
    recovered = SelfImprovementScheduler(state)
    rows = recovered.jobs()
    assert rows[0].status == "pending"
    assert "recovered" in rows[0].last_error
