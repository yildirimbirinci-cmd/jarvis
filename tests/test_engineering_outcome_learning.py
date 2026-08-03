from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from artmach_assistant.core.engineering_brain import EngineeringBudget, EngineeringPlan, EngineeringPlanStore, EngineeringStep
from artmach_assistant.core.engineering_outcome_learning import EngineeringOutcomeLearner, EngineeringOutcomePlanAdapter


def _plan(*, plan_id: str = "engp1-a", implementation: str = "completed", analysis: str = "completed", error: str = "") -> EngineeringPlan:
    now = datetime(2026, 8, 3, tzinfo=timezone.utc).isoformat()
    status = "completed" if implementation == "completed" and analysis == "completed" else "blocked"
    return EngineeringPlan(
        1, plan_id, "Piper sorununu çöz", "voice", status, now, now,
        "root_cause_identified", "invalid_sample_rate", EngineeringBudget(),
        (
            EngineeringStep("analysis", "Kapsam", "tts", "code_analysis", analysis, (), (), ("core/voice.py",), ("scope",), "low", 80, last_error=error if analysis != "completed" else ""),
            EngineeringStep("implement", "Uygula", "tts", "implementation", implementation, ("analysis",), (), ("core/voice.py",), ("tests",), "low", 80, last_error=error if implementation != "completed" else ""),
        ),
    )


def test_summarizes_terminal_plan_and_verified_files() -> None:
    record = EngineeringOutcomeLearner().summarize(_plan())
    assert record.recommendation == "reuse_verified_sequence"
    assert record.successful_implementation_files == ("core/voice.py",)
    assert next(item for item in record.action_outcomes if item.action_type == "implementation").success_rate == 100


def test_non_terminal_plan_is_not_learned() -> None:
    plan = _plan()
    plan = EngineeringPlan(*plan.__dict__.values()) if False else plan
    pending = EngineeringPlan(
        plan.schema_version, plan.plan_id, plan.request, plan.domain, "ready", plan.created_at, plan.updated_at,
        plan.diagnostic_status, plan.root_cause, plan.budget,
        (plan.steps[0], EngineeringStep("implement", "Uygula", "tts", "implementation", "pending", ("analysis",), (), ("core/voice.py",), ("tests",), "low", 80)),
    )
    with pytest.raises(ValueError, match="terminal plan"):
        EngineeringOutcomeLearner().summarize(pending)


def test_repository_is_idempotent_per_plan(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.json"
    learner = EngineeringOutcomeLearner()
    learner.record(_plan(), path)
    learner.record(_plan(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["records"]) == 1


def test_profile_detects_repeated_failed_action(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.json"
    learner = EngineeringOutcomeLearner()
    learner.record(_plan(plan_id="a", implementation="failed", error="compile failed"), path)
    learner.record(_plan(plan_id="b", implementation="blocked", error="tests failed"), path)
    profile = learner.profile(path, domain="voice")
    assert "implementation" in profile.repeatedly_failed_actions
    assert profile.recommendation == "reorder_away_from_repeated_failures"


def test_adapter_lowers_repeated_failure_and_preserves_completed_steps(tmp_path: Path) -> None:
    repository = tmp_path / "outcomes.json"
    learner = EngineeringOutcomeLearner()
    learner.record(_plan(plan_id="a", implementation="failed", error="compile failed"), repository)
    learner.record(_plan(plan_id="b", implementation="blocked", error="tests failed"), repository)
    profile = learner.profile(repository, domain="voice")
    base = _plan()
    pending = EngineeringPlan(
        base.schema_version, "next", base.request, base.domain, "ready", base.created_at, base.updated_at,
        base.diagnostic_status, base.root_cause, base.budget,
        (
            base.steps[0],
            EngineeringStep("implement", "Uygula", "tts", "implementation", "pending", ("analysis",), (), ("core/voice.py",), ("tests",), "low", 90),
        ),
    )
    adapted = EngineeringOutcomePlanAdapter().adapt(pending, profile)
    assert adapted.steps[0].priority == pending.steps[0].priority
    assert adapted.steps[1].priority == 70


def test_adapter_can_persist_adapted_plan(tmp_path: Path) -> None:
    repository = tmp_path / "outcomes.json"
    learner = EngineeringOutcomeLearner()
    learner.record(_plan(), repository)
    profile = learner.profile(repository, domain="voice")
    plan = _plan()
    pending = EngineeringPlan(
        plan.schema_version, "next", plan.request, plan.domain, "ready", plan.created_at, plan.updated_at,
        plan.diagnostic_status, plan.root_cause, plan.budget,
        (EngineeringStep("implement", "Uygula", "tts", "implementation", "pending", (), (), ("core/voice.py",), ("tests",), "low", 80),),
    )
    store = EngineeringPlanStore(tmp_path / "plan.json")
    store.save(pending)
    updated = EngineeringOutcomePlanAdapter().adapt_store(store, profile)
    assert updated.steps[0].priority > 80
    assert store.load().steps[0].priority == updated.steps[0].priority
