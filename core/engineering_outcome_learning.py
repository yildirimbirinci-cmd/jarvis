from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Mapping

from .engineering_brain import EngineeringPlan, EngineeringPlanStore

_SCHEMA_VERSION = 1
_MAX_BYTES = 8 * 1024 * 1024
_TERMINAL = {"completed", "blocked", "failed", "cancelled"}


def _atomic_write(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise ValueError("engineering outcome repository is oversized")
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


@dataclass(frozen=True, slots=True)
class EngineeringActionOutcome:
    action_type: str
    completed: int
    blocked: int
    failed: int
    total: int
    success_rate: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EngineeringOutcomeRecord:
    schema_version: int
    plan_id: str
    domain: str
    request: str
    root_cause: str
    plan_status: str
    completed_steps: int
    blocked_steps: int
    failed_steps: int
    action_outcomes: tuple[EngineeringActionOutcome, ...]
    successful_implementation_files: tuple[str, ...]
    failed_patterns: tuple[str, ...]
    recommendation: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["action_outcomes"] = [item.to_dict() for item in self.action_outcomes]
        payload["successful_implementation_files"] = list(self.successful_implementation_files)
        payload["failed_patterns"] = list(self.failed_patterns)
        return payload


@dataclass(frozen=True, slots=True)
class EngineeringOutcomeProfile:
    domain: str
    sample_count: int
    action_success_rates: Mapping[str, int]
    repeatedly_failed_actions: tuple[str, ...]
    verified_implementation_files: tuple[str, ...]
    recommendation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "sample_count": self.sample_count,
            "action_success_rates": dict(self.action_success_rates),
            "repeatedly_failed_actions": list(self.repeatedly_failed_actions),
            "verified_implementation_files": list(self.verified_implementation_files),
            "recommendation": self.recommendation,
        }


