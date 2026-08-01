"""Constitution kontrollü, yürütme yetkisi olmayan geliştirme planı yöneticisi."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Iterator

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.constitution import ModuleConstitutionContext, PlanningPolicy

_MAX_PLAN_RECORD_BYTES = 1024 * 1024


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Geçersiz JSON sabiti: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"Yinelenen JSON anahtarı: {key}")
        payload[key] = value
    return payload


def _iter_bounded_plan_rows(path: Path) -> Iterator[bytes]:
    """JSONL satırlarını bellek taşmasına izin vermeden sırayla üretir."""
    with path.open("rb") as handle:
        while True:
            row = handle.readline(_MAX_PLAN_RECORD_BYTES + 1)
            if not row:
                return
            if len(row) > _MAX_PLAN_RECORD_BYTES:
                if not row.endswith(b"\n"):
                    while True:
                        remainder = handle.readline(_MAX_PLAN_RECORD_BYTES + 1)
                        if not remainder or remainder.endswith(b"\n"):
                            break
                continue
            stripped = row.strip()
            if stripped:
                yield stripped


def _load_plan_record(line: str | bytes) -> dict[str, Any]:
    if isinstance(line, bytes):
        if len(line) > _MAX_PLAN_RECORD_BYTES:
            raise ValueError("Plan kaydı boyut sınırını aşıyor.")
        line = line.decode("utf-8", errors="strict")
    elif len(line.encode("utf-8")) > _MAX_PLAN_RECORD_BYTES:
        raise ValueError("Plan kaydı boyut sınırını aşıyor.")
    payload = json.loads(
        line,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_unique_json_object,
    )
    if not isinstance(payload, dict):
        raise ValueError("Plan kaydı bir JSON nesnesi olmalıdır.")
    return payload


@dataclass
class PlanStep:
    step_id: str
    title: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    test_plan: list[str] = field(default_factory=list)
    state: str = "pending"


@dataclass
class DevelopmentPlan:
    plan_id: str
    created_at: str
    title: str
    problem: str
    root_cause: str
    solution: str
    risk: str
    rollback_plan: str
    steps: list[PlanStep]
    state: str = "draft"
    approved_at: str = ""


class PlanningManager:
    """Plan üretir ve doğrular; plan adımlarını kendisi çalıştırmaz."""

    def __init__(self, constitution: ModuleConstitutionContext) -> None:
        self.policy = PlanningPolicy(constitution)
        self.path = DATA_DIR / "development_plans.jsonl"
        self._lock = RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_plan_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"PLAN-{stamp}-{uuid.uuid4().hex[:8].upper()}"

    def create(
        self,
        *,
        title: str,
        problem: str,
        root_cause: str,
        solution: str,
        risk: str,
        rollback_plan: str = "",
        steps: Iterable[PlanStep],
    ) -> DevelopmentPlan:
        self.policy.require("plan.create")
        if not title.strip() or not problem.strip() or not root_cause.strip() or not solution.strip():
            raise ValueError("Plan başlığı, problem, kök neden ve çözüm alanları zorunludur.")
        self.policy.validate_risk(risk, rollback_plan)
        normalized_steps = list(steps)
        self._validate_steps(normalized_steps)
        plan = DevelopmentPlan(
            plan_id=self.new_plan_id(),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            title=title.strip(),
            problem=problem.strip(),
            root_cause=root_cause.strip(),
            solution=solution.strip(),
            risk=risk,
            rollback_plan=rollback_plan.strip(),
            steps=normalized_steps,
        )
        self._append(plan)
        return plan

    def approve(self, plan: DevelopmentPlan, *, approved: bool = False) -> DevelopmentPlan:
        self.policy.require("plan.approve", approved=approved)
        if plan.state not in {"draft", "ready", "awaiting_approval"}:
            raise ValueError(f"{plan.state} durumundaki plan onaylanamaz.")
        previous_state = plan.state
        previous_approved_at = plan.approved_at
        plan.state = "approved"
        plan.approved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            self._append(plan)
        except Exception:
            plan.state = previous_state
            plan.approved_at = previous_approved_at
            raise
        return plan

    def can_start(self, plan: DevelopmentPlan, *, approved: bool = False) -> bool:
        self.policy.require("plan.start_execution", approved=approved)
        if plan.state != "approved":
            raise ValueError("Yalnızca onaylanmış plan yürütmeye hazırlanabilir.")
        return True

    def next_ready_step(self, plan: DevelopmentPlan) -> PlanStep | None:
        completed = {step.step_id for step in plan.steps if step.state == "completed"}
        for step in plan.steps:
            if step.state in {"pending", "blocked", "ready"}:
                if set(step.dependencies).issubset(completed):
                    step.state = "ready"
                    return step
                step.state = "blocked"
        return None

    def list(self, limit: int = 100) -> list[DevelopmentPlan]:
        self.policy.require("plan.read")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("Plan listesi limiti bir tamsayı olmalıdır.")
        if limit <= 0 or not self.path.exists():
            return []
        plans: list[DevelopmentPlan] = []
        try:
            rows = _iter_bounded_plan_rows(self.path)
            for line in rows:
                try:
                    raw = _load_plan_record(line)
                    raw_steps = raw.get("steps")
                    if not isinstance(raw_steps, list):
                        raise ValueError("Plan adımları bir liste olmalıdır.")
                    steps = [PlanStep(**step) for step in raw_steps if isinstance(step, dict)]
                    if len(steps) != len(raw_steps):
                        raise ValueError("Her plan adımı bir JSON nesnesi olmalıdır.")
                    self._validate_steps(steps)
                    raw["steps"] = steps
                    plan = DevelopmentPlan(**raw)
                    for field_name in (
                        "plan_id", "created_at", "title", "problem", "root_cause",
                        "solution", "risk", "rollback_plan", "state", "approved_at",
                    ):
                        if not isinstance(getattr(plan, field_name), str):
                            raise ValueError(f"Plan alanı metin olmalıdır: {field_name}")
                    plans.append(plan)
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
        except OSError:
            return []
        return plans[-limit:][::-1]
    def _validate_steps(self, steps: list[PlanStep]) -> None:
        if not steps:
            raise ValueError("Plan en az bir adım içermelidir.")
        for step in steps:
            if not isinstance(step, PlanStep):
                raise TypeError("Plan adımları PlanStep nesnesi olmalıdır.")
            if not isinstance(step.step_id, str) or not step.step_id.strip():
                raise ValueError("Her plan adımının step_id alanı zorunludur.")
            if not isinstance(step.title, str) or not step.title.strip():
                raise ValueError("Her plan adımının başlığı zorunludur.")
            if not isinstance(step.description, str) or not step.description.strip():
                raise ValueError("Her plan adımının açıklaması zorunludur.")
            for field_name, values in (
                ("bağımlılıkları", step.dependencies),
                ("etkilenen modülleri", step.affected_modules),
                ("test planı", step.test_plan),
            ):
                if not isinstance(values, list) or any(
                    not isinstance(value, str) or not value.strip() for value in values
                ):
                    raise ValueError(f"Plan adımının {field_name} geçerli metinlerden oluşmalıdır.")
            if len(step.dependencies) != len(set(step.dependencies)):
                raise ValueError("Plan adımı yinelenen bağımlılık içeremez.")
        ids = [step.step_id for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Plan adımı kimlikleri benzersiz olmalıdır.")
        known: set[str] = set()
        all_ids = set(ids)
        for step in steps:
            unknown = set(step.dependencies).difference(all_ids)
            if unknown:
                raise ValueError(
                    f"{step.step_id} bilinmeyen bağımlılıklara sahip: {', '.join(sorted(unknown))}"
                )
            forward = set(step.dependencies).difference(known)
            if forward:
                raise ValueError(
                    f"{step.step_id} yalnızca kendisinden önceki adımlara bağlı olabilir: "
                    + ", ".join(sorted(forward))
                )
            known.add(step.step_id)

    def _append(self, plan: DevelopmentPlan) -> None:
        payload = asdict(plan)
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_PLAN_RECORD_BYTES:
            raise ValueError("Plan kaydı boyut sınırını aşıyor.")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                original_size = handle.tell()
                try:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                except Exception:
                    try:
                        handle.seek(original_size)
                        handle.truncate()
                        handle.flush()
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                    raise
