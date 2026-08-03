from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from artmach_assistant.core.engineering_brain import (
    EngineeringBudget,
    EngineeringPlan,
    EngineeringPlanStore,
    EngineeringStep,
)
from artmach_assistant.core.engineering_progress import (
    EngineeringAdaptiveReplanner,
    EngineeringProgressTracker,
)


def _step(step_id: str, action: str, status: str = "pending", *, depends=(), attempts=0) -> EngineeringStep:
    return EngineeringStep(step_id, step_id, "voice", action, status, tuple(depends), (), (), ("done",), "low", 50, attempts)


def _plan(tmp_path: Path, steps: tuple[EngineeringStep, ...], status: str = "ready"):
    now = datetime.now(timezone.utc).isoformat()
    plan = EngineeringPlan(1, "engp1-test", "fix voice", "voice", status, now, now, "investigating", "", EngineeringBudget(maximum_steps=8, maximum_implementation_steps=2, maximum_commits=2, maximum_changed_files_per_step=2), steps)
    store = EngineeringPlanStore(tmp_path / "plan.json")
    store.save(plan)
    return store, plan


def test_progress_snapshot_counts_and_percentage(tmp_path: Path) -> None:
    _store, plan = _plan(tmp_path, (_step("a", "measurement", "completed"), _step("b", "investigation", "pending", depends=("a",))))
    snapshot = EngineeringProgressTracker(tmp_path / "progress.json").observe(plan)
    assert snapshot.progress_percent == 50
    assert snapshot.completed_steps == 1
    assert snapshot.pending_steps == 1
    assert snapshot.recommendation == "continue"


def test_stalled_running_step_requests_replan(tmp_path: Path) -> None:
    _store, plan = _plan(tmp_path, (_step("a", "measurement", "running"),), status="running")
    tracker = EngineeringProgressTracker(tmp_path / "progress.json", stall_after_seconds=10)
    start = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
    tracker.observe(plan, now=start)
    snapshot = tracker.observe(plan, now=start + timedelta(seconds=11))
    assert snapshot.stalled_step_ids == ("a",)
    assert snapshot.recommendation == "replan"


def test_completed_plan_reports_complete(tmp_path: Path) -> None:
    _store, plan = _plan(tmp_path, (_step("a", "measurement", "completed"),), status="completed")
    snapshot = EngineeringProgressTracker(tmp_path / "progress.json").observe(plan)
    assert snapshot.progress_percent == 100
    assert snapshot.recommendation == "complete"


def test_replanner_adds_bounded_recovery_measurement(tmp_path: Path) -> None:
    store, _plan_value = _plan(tmp_path, (_step("a", "investigation", "blocked"),), status="blocked")
    updated = EngineeringAdaptiveReplanner().replan(store, "a", reason="evidence tied")
    assert len(updated.steps) == 2
    target = next(step for step in updated.steps if step.step_id == "a")
    recovery = next(step for step in updated.steps if step.step_id != "a")
    assert target.status == "pending"
    assert recovery.action_type == "measurement"
    assert recovery.step_id in target.depends_on
    assert updated.status == "ready"


def test_replanner_does_not_duplicate_recovery(tmp_path: Path) -> None:
    store, _plan_value = _plan(tmp_path, (_step("a", "synthesis", "blocked"),), status="blocked")
    first = EngineeringAdaptiveReplanner().replan(store, "a", reason="uncertain")
    second = EngineeringAdaptiveReplanner().replan(store, "a", reason="uncertain")
    assert len(first.steps) == len(second.steps) == 2


def test_implementation_failure_is_not_auto_replanned(tmp_path: Path) -> None:
    store, plan = _plan(tmp_path, (_step("a", "implementation", "blocked"),), status="blocked")
    updated = EngineeringAdaptiveReplanner().replan(store, "a", reason="tests failed")
    assert updated == plan
