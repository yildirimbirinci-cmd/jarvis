from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_SCHEMA_VERSION = 1
_MAX_PLAN_BYTES = 8 * 1024 * 1024
_TERMINAL_STEP_STATES = {"completed", "blocked", "failed", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
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


def _safe_strings(values: object, *, limit: int = 100) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    for value in values[:limit]:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        payload = method()
        if isinstance(payload, Mapping):
            return dict(payload)
    raise TypeError("engineering brain requires a diagnostic mapping or to_dict() object")


@dataclass(frozen=True, slots=True)
class EngineeringBudget:
    maximum_steps: int = 24
    maximum_implementation_steps: int = 6
    maximum_commits: int = 3
    maximum_changed_files_per_step: int = 3

    def __post_init__(self) -> None:
        if min(
            self.maximum_steps,
            self.maximum_implementation_steps,
            self.maximum_commits,
            self.maximum_changed_files_per_step,
        ) <= 0:
            raise ValueError("engineering budgets must be positive")

    @classmethod
    def from_delegated_policy(cls, path: str | Path | None) -> "EngineeringBudget":
        if path is None:
            return cls()
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.stat().st_size > _MAX_PLAN_BYTES:
            raise ValueError("delegated policy is missing or oversized")
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("delegated policy must be an object")
        maximum_commits = max(1, int(payload.get("maximum_commits", 3)))
        maximum_files = max(1, int(payload.get("maximum_changed_files", 3)))
        return cls(
            maximum_steps=max(8, maximum_commits * 6),
            maximum_implementation_steps=maximum_commits,
            maximum_commits=maximum_commits,
            maximum_changed_files_per_step=maximum_files,
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EngineeringStep:
    step_id: str
    title: str
    subsystem: str
    action_type: str
    status: str
    depends_on: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    affected_files: tuple[str, ...]
    success_criteria: tuple[str, ...]
    risk: str
    priority: int
    attempt_count: int = 0
    last_error: str = ""
    artifact_path: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for name in ("depends_on", "evidence_requirements", "affected_files", "success_criteria"):
            payload[name] = list(payload[name])
        return payload


@dataclass(frozen=True, slots=True)
class EngineeringPlan:
    schema_version: int
    plan_id: str
    request: str
    domain: str
    status: str
    created_at: str
    updated_at: str
    diagnostic_status: str
    root_cause: str
    budget: EngineeringBudget
    steps: tuple[EngineeringStep, ...]
    delegated_policy_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "request": self.request,
            "domain": self.domain,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "diagnostic_status": self.diagnostic_status,
            "root_cause": self.root_cause,
            "budget": self.budget.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "delegated_policy_path": self.delegated_policy_path,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "EngineeringPlan":
        if int(payload.get("schema_version", 0)) != _SCHEMA_VERSION:
            raise ValueError("unsupported engineering plan schema")
        budget_raw = payload.get("budget")
        if not isinstance(budget_raw, Mapping):
            raise ValueError("engineering plan budget is invalid")
        budget = EngineeringBudget(
            int(budget_raw.get("maximum_steps", 24)),
            int(budget_raw.get("maximum_implementation_steps", 6)),
            int(budget_raw.get("maximum_commits", 3)),
            int(budget_raw.get("maximum_changed_files_per_step", 3)),
        )
        rows = payload.get("steps")
        if not isinstance(rows, list):
            raise ValueError("engineering plan steps must be a list")
        steps: list[EngineeringStep] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("engineering plan step is invalid")
            steps.append(EngineeringStep(
                str(row.get("step_id", "")),
                str(row.get("title", "")),
                str(row.get("subsystem", "")),
                str(row.get("action_type", "")),
                str(row.get("status", "pending")),
                _safe_strings(row.get("depends_on")),
                _safe_strings(row.get("evidence_requirements")),
                _safe_strings(row.get("affected_files")),
                _safe_strings(row.get("success_criteria")),
                str(row.get("risk", "medium")),
                int(row.get("priority", 50)),
                int(row.get("attempt_count", 0)),
                str(row.get("last_error", "")),
                str(row.get("artifact_path", "")),
            ))
        return cls(
            _SCHEMA_VERSION,
            str(payload.get("plan_id", "")),
            str(payload.get("request", "")),
            str(payload.get("domain", "unknown")),
            str(payload.get("status", "pending")),
            str(payload.get("created_at", "")),
            str(payload.get("updated_at", "")),
            str(payload.get("diagnostic_status", "")),
            str(payload.get("root_cause", "")),
            budget,
            tuple(steps),
            str(payload.get("delegated_policy_path", "")),
        )

    def ready_steps(self) -> tuple[EngineeringStep, ...]:
        completed = {step.step_id for step in self.steps if step.status == "completed"}
        return tuple(
            sorted(
                (
                    step for step in self.steps
                    if step.status == "pending" and set(step.depends_on).issubset(completed)
                ),
                key=lambda step: (-step.priority, step.step_id),
            )
        )


class EngineeringTaskDecomposer:
    """Convert one diagnostic request into a bounded dependency graph.

    The decomposer is intentionally deterministic. It does not invent root
    causes or implementation work when the diagnostic report says evidence is
    missing or hypotheses remain tied.
    """

    def __init__(self, *, budget: EngineeringBudget | None = None) -> None:
        self.budget = budget or EngineeringBudget()

    @staticmethod
    def _step_id(plan_seed: str, action: str, subsystem: str, index: int) -> str:
        digest = hashlib.sha256(f"{plan_seed}:{action}:{subsystem}:{index}".encode("utf-8")).hexdigest()[:12]
        return f"engs1-{digest}"

    def build(
        self,
        diagnostic_report: object,
        *,
        delegated_policy_path: str | Path | None = None,
        research_strategy: object | None = None,
        memory_context: object | None = None,
    ) -> EngineeringPlan:
        report = _mapping(diagnostic_report)
        request = str(report.get("request", "")).strip()
        domain = str(report.get("domain", "unknown")).strip().casefold() or "unknown"
        diagnostic_status = str(report.get("status", "unsupported")).strip().casefold()
        budget = (
            EngineeringBudget.from_delegated_policy(delegated_policy_path)
            if delegated_policy_path is not None else self.budget
        )
        seed = hashlib.sha256(f"{domain}:{request}".encode("utf-8")).hexdigest()[:20]
        plan_id = f"engp1-{seed}"
        now = _utc_now()
        subsystems = _safe_strings(report.get("subsystems")) or (domain,)
        findings = report.get("findings") if isinstance(report.get("findings"), list) else []
        investigation = report.get("investigation") if isinstance(report.get("investigation"), Mapping) else {}
        planner_task = report.get("planner_task") if isinstance(report.get("planner_task"), Mapping) else None
        affected_files = _safe_strings(planner_task.get("affected_files") if planner_task else ())
        test_plan = _safe_strings(planner_task.get("test_plan") if planner_task else ())
        steps: list[EngineeringStep] = []
        research_dependencies: list[str] = []
        if memory_context is not None:
            memory = _mapping(memory_context)
            success_matches = memory.get("success_matches") if isinstance(memory.get("success_matches"), list) else []
            failure_matches = memory.get("failure_matches") if isinstance(memory.get("failure_matches"), list) else []
            recommendation = str(memory.get("recommendation", "no_relevant_memory")).strip().casefold()
            if success_matches or failure_matches:
                memory_step_id = self._step_id(seed, "memory_review", domain, 0)
                research_dependencies.append(memory_step_id)
                steps.append(EngineeringStep(
                    memory_step_id,
                    "Geçmiş mühendislik deneyimlerini değerlendir",
                    domain,
                    "memory_review",
                    "pending",
                    (),
                    tuple(
                        [str(item.get("record_id", "")) for item in success_matches + failure_matches if isinstance(item, Mapping)]
                    ),
                    affected_files[: budget.maximum_changed_files_per_step],
                    (
                        "Başarılı örüntüler yeniden doğrulandı.",
                        "Başarısız örüntüler tekrar edilmemek üzere kaydedildi.",
                    ),
                    "low",
                    110 if recommendation == "reuse_verified_pattern" else 105,
                ))
        if research_strategy is not None:
            strategy = _mapping(research_strategy)
            actions = strategy.get("actions") if isinstance(strategy.get("actions"), list) else []
            for index, action in enumerate(actions):
                if not isinstance(action, Mapping) or len(steps) >= budget.maximum_steps:
                    continue
                kind = str(action.get("kind", "research")).strip().casefold() or "research"
                step_id = self._step_id(seed, f"research_{kind}", domain, index)
                research_dependencies.append(step_id)
                steps.append(EngineeringStep(
                    step_id,
                    str(action.get("title", f"Araştırma: {kind}")),
                    domain,
                    "research",
                    "pending",
                    (),
                    _safe_strings(action.get("inputs")),
                    (),
                    _safe_strings(action.get("success_criteria")) or ("Araştırma sonucu kaydedildi.",),
                    "low",
                    int(action.get("priority", 70)),
                ))

        if diagnostic_status in {"unsupported", "needs_evidence"}:
            for index, subsystem in enumerate(subsystems):
                if len(steps) >= budget.maximum_steps:
                    break
                steps.append(EngineeringStep(
                    self._step_id(seed, "measurement", subsystem, index),
                    f"{subsystem} için ölçüm ve log kanıtı topla",
                    subsystem,
                    "measurement",
                    "pending",
                    tuple(research_dependencies),
                    ("runtime log", "configuration snapshot", "reproduction result"),
                    (),
                    ("En az bir yeniden üretilebilir kanıt kaydedildi.",),
                    "low",
                    100 - index,
                ))
        elif diagnostic_status == "investigating" or planner_task is None:
            hypotheses = investigation.get("hypotheses") if isinstance(investigation, Mapping) else []
            hypothesis_steps: list[str] = []
            if isinstance(hypotheses, list):
                for index, hypothesis in enumerate(hypotheses[: max(1, budget.maximum_steps - 1)]):
                    if not isinstance(hypothesis, Mapping):
                        continue
                    subsystem = str(hypothesis.get("subsystem", domain))
                    cause = str(hypothesis.get("cause", "candidate root cause"))
                    step_id = self._step_id(seed, "investigation", subsystem, index)
                    hypothesis_steps.append(step_id)
                    steps.append(EngineeringStep(
                        step_id,
                        f"Hipotezi doğrula: {cause}",
                        subsystem,
                        "investigation",
                        "pending",
                        tuple(research_dependencies),
                        _safe_strings(hypothesis.get("evidence_ids")) or ("additional independent evidence",),
                        (),
                        ("Hipotez doğrulandı veya kanıtla elendi.",),
                        "low",
                        max(50, 100 - index * 5),
                    ))
            if not steps:
                for index, subsystem in enumerate(subsystems[: budget.maximum_steps - 1]):
                    step_id = self._step_id(seed, "investigation", subsystem, index)
                    hypothesis_steps.append(step_id)
                    steps.append(EngineeringStep(
                        step_id,
                        f"{subsystem} kök neden adaylarını incele",
                        subsystem,
                        "investigation",
                        "pending",
                        tuple(research_dependencies),
                        ("independent evidence",),
                        (),
                        ("Kök neden adayları puanlandı.",),
                        "low",
                        90 - index,
                    ))
            if len(steps) < budget.maximum_steps:
                steps.append(EngineeringStep(
                    self._step_id(seed, "synthesis", domain, len(steps)),
                    "Kanıtları birleştir ve kök nedeni yeniden değerlendir",
                    domain,
                    "synthesis",
                    "pending",
                    tuple(hypothesis_steps),
                    ("ranked hypotheses", "confidence margin"),
                    (),
                    ("Tek kök neden seçildi veya ek kanıt ihtiyacı açıklandı.",),
                    "low",
                    40,
                ))
        else:
            root_cause = ""
            if isinstance(investigation, Mapping):
                root = investigation.get("root_cause")
                if isinstance(root, Mapping):
                    root_cause = str(root.get("cause", ""))
            root_cause = root_cause or str(planner_task.get("title", "verified root cause"))
            analysis_id = self._step_id(seed, "code_analysis", domain, 0)
            steps.append(EngineeringStep(
                analysis_id,
                f"Kök neden kapsamını doğrula: {root_cause}",
                str(planner_task.get("diagnostic_subsystem", domain)),
                "code_analysis",
                "pending",
                tuple(research_dependencies),
                _safe_strings(planner_task.get("evidence_ids")) or ("diagnostic evidence",),
                affected_files[: budget.maximum_changed_files_per_step],
                ("Değişiklik sınırı ve gözlenebilir davranış belirlendi.",),
                "low",
                100,
            ))
            if budget.maximum_implementation_steps > 0:
                steps.append(EngineeringStep(
                    self._step_id(seed, "implementation", domain, 1),
                    str(planner_task.get("title", "Doğrulanmış iyileştirmeyi uygula")),
                    str(planner_task.get("diagnostic_subsystem", domain)),
                    "implementation",
                    "pending",
                    (analysis_id,),
                    _safe_strings(planner_task.get("evidence_ids")),
                    affected_files[: budget.maximum_changed_files_per_step],
                    test_plan or ("Focused tests pass.", "Full regression passes."),
                    str(planner_task.get("risk", "medium")),
                    90,
                ))

        steps = steps[: budget.maximum_steps]
        root_cause = ""
        if isinstance(investigation, Mapping) and isinstance(investigation.get("root_cause"), Mapping):
            root_cause = str(investigation["root_cause"].get("cause", ""))
        status = "ready" if steps else "blocked"
        return EngineeringPlan(
            _SCHEMA_VERSION,
            plan_id,
            request,
            domain,
            status,
            now,
            now,
            diagnostic_status,
            root_cause,
            budget,
            tuple(steps),
            str(Path(delegated_policy_path).expanduser().resolve()) if delegated_policy_path else "",
        )


class EngineeringPlanStore:
    """Crash-safe persistence and transition control for long engineering work."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)

    def save(self, plan: EngineeringPlan) -> Path:
        _atomic_write_json(self.path, plan.to_dict())
        return self.path

    def load(self) -> EngineeringPlan:
        if not self.path.is_file() or self.path.stat().st_size > _MAX_PLAN_BYTES:
            raise FileNotFoundError(self.path)
        payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, Mapping):
            raise ValueError("engineering plan must be an object")
        plan = EngineeringPlan.from_dict(payload)
        recovered: list[EngineeringStep] = []
        changed = False
        for step in plan.steps:
            if step.status == "running":
                recovered.append(replace(step, status="pending", last_error="recovered after interrupted engineering run"))
                changed = True
            else:
                recovered.append(step)
        if changed:
            plan = replace(plan, steps=tuple(recovered), updated_at=_utc_now(), status="ready")
            self.save(plan)
        return plan

    def update_step(
        self,
        step_id: str,
        status: str,
        *,
        error: str = "",
        artifact_path: str = "",
    ) -> EngineeringPlan:
        plan = self.load()
        normalized = str(status).strip().casefold()
        if normalized not in {"pending", "running", *_TERMINAL_STEP_STATES}:
            raise ValueError("invalid engineering step status")
        rows: list[EngineeringStep] = []
        found = False
        for step in plan.steps:
            if step.step_id != step_id:
                rows.append(step)
                continue
            found = True
            attempts = step.attempt_count + (1 if normalized == "running" else 0)
            rows.append(replace(
                step,
                status=normalized,
                attempt_count=attempts,
                last_error=str(error)[:2000],
                artifact_path=str(artifact_path),
            ))
        if not found:
            raise KeyError(f"unknown engineering step: {step_id}")
        states = {step.status for step in rows}
        if all(state == "completed" for state in states):
            plan_status = "completed"
        elif any(state == "failed" for state in states):
            plan_status = "failed"
        elif any(state == "running" for state in states):
            plan_status = "running"
        elif any(state == "pending" for state in states):
            plan_status = "ready"
        else:
            plan_status = "blocked"
        updated = replace(plan, steps=tuple(rows), status=plan_status, updated_at=_utc_now())
        self.save(updated)
        return updated


class EngineeringPlanSchedulerBridge:
    """Enqueue dependency-ready engineering work into the durable supervisor.

    Measurement, investigation, synthesis and code-analysis steps are routed
    through the ``engineering`` handler. Only implementation work enters the
    self-improvement ``cycle`` path. The older ``enqueue_ready_cycles`` API is
    retained for compatibility and deliberately filters to implementation.
    """

    _ENGINEERING_ACTIONS = {"memory_review", "research", "measurement", "investigation", "synthesis", "code_analysis"}

    @staticmethod
    def _payload(
        plan: EngineeringPlan,
        step: EngineeringStep,
        *,
        base_payload: Mapping[str, object],
        plan_path: str | Path,
    ) -> dict[str, object]:
        payload = dict(base_payload)
        payload.update({
            "engineering_plan_path": str(Path(plan_path).expanduser().resolve()),
            "engineering_step_id": step.step_id,
            "engineering_action_type": step.action_type,
            "domain": plan.domain,
            "request": plan.request,
            "step_title": step.title,
            "subsystem": step.subsystem,
            "evidence_requirements": list(step.evidence_requirements),
            "affected_files": list(step.affected_files),
            "success_criteria": list(step.success_criteria),
            "risk": step.risk,
            "delegated_policy_path": plan.delegated_policy_path,
        })
        return payload

    def enqueue_ready_work(
        self,
        plan: EngineeringPlan,
        scheduler: object,
        *,
        base_payload: Mapping[str, object],
        plan_path: str | Path,
    ) -> tuple[object, ...]:
        jobs: list[object] = []
        for step in plan.ready_steps():
            if step.action_type == "implementation":
                kind = "cycle"
                prefix = "engineering-cycle"
            elif step.action_type in self._ENGINEERING_ACTIONS:
                kind = "engineering"
                prefix = "engineering-step"
            else:
                continue
            jobs.append(scheduler.enqueue_unique(
                kind,
                self._payload(plan, step, base_payload=base_payload, plan_path=plan_path),
                dedupe_key=f"{prefix}:{plan.plan_id}:{step.step_id}",
            ))
        return tuple(jobs)

    def enqueue_ready_cycles(
        self,
        plan: EngineeringPlan,
        scheduler: object,
        *,
        base_payload: Mapping[str, object],
        plan_path: str | Path,
    ) -> tuple[object, ...]:
        jobs: list[object] = []
        for step in plan.ready_steps():
            if step.action_type != "implementation":
                continue
            jobs.append(scheduler.enqueue_unique(
                "cycle",
                self._payload(plan, step, base_payload=base_payload, plan_path=plan_path),
                dedupe_key=f"engineering-cycle:{plan.plan_id}:{step.step_id}",
            ))
        return tuple(jobs)
