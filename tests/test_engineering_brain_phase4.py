from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.engineering_brain import EngineeringPlanSchedulerBridge, EngineeringPlanStore, EngineeringTaskDecomposer
from artmach_assistant.core.engineering_memory import EngineeringMemoryAdvisor
from artmach_assistant.core.engineering_step_handlers import EngineeringStepHandlerRegistry
from artmach_assistant.core.self_improvement_scheduler import SelfImprovementScheduler


def _repo(path: Path) -> None:
    path.write_text(json.dumps({"schema_version": 1, "records": [{
        "record_id": "rec-1", "outcome": "success", "experiment_id": "exp-1", "candidate_id": "c-1",
        "title": "Piper invalid sample rate", "problem_pattern": "voice piper invalid sample rate",
        "solution_pattern": "negotiate supported sample rate", "applicability": ["voice"], "constraints": [],
        "validation_steps": ["focused", "full"], "affected_files": ["core/voice_service.py"], "risk": "low",
        "confidence_score": 95, "focused_tests_passed": 8, "full_tests_passed": 1900, "failure_message": "",
        "result_digest": "d" * 64, "selection_reliability": 90, "selection_strategy": "diagnostic",
        "selection_accepted": True, "observation_count": 3,
    }]}), encoding="utf-8")


def _report() -> dict[str, object]:
    return {
        "request": "voice piper invalid sample rate sorununu düzelt",
        "domain": "voice",
        "status": "root_cause_identified",
        "subsystems": ["text_to_speech"],
        "investigation": {"root_cause": {"cause": "invalid_sample_rate"}},
        "planner_task": {
            "title": "Piper sample rate negotiation",
            "diagnostic_subsystem": "text_to_speech",
            "affected_files": ["core/voice_service.py"],
            "evidence_ids": ["ev-1"],
            "test_plan": ["tests/test_voice_service.py"],
            "risk": "low",
        },
    }


def test_memory_review_precedes_code_analysis(tmp_path: Path) -> None:
    repository = tmp_path / "knowledge.json"
    _repo(repository)
    memory = EngineeringMemoryAdvisor().inspect(_report()["request"], repository_paths=[repository]).to_dict()
    plan = EngineeringTaskDecomposer().build(_report(), memory_context=memory)
    assert plan.steps[0].action_type == "memory_review"
    analysis = next(step for step in plan.steps if step.action_type == "code_analysis")
    assert plan.steps[0].step_id in analysis.depends_on


def test_memory_review_handler_advances_plan(tmp_path: Path) -> None:
    repository = tmp_path / "knowledge.json"
    _repo(repository)
    memory = EngineeringMemoryAdvisor().inspect(_report()["request"], repository_paths=[repository]).to_dict()
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(_report(), memory_context=memory)
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    job = EngineeringPlanSchedulerBridge().enqueue_ready_work(
        plan,
        scheduler,
        base_payload={"project_root": str(tmp_path), "engineering_memory": memory},
        plan_path=store.path,
    )[0]
    result = EngineeringStepHandlerRegistry(scheduler).execute(job.payload)
    assert result.status == "completed"
    assert any(item.payload.get("engineering_action_type") == "code_analysis" for item in scheduler.jobs(statuses=["pending"]))
