from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from artmach_assistant.core.engineering_brain import (
    EngineeringBudget,
    EngineeringPlan,
    EngineeringPlanStore,
    EngineeringStep,
)
from artmach_assistant.core.engineering_long_task_validation import EngineeringLongTaskValidator


def _artifact(root: Path, plan_id: str, step_id: str, action: str) -> str:
    path = root / f"{step_id}.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "plan_id": plan_id,
        "step_id": step_id,
        "action_type": action,
        "status": "completed",
    }), encoding="utf-8")
    return str(path)


def _audit_row(previous_hash: str, *, status: str = "committed", push: bool = False) -> dict[str, object]:
    row = {
        "status": status,
        "policy_id": "dap1-night",
        "previous_hash": previous_hash,
        "push_performed": push,
        "changed_files": ["core/voice.py"],
    }
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    row["record_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return row


def _completed_plan(tmp_path: Path, *, plan_id: str = "engp1-e2e") -> Path:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    now = datetime(2026, 8, 3, tzinfo=timezone.utc).isoformat()
    actions = ("measurement", "investigation", "synthesis", "code_analysis", "implementation")
    steps = []
    previous: tuple[str, ...] = ()
    for index, action in enumerate(actions):
        step_id = f"step-{index}"
        artifact = "" if action == "implementation" else _artifact(artifacts, plan_id, step_id, action)
        steps.append(EngineeringStep(
            step_id,
            action,
            "audio_output",
            action,
            "completed",
            previous,
            ("evidence",),
            ("core/voice.py",) if action in {"code_analysis", "implementation"} else (),
            ("verified",),
            "low",
            100 - index,
            1,
            "",
            artifact,
        ))
        previous = (step_id,)
    plan = EngineeringPlan(
        1,
        plan_id,
        "Ses sistemini gece boyunca incele ve güvenli biçimde düzelt",
        "voice",
        "completed",
        now,
        now,
        "root_cause_identified",
        "invalid_sample_rate",
        EngineeringBudget(12, 3, 2, 2),
        tuple(steps),
    )
    path = tmp_path / "plan.json"
    EngineeringPlanStore(path).save(plan)
    return path


def test_validates_full_long_task_chain_and_builds_closeout(tmp_path: Path) -> None:
    plan = _completed_plan(tmp_path)
    audit = tmp_path / "audit.jsonl"
    row = _audit_row("0" * 64)
    audit.write_text(json.dumps(row) + "\n", encoding="utf-8")
    result = EngineeringLongTaskValidator(plan, delegated_audit_path=audit).validate()
    assert result.status == "verified"
    assert result.completed_steps == result.total_steps == 5
    assert result.artifact_count == 4
    assert result.push_performed is False
    assert Path(result.morning_report_path).is_file()
    assert Path(result.outcome_repository_path).is_file()
    payload = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
    assert payload["missing_action_types"] == []


def test_rejects_non_terminal_plan(tmp_path: Path) -> None:
    path = _completed_plan(tmp_path)
    plan = EngineeringPlanStore(path).load()
    pending = EngineeringStep(
        plan.steps[-1].step_id,
        plan.steps[-1].title,
        plan.steps[-1].subsystem,
        plan.steps[-1].action_type,
        "pending",
        plan.steps[-1].depends_on,
        plan.steps[-1].evidence_requirements,
        plan.steps[-1].affected_files,
        plan.steps[-1].success_criteria,
        plan.steps[-1].risk,
        plan.steps[-1].priority,
    )
    EngineeringPlanStore(path).save(EngineeringPlan(
        plan.schema_version, plan.plan_id, plan.request, plan.domain, "ready",
        plan.created_at, plan.updated_at, plan.diagnostic_status, plan.root_cause,
        plan.budget, (*plan.steps[:-1], pending), plan.delegated_policy_path,
    ))
    with pytest.raises(ValueError, match="fully completed plan"):
        EngineeringLongTaskValidator(path).validate()


def test_rejects_incomplete_required_chain(tmp_path: Path) -> None:
    path = _completed_plan(tmp_path)
    plan = EngineeringPlanStore(path).load()
    EngineeringPlanStore(path).save(EngineeringPlan(
        plan.schema_version, plan.plan_id, plan.request, plan.domain, "completed",
        plan.created_at, plan.updated_at, plan.diagnostic_status, plan.root_cause,
        plan.budget, tuple(step for step in plan.steps if step.action_type != "synthesis"),
        plan.delegated_policy_path,
    ))
    with pytest.raises(ValueError, match="chain is incomplete"):
        EngineeringLongTaskValidator(path).validate()


def test_rejects_tampered_completed_step_artifact(tmp_path: Path) -> None:
    path = _completed_plan(tmp_path)
    plan = EngineeringPlanStore(path).load()
    artifact = Path(plan.steps[0].artifact_path)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["step_id"] = "wrong"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="step mismatch"):
        EngineeringLongTaskValidator(path).validate()


def test_rejects_tampered_audit_or_unattended_push(tmp_path: Path) -> None:
    path = _completed_plan(tmp_path)
    audit = tmp_path / "audit.jsonl"
    tampered = _audit_row("0" * 64)
    tampered["record_hash"] = "f" * 64
    audit.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="audit integrity"):
        EngineeringLongTaskValidator(path, delegated_audit_path=audit).validate()

    pushed = _audit_row("0" * 64, push=True)
    audit.write_text(json.dumps(pushed) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="push operation"):
        EngineeringLongTaskValidator(path, delegated_audit_path=audit).validate()
