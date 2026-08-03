from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .engineering_brain import EngineeringPlanStore
from .engineering_morning_report import EngineeringMorningReport
from .engineering_outcome_learning import EngineeringOutcomeLearner
from .engineering_progress import EngineeringProgressTracker

_SCHEMA_VERSION = 1
_MAX_BYTES = 8 * 1024 * 1024
_REQUIRED_CHAIN = (
    "measurement",
    "investigation",
    "synthesis",
    "code_analysis",
    "implementation",
)


def _atomic_write(path: Path, payload: object) -> Path:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise ValueError("engineering long-task validation report is oversized")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _read_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError(f"validation artifact is oversized: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"validation artifact must be an object: {path}")
    return payload


@dataclass(frozen=True, slots=True)
class EngineeringLongTaskValidationResult:
    schema_version: int
    status: str
    plan_id: str
    request: str
    domain: str
    completed_steps: int
    total_steps: int
    verified_action_types: tuple[str, ...]
    missing_action_types: tuple[str, ...]
    artifact_count: int
    morning_report_path: str
    morning_text_path: str
    outcome_repository_path: str
    audit_integrity_ok: bool
    push_performed: bool
    message: str
    result_path: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["verified_action_types"] = list(self.verified_action_types)
        payload["missing_action_types"] = list(self.missing_action_types)
        return payload


class EngineeringLongTaskValidator:
    """Close and verify one complete long-running engineering assignment.

    This validator is intentionally read-mostly. It derives progress, morning
    reporting, and outcome-learning records from an already executed plan. It
    does not approve, commit, push, or mutate project source files.
    """

    def __init__(
        self,
        engineering_plan_path: str | Path,
        *,
        delegated_audit_path: str | Path | None = None,
        output_root: str | Path | None = None,
        required_action_types: tuple[str, ...] = _REQUIRED_CHAIN,
    ) -> None:
        self.plan_path = Path(engineering_plan_path).expanduser().resolve(strict=False)
        self.audit_path = (
            Path(delegated_audit_path).expanduser().resolve(strict=False)
            if delegated_audit_path is not None else None
        )
        self.output_root = (
            Path(output_root).expanduser().resolve(strict=False)
            if output_root is not None else self.plan_path.parent / "long_task_closeout"
        )
        self.required_action_types = tuple(dict.fromkeys(str(item).strip() for item in required_action_types if str(item).strip()))

    def _verify_artifacts(self, plan) -> tuple[int, tuple[str, ...]]:
        verified: list[str] = []
        count = 0
        for step in plan.steps:
            if step.status != "completed":
                continue
            if step.action_type == "implementation" and not step.artifact_path:
                # Implementation may be represented by the downstream
                # experiment/promotion/approval chain rather than a step JSON.
                continue
            if not step.artifact_path:
                raise ValueError(f"completed engineering step has no artifact: {step.step_id}")
            path = Path(step.artifact_path).expanduser().resolve(strict=False)
            payload = _read_object(path)
            if str(payload.get("plan_id", plan.plan_id)) != plan.plan_id:
                raise ValueError(f"engineering artifact plan mismatch: {path}")
            if str(payload.get("step_id", step.step_id)) != step.step_id:
                raise ValueError(f"engineering artifact step mismatch: {path}")
            count += 1
            verified.append(step.action_type)
        return count, tuple(sorted(set(verified)))

    def validate(self) -> EngineeringLongTaskValidationResult:
        store = EngineeringPlanStore(self.plan_path)
        plan = store.load()
        states = [step.status for step in plan.steps]
        if not plan.steps:
            raise ValueError("engineering long-task plan has no steps")
        if plan.status != "completed" or any(state != "completed" for state in states):
            raise ValueError("engineering long-task validation requires a fully completed plan")

        action_types = tuple(sorted({step.action_type for step in plan.steps}))
        missing = tuple(action for action in self.required_action_types if action not in action_types)
        if missing:
            raise ValueError("engineering long-task chain is incomplete: " + ", ".join(missing))

        artifact_count, verified_artifacts = self._verify_artifacts(plan)
        progress_path = self.output_root / f"{plan.plan_id}.progress.json"
        EngineeringProgressTracker(progress_path).observe(plan)
        morning_path = self.output_root / f"{plan.plan_id}.morning.json"
        morning_text = self.output_root / f"{plan.plan_id}.morning.txt"
        EngineeringMorningReport(
            self.plan_path,
            progress_path=progress_path,
            delegated_audit_path=self.audit_path,
        ).build(morning_path, text_output_path=morning_text)
        morning = _read_object(morning_path)
        summary = morning.get("summary")
        if not isinstance(summary, Mapping):
            raise ValueError("engineering morning report summary is missing")
        integrity = bool(summary.get("audit_integrity_ok", False))
        push = bool(summary.get("push_performed", False))
        if not integrity:
            raise ValueError("delegated approval audit integrity could not be verified")
        if push:
            raise ValueError("unattended long-task validation found a push operation")

        outcomes_path = self.output_root / "engineering_outcomes.json"
        EngineeringOutcomeLearner().record(plan, outcomes_path)
        result_path = self.output_root / f"{plan.plan_id}.validation.json"
        result = EngineeringLongTaskValidationResult(
            _SCHEMA_VERSION,
            "verified",
            plan.plan_id,
            plan.request,
            plan.domain,
            states.count("completed"),
            len(states),
            tuple(sorted(set((*action_types, *verified_artifacts)))),
            missing,
            artifact_count,
            str(morning_path),
            str(morning_text),
            str(outcomes_path),
            integrity,
            push,
            "long-running engineering task completed, reported, and learned without unattended push",
            str(result_path),
        )
        _atomic_write(result_path, result.to_dict())
        return result
