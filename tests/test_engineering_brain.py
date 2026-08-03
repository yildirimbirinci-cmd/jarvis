from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.engineering_brain import (
    EngineeringBudget,
    EngineeringPlanSchedulerBridge,
    EngineeringPlanStore,
    EngineeringTaskDecomposer,
)
from artmach_assistant.core.self_improvement_scheduler import SelfImprovementScheduler


def _report(status: str, *, planner: bool = False) -> dict[str, object]:
    investigation: dict[str, object] = {
        "hypotheses": [
            {
                "cause": "invalid_sample_rate",
                "subsystem": "audio_output",
                "evidence_ids": ["e1", "e2"],
            },
            {
                "cause": "wrong_output_device",
                "subsystem": "audio_output",
                "evidence_ids": ["e3"],
            },
        ],
        "root_cause": None,
    }
    planner_task = None
    if planner:
        investigation["root_cause"] = {
            "cause": "invalid_sample_rate",
            "subsystem": "audio_output",
        }
        planner_task = {
            "title": "Piper sample-rate negotiation düzeltmesi",
            "affected_files": ["core/voice_service.py", "config.py"],
            "test_plan": ["tests/test_voice_service.py", "full regression"],
            "evidence_ids": ["e1", "e2"],
            "risk": "low",
            "diagnostic_subsystem": "audio_output",
        }
    return {
        "schema_version": 1,
        "request": "Ses sorunlarını gider",
        "domain": "voice",
        "status": status,
        "subsystems": ["audio_input", "audio_output", "text_to_speech"],
        "findings": [],
        "planner_task": planner_task,
        "investigation": investigation,
    }


def test_needs_evidence_creates_measurement_graph_without_implementation() -> None:
    plan = EngineeringTaskDecomposer().build(_report("needs_evidence"))
    assert plan.status == "ready"
    assert {step.action_type for step in plan.steps} == {"measurement"}
    assert len(plan.steps) == 3


def test_investigating_creates_hypotheses_then_synthesis() -> None:
    plan = EngineeringTaskDecomposer().build(_report("investigating"))
    assert [step.action_type for step in plan.steps] == [
        "investigation", "investigation", "synthesis"
    ]
    assert set(plan.steps[-1].depends_on) == {plan.steps[0].step_id, plan.steps[1].step_id}
    assert all(step.action_type != "implementation" for step in plan.steps)


def test_actionable_creates_analysis_before_bounded_implementation() -> None:
    plan = EngineeringTaskDecomposer(
        budget=EngineeringBudget(maximum_changed_files_per_step=1)
    ).build(_report("actionable", planner=True))
    assert [step.action_type for step in plan.steps] == ["code_analysis", "implementation"]
    assert plan.steps[1].depends_on == (plan.steps[0].step_id,)
    assert plan.steps[1].affected_files == ("core/voice_service.py",)


def test_plan_store_recovers_running_step(tmp_path: Path) -> None:
    store = EngineeringPlanStore(tmp_path / "plans" / "voice.json")
    plan = EngineeringTaskDecomposer().build(_report("actionable", planner=True))
    store.save(plan)
    running = store.update_step(plan.steps[0].step_id, "running")
    assert running.steps[0].status == "running"
    recovered = EngineeringPlanStore(store.path).load()
    assert recovered.steps[0].status == "pending"
    assert "recovered" in recovered.steps[0].last_error


def test_delegated_policy_controls_implementation_and_file_budget(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "maximum_commits": 1,
        "maximum_changed_files": 1,
    }), encoding="utf-8")
    plan = EngineeringTaskDecomposer().build(
        _report("actionable", planner=True), delegated_policy_path=policy,
    )
    implementation = next(step for step in plan.steps if step.action_type == "implementation")
    assert plan.budget.maximum_implementation_steps == 1
    assert plan.budget.maximum_commits == 1
    assert implementation.affected_files == ("core/voice_service.py",)


def test_bridge_waits_for_dependencies_then_enqueues_one_cycle(tmp_path: Path) -> None:
    store = EngineeringPlanStore(tmp_path / "plan.json")
    plan = EngineeringTaskDecomposer().build(_report("actionable", planner=True))
    store.save(plan)
    scheduler = SelfImprovementScheduler(tmp_path / "queue.json")
    bridge = EngineeringPlanSchedulerBridge()
    assert bridge.enqueue_ready_cycles(
        plan, scheduler, base_payload={"project_root": "project"}, plan_path=store.path,
    ) == ()
    updated = store.update_step(plan.steps[0].step_id, "completed")
    first = bridge.enqueue_ready_cycles(
        updated, scheduler, base_payload={"project_root": "project"}, plan_path=store.path,
    )
    second = bridge.enqueue_ready_cycles(
        updated, scheduler, base_payload={"project_root": "project"}, plan_path=store.path,
    )
    assert len(first) == 1
    assert second == first
    assert len(scheduler.jobs()) == 1
    assert scheduler.jobs()[0].payload["engineering_step_id"] == plan.steps[1].step_id