class EngineeringOutcomeLearner:
    """Learn bounded planning preferences from terminal engineering plans."""

    def summarize(self, plan: EngineeringPlan) -> EngineeringOutcomeRecord:
        if any(step.status not in _TERMINAL for step in plan.steps):
            raise ValueError("engineering outcome learning requires a terminal plan")
        action_rows: list[EngineeringActionOutcome] = []
        action_types = sorted({step.action_type for step in plan.steps})
        for action_type in action_types:
            rows = [step for step in plan.steps if step.action_type == action_type]
            completed = sum(step.status == "completed" for step in rows)
            blocked = sum(step.status == "blocked" for step in rows)
            failed = sum(step.status == "failed" for step in rows)
            total = len(rows)
            action_rows.append(EngineeringActionOutcome(
                action_type,
                completed,
                blocked,
                failed,
                total,
                round(100 * completed / total) if total else 0,
            ))
        successful_files = sorted({
            relative
            for step in plan.steps
            if step.action_type == "implementation" and step.status == "completed"
            for relative in step.affected_files
        })
        failed_patterns = sorted({
            f"{step.action_type}:{step.last_error.strip()}"
            for step in plan.steps
            if step.status in {"blocked", "failed"} and step.last_error.strip()
        })
        completed = sum(step.status == "completed" for step in plan.steps)
        blocked = sum(step.status == "blocked" for step in plan.steps)
        failed = sum(step.status == "failed" for step in plan.steps)
        if failed:
            recommendation = "avoid_repeating_failed_actions"
        elif blocked:
            recommendation = "collect_more_evidence_before_reuse"
        elif any(step.action_type == "implementation" for step in plan.steps):
            recommendation = "reuse_verified_sequence"
        else:
            recommendation = "reuse_research_sequence"
        return EngineeringOutcomeRecord(
            _SCHEMA_VERSION,
            plan.plan_id,
            plan.domain,
            plan.request,
            plan.root_cause,
            plan.status,
            completed,
            blocked,
            failed,
            tuple(action_rows),
            tuple(successful_files),
            tuple(failed_patterns),
            recommendation,
        )

    def record(self, plan: EngineeringPlan, repository_path: str | Path) -> EngineeringOutcomeRecord:
        record = self.summarize(plan)
        path = Path(repository_path).expanduser().resolve(strict=False)
        if path.is_file():
            if path.stat().st_size > _MAX_BYTES:
                raise ValueError("engineering outcome repository is oversized")
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != _SCHEMA_VERSION:
                raise ValueError("engineering outcome repository is invalid")
        else:
            payload = {"schema_version": _SCHEMA_VERSION, "records": []}
        records = payload.get("records")
        if not isinstance(records, list):
            raise ValueError("engineering outcome repository records are invalid")
        by_plan = {
            str(item.get("plan_id", "")): item
            for item in records
            if isinstance(item, Mapping)
        }
        by_plan[record.plan_id] = record.to_dict()
        payload["records"] = [by_plan[key] for key in sorted(by_plan)]
        _atomic_write(path, payload)
        return record

    def profile(self, repository_path: str | Path, *, domain: str) -> EngineeringOutcomeProfile:
        path = Path(repository_path).expanduser().resolve(strict=False)
        if not path.is_file():
            return EngineeringOutcomeProfile(domain, 0, {}, (), (), "no_outcome_history")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        records = payload.get("records") if isinstance(payload, Mapping) else None
        rows = [item for item in records if isinstance(item, Mapping) and str(item.get("domain", "")) == domain] if isinstance(records, list) else []
        totals: dict[str, list[int]] = {}
        failures: dict[str, int] = {}
        verified_files: set[str] = set()
        for row in rows:
            outcomes = row.get("action_outcomes")
            if isinstance(outcomes, list):
                for item in outcomes:
                    if not isinstance(item, Mapping):
                        continue
                    action = str(item.get("action_type", ""))
                    if not action:
                        continue
                    totals.setdefault(action, [0, 0])
                    totals[action][0] += int(item.get("completed", 0))
                    totals[action][1] += int(item.get("total", 0))
                    failures[action] = failures.get(action, 0) + int(item.get("failed", 0)) + int(item.get("blocked", 0))
            files = row.get("successful_implementation_files")
            if isinstance(files, list):
                verified_files.update(str(value) for value in files if str(value).strip())
        rates = {
            action: round(100 * values[0] / values[1]) if values[1] else 0
            for action, values in sorted(totals.items())
        }
        repeated = tuple(sorted(action for action, count in failures.items() if count >= 2))
        if repeated:
            recommendation = "reorder_away_from_repeated_failures"
        elif rows:
            recommendation = "prefer_high_success_actions"
        else:
            recommendation = "no_outcome_history"
        return EngineeringOutcomeProfile(domain, len(rows), rates, repeated, tuple(sorted(verified_files)), recommendation)


class EngineeringOutcomePlanAdapter:
    """Adjust only pending-step priority; never invent or auto-approve work."""

    def adapt(self, plan: EngineeringPlan, profile: EngineeringOutcomeProfile) -> EngineeringPlan:
        if profile.domain != plan.domain or profile.sample_count == 0:
            return plan
        adapted = []
        for step in plan.steps:
            if step.status != "pending":
                adapted.append(step)
                continue
            rate = int(profile.action_success_rates.get(step.action_type, 50))
            delta = max(-20, min(20, (rate - 50) // 2))
            if step.action_type in profile.repeatedly_failed_actions:
                delta = min(delta, -20)
            if (
                step.action_type == "implementation"
                and step.affected_files
                and set(step.affected_files).issubset(profile.verified_implementation_files)
            ):
                delta = max(delta, 10)
            adapted.append(replace(step, priority=max(0, min(120, step.priority + delta))))
        return replace(plan, steps=tuple(adapted))

    def adapt_store(
        self,
        store: EngineeringPlanStore,
        profile: EngineeringOutcomeProfile,
    ) -> EngineeringPlan:
        plan = store.load()
        adapted = self.adapt(plan, profile)
        store.save(adapted)
        return adapted
