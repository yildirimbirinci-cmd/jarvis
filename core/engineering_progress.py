from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from .engineering_brain import EngineeringPlan, EngineeringPlanStore, EngineeringStep

_SCHEMA_VERSION = 1
_MAX_BYTES = 4 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _atomic_write(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        raise ValueError("engineering progress state is oversized")
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
class EngineeringProgressSnapshot:
    schema_version: int
    plan_id: str
    status: str
    progress_percent: int
    completed_steps: int
    total_steps: int
    pending_steps: int
    running_steps: int
    blocked_steps: int
    failed_steps: int
    implementation_steps_completed: int
    commit_budget_used: int
    commit_budget_total: int
    changed_file_budget_total: int
    stalled_step_ids: tuple[str, ...]
    recommendation: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["stalled_step_ids"] = list(self.stalled_step_ids)
        return payload


class EngineeringProgressTracker:
    """Persist progress and detect long-running or repeatedly blocked work."""

    def __init__(self, state_path: str | Path, *, stall_after_seconds: float = 1800.0) -> None:
        if stall_after_seconds <= 0:
            raise ValueError("stall_after_seconds must be positive")
        self.state_path = Path(state_path).expanduser().resolve(strict=False)
        self.stall_after = timedelta(seconds=float(stall_after_seconds))

    def _history(self) -> dict[str, object]:
        if not self.state_path.is_file():
            return {"schema_version": _SCHEMA_VERSION, "steps": {}}
        if self.state_path.stat().st_size > _MAX_BYTES:
            raise ValueError("engineering progress state is oversized")
        payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != _SCHEMA_VERSION:
            raise ValueError("engineering progress state is invalid")
        if not isinstance(payload.get("steps"), dict):
            payload["steps"] = {}
        return payload

    def observe(self, plan: EngineeringPlan, *, now: datetime | None = None) -> EngineeringProgressSnapshot:
        moment = now or _now()
        history = self._history()
        step_history = dict(history.get("steps", {}))
        stalled: list[str] = []
        for step in plan.steps:
            row = dict(step_history.get(step.step_id, {})) if isinstance(step_history.get(step.step_id), Mapping) else {}
            previous_status = str(row.get("status", ""))
            if previous_status != step.status:
                row["status_since"] = _iso(moment)
            row.update({"status": step.status, "attempt_count": step.attempt_count, "last_seen": _iso(moment)})
            step_history[step.step_id] = row
            since_raw = str(row.get("status_since", _iso(moment)))
            try:
                since = datetime.fromisoformat(since_raw)
            except ValueError:
                since = moment
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            if step.status == "running" and moment - since >= self.stall_after:
                stalled.append(step.step_id)

        states = [step.status for step in plan.steps]
        total = len(states)
        completed = states.count("completed")
        blocked = states.count("blocked")
        failed = states.count("failed")
        running = states.count("running")
        pending = states.count("pending")
        implementation_completed = sum(
            1 for step in plan.steps if step.action_type == "implementation" and step.status == "completed"
        )
        if total == 0:
            progress = 0
        else:
            terminal_weight = completed + 0.25 * (blocked + failed)
            progress = max(0, min(100, round(100 * terminal_weight / total)))
        if plan.status == "completed":
            recommendation = "complete"
        elif stalled:
            recommendation = "replan"
        elif failed:
            recommendation = "hold"
        elif blocked and not plan.ready_steps():
            recommendation = "replan"
        elif implementation_completed >= plan.budget.maximum_commits:
            recommendation = "hold"
        else:
            recommendation = "continue"
        snapshot = EngineeringProgressSnapshot(
            _SCHEMA_VERSION,
            plan.plan_id,
            plan.status,
            progress,
            completed,
            total,
            pending,
            running,
            blocked,
            failed,
            implementation_completed,
            implementation_completed,
            plan.budget.maximum_commits,
            plan.budget.maximum_changed_files_per_step,
            tuple(sorted(stalled)),
            recommendation,
            _iso(moment),
        )
        history.update({
            "schema_version": _SCHEMA_VERSION,
            "plan_id": plan.plan_id,
            "steps": step_history,
            "snapshot": snapshot.to_dict(),
        })
        _atomic_write(self.state_path, history)
        return snapshot


class EngineeringAdaptiveReplanner:
    """Add one bounded evidence-recovery step for a blocked diagnostic step."""

    _REPLANNABLE = {"research", "measurement", "investigation", "synthesis", "memory_review"}

    def replan(self, store: EngineeringPlanStore, step_id: str, *, reason: str) -> EngineeringPlan:
        plan = store.load()
        try:
            target = next(step for step in plan.steps if step.step_id == step_id)
        except StopIteration as exc:
            raise KeyError(f"unknown engineering step: {step_id}") from exc
        if target.action_type not in self._REPLANNABLE:
            return plan
        marker = f"recovery:{target.step_id}"
        if any(step.last_error == marker for step in plan.steps):
            return plan
        if len(plan.steps) >= plan.budget.maximum_steps:
            return plan
        digest = hashlib.sha256(f"{plan.plan_id}:{target.step_id}:recovery".encode("utf-8")).hexdigest()[:12]
        recovery_id = f"engs1-{digest}"
        recovery = EngineeringStep(
            recovery_id,
            f"Ek kanıt topla: {target.title}",
            target.subsystem,
            "measurement",
            "pending",
            tuple(dep for dep in target.depends_on if any(s.step_id == dep and s.status == "completed" for s in plan.steps)),
            tuple(target.evidence_requirements) or ("additional independent evidence",),
            (),
            ("Engellenen adımı yeniden değerlendirecek yeni kanıt kaydedildi.",),
            "low",
            min(120, target.priority + 10),
            0,
            marker,
            "",
        )
        rows = []
        for step in plan.steps:
            if step.step_id == target.step_id:
                rows.append(replace(
                    step,
                    status="pending",
                    depends_on=tuple(dict.fromkeys((*step.depends_on, recovery_id))),
                    last_error=str(reason)[:2000],
                ))
            else:
                rows.append(step)
        rows.append(recovery)
        updated = replace(plan, status="ready", updated_at=_iso(), steps=tuple(rows))
        store.save(updated)
        return updated
