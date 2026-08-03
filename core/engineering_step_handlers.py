from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .engineering_brain import (
    EngineeringPlan,
    EngineeringPlanSchedulerBridge,
    EngineeringPlanStore,
    EngineeringStep,
)
from .research_orchestrator import ResearchOrchestrator
from .engineering_memory import EngineeringMemoryAdvisor
from .engineering_progress import EngineeringAdaptiveReplanner, EngineeringProgressTracker

_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


def _atomic_write_json(path: Path, payload: object) -> Path:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        raise ValueError("engineering step artifact is oversized")
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


def _path(payload: Mapping[str, object], name: str) -> Path:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"engineering payload {name} is missing")
    return Path(value).expanduser().resolve(strict=False)


def _json_mapping(path: Path) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size > _MAX_ARTIFACT_BYTES:
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"engineering JSON artifact must be an object: {path}")
    return payload


def _step(plan: EngineeringPlan, step_id: str) -> EngineeringStep:
    try:
        return next(item for item in plan.steps if item.step_id == step_id)
    except StopIteration as exc:
        raise KeyError(f"unknown engineering step: {step_id}") from exc


@dataclass(frozen=True, slots=True)
class EngineeringStepResult:
    status: str
    message: str
    artifact_path: str = ""
    plan_path: str = ""
    step_id: str = ""
    enqueued_jobs: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


StepCallback = Callable[[EngineeringPlan, EngineeringStep, Mapping[str, object]], Mapping[str, object]]


class EngineeringStepHandlerRegistry:
    """Execute non-mutating Engineering Brain steps and advance the plan.

    Callbacks may be replaced by domain-specific collectors. Default handlers
    remain evidence-bound: they consume only explicit diagnostic artifacts,
    runtime evidence and files listed in the plan. They never edit source code.
    """

    def __init__(
        self,
        scheduler: object,
        *,
        measurement_handler: StepCallback | None = None,
        investigation_handler: StepCallback | None = None,
        synthesis_handler: StepCallback | None = None,
        code_analysis_handler: StepCallback | None = None,
        research_handler: StepCallback | None = None,
        memory_review_handler: StepCallback | None = None,
        web_research_provider: Callable[[str, Sequence[str]], Mapping[str, object]] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.bridge = EngineeringPlanSchedulerBridge()
        self.web_research_provider = web_research_provider
        self.callbacks: dict[str, StepCallback] = {
            "measurement": measurement_handler or self._measurement,
            "investigation": investigation_handler or self._investigation,
            "synthesis": synthesis_handler or self._synthesis,
            "code_analysis": code_analysis_handler or self._code_analysis,
            "research": research_handler or self._research,
            "memory_review": memory_review_handler or self._memory_review,
        }


    @staticmethod
    def _memory_review(plan: EngineeringPlan, step: EngineeringStep, payload: Mapping[str, object]) -> Mapping[str, object]:
        snapshot = payload.get("engineering_memory")
        if not isinstance(snapshot, Mapping):
            paths = payload.get("knowledge_repository_paths")
            repositories = [str(item) for item in paths if str(item).strip()] if isinstance(paths, list) else []
            if not repositories:
                return {"status": "completed", "message": "no engineering memory repository configured", "recommendation": "no_relevant_memory"}
            snapshot = EngineeringMemoryAdvisor().inspect(
                plan.request,
                repository_paths=repositories,
                affected_files=step.affected_files,
            ).to_dict()
        recommendation = str(snapshot.get("recommendation", "no_relevant_memory"))
        return {
            "status": "completed",
            "message": "engineering memory reviewed",
            "recommendation": recommendation,
            "confidence_score": int(snapshot.get("confidence_score", 0)),
            "rationale": str(snapshot.get("rationale", "")),
            "success_matches": list(snapshot.get("success_matches", [])),
            "failure_matches": list(snapshot.get("failure_matches", [])),
        }

    def _research(self, plan: EngineeringPlan, step: EngineeringStep, payload: Mapping[str, object]) -> Mapping[str, object]:
        strategy_payload = payload.get("research_strategy")
        strategy = dict(strategy_payload) if isinstance(strategy_payload, Mapping) else {}
        actions = strategy.get("actions") if isinstance(strategy.get("actions"), list) else []
        action = next((dict(item) for item in actions if isinstance(item, Mapping) and str(item.get("title", "")) == step.title), None)
        if action is None:
            # Reconstruct a minimal decision when the strategy is not serialized in the queue payload.
            kind = "runtime_logs" if "log" in step.title.casefold() else "source_code"
            action = {"kind": kind, "inputs": list(step.evidence_requirements), "permission_required": False}
        kind = str(action.get("kind", "source_code")).strip().casefold()
        inputs = [str(item).strip() for item in action.get("inputs", []) if str(item).strip()] if isinstance(action.get("inputs"), list) else []

        if kind == "runtime_logs":
            log_paths = payload.get("log_paths")
            rows = [str(item) for item in log_paths if str(item).strip()] if isinstance(log_paths, list) else []
            runtime = payload.get("runtime_evidence")
            evidence = [dict(item) for item in runtime if isinstance(item, Mapping)] if isinstance(runtime, list) else []
            if not rows and not evidence:
                return {"status": "blocked", "message": "runtime log research requires log_paths or runtime_evidence"}
            return {"status": "completed", "message": "runtime evidence research completed", "kind": kind, "log_paths": rows, "evidence": evidence}

        if kind == "source_code":
            project_root = _path(payload, "project_root")
            files: list[dict[str, object]] = []
            for raw in inputs:
                relative = Path(raw.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                candidate = (project_root / relative).resolve(strict=False)
                if candidate == project_root or project_root not in candidate.parents or not candidate.is_file():
                    continue
                text = candidate.read_text(encoding="utf-8", errors="replace")
                files.append({"relative_path": relative.as_posix(), "size_bytes": candidate.stat().st_size, "line_count": text.count("\n") + 1})
            if inputs and not files:
                return {"status": "blocked", "message": "source research could not verify any bounded input file"}
            return {"status": "completed", "message": "source research completed", "kind": kind, "files": files}

        if kind == "knowledge_history":
            matches: list[dict[str, object]] = []
            terms = {plan.domain.casefold(), *(token for token in plan.request.casefold().split() if len(token) > 3)}
            for raw in inputs:
                path = Path(raw).expanduser().resolve(strict=False)
                if not path.is_file() or path.stat().st_size > _MAX_ARTIFACT_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                score = sum(1 for term in terms if term and term in text.casefold())
                if score:
                    matches.append({"path": str(path), "match_score": score})
            return {"status": "completed", "message": "knowledge history research completed", "kind": kind, "matches": matches}

        if kind == "web_research":
            if bool(action.get("permission_required", False)) or not bool(payload.get("allow_web_research", False)):
                return {"status": "blocked", "message": "web research requires explicit user permission", "permission_required": True}
            if self.web_research_provider is None:
                return {"status": "blocked", "message": "web research provider is not configured"}
            result = dict(self.web_research_provider(plan.request, inputs))
            if not result.get("sources"):
                return {"status": "blocked", "message": "web research returned no citable primary sources"}
            return {"status": "completed", "message": "web research completed", "kind": kind, **result}

        return {"status": "blocked", "message": f"unsupported research action: {kind}"}

    @staticmethod
    def _measurement(plan: EngineeringPlan, step: EngineeringStep, payload: Mapping[str, object]) -> Mapping[str, object]:
        rows = payload.get("runtime_evidence")
        evidence = [dict(item) for item in rows if isinstance(item, Mapping)] if isinstance(rows, list) else []
        relevant = [
            item for item in evidence
            if str(item.get("subsystem", step.subsystem)).strip() in {"", step.subsystem}
        ]
        log_paths = payload.get("log_paths")
        logs = [str(value) for value in log_paths if str(value).strip()] if isinstance(log_paths, list) else []
        status = "completed" if relevant or logs else "blocked"
        return {
            "status": status,
            "action_type": step.action_type,
            "subsystem": step.subsystem,
            "evidence": relevant,
            "log_paths": logs,
            "message": (
                f"{len(relevant)} runtime evidence and {len(logs)} log source recorded"
                if status == "completed" else
                "measurement requires runtime evidence or a log source"
            ),
        }

    @staticmethod
    def _investigation(plan: EngineeringPlan, step: EngineeringStep, payload: Mapping[str, object]) -> Mapping[str, object]:
        report_path = payload.get("diagnostic_report_path")
        if not isinstance(report_path, str) or not report_path.strip():
            return {"status": "blocked", "message": "investigation requires diagnostic_report_path"}
        report = _json_mapping(Path(report_path).expanduser().resolve(strict=False))
        investigation = report.get("investigation")
        hypotheses = investigation.get("hypotheses") if isinstance(investigation, Mapping) else []
        candidates = [
            dict(item) for item in hypotheses
            if isinstance(item, Mapping) and str(item.get("subsystem", "")) == step.subsystem
        ] if isinstance(hypotheses, list) else []
        if not candidates:
            return {"status": "blocked", "message": f"no hypothesis found for subsystem {step.subsystem}"}
        candidate = max(candidates, key=lambda item: int(item.get("rank_score", 0)))
        return {
            "status": "completed",
            "message": "hypothesis evidence captured",
            "selected_hypothesis": candidate,
            "diagnostic_report_path": str(Path(report_path).expanduser().resolve(strict=False)),
        }

    @staticmethod
    def _synthesis(plan: EngineeringPlan, step: EngineeringStep, payload: Mapping[str, object]) -> Mapping[str, object]:
        report_path = payload.get("diagnostic_report_path")
        if not isinstance(report_path, str) or not report_path.strip():
            return {"status": "blocked", "message": "synthesis requires diagnostic_report_path"}
        report = _json_mapping(Path(report_path).expanduser().resolve(strict=False))
        investigation = report.get("investigation")
        root = investigation.get("root_cause") if isinstance(investigation, Mapping) else None
        planner_task = report.get("planner_task")
        if not isinstance(root, Mapping) or not isinstance(planner_task, Mapping):
            return {
                "status": "blocked",
                "message": "root cause is not yet sufficiently separated from alternatives",
                "additional_evidence_required": True,
            }
        return {
            "status": "completed",
            "message": "root cause synthesis completed",
            "root_cause": dict(root),
            "planner_task": dict(planner_task),
        }

    @staticmethod
    def _code_analysis(plan: EngineeringPlan, step: EngineeringStep, payload: Mapping[str, object]) -> Mapping[str, object]:
        project_root = _path(payload, "project_root")
        verified: list[dict[str, object]] = []
        for relative in step.affected_files:
            candidate = Path(relative.replace("\\", "/"))
            if candidate.is_absolute() or ".." in candidate.parts:
                return {"status": "blocked", "message": f"unsafe affected file: {relative}"}
            resolved = (project_root / candidate).resolve(strict=False)
            if resolved != project_root and project_root not in resolved.parents:
                return {"status": "blocked", "message": f"affected file escapes project: {relative}"}
            if not resolved.is_file():
                return {"status": "blocked", "message": f"affected file is missing: {relative}"}
            verified.append({"relative_path": candidate.as_posix(), "size_bytes": resolved.stat().st_size})
        if not verified:
            return {"status": "blocked", "message": "code analysis has no bounded affected files"}
        return {
            "status": "completed",
            "message": "affected source scope verified",
            "files": verified,
            "success_criteria": list(step.success_criteria),
        }

    def execute(self, payload: Mapping[str, object]) -> EngineeringStepResult:
        plan_path = _path(payload, "engineering_plan_path")
        step_id = str(payload.get("engineering_step_id", "")).strip()
        if not step_id:
            raise ValueError("engineering_step_id is missing")
        store = EngineeringPlanStore(plan_path)
        plan = store.load()
        step = _step(plan, step_id)
        if step.status == "completed":
            return EngineeringStepResult("completed", "engineering step was already completed", step.artifact_path, str(plan_path), step_id, 0)
        if step.status != "pending":
            return EngineeringStepResult("blocked", f"engineering step is not pending: {step.status}", step.artifact_path, str(plan_path), step_id, 0)
        callback = self.callbacks.get(step.action_type)
        if callback is None:
            store.update_step(step_id, "blocked", error=f"no handler for action type {step.action_type}")
            return EngineeringStepResult("blocked", f"no handler for action type {step.action_type}", "", str(plan_path), step_id, 0)

        store.update_step(step_id, "running")
        artifact_root = Path(str(payload.get("engineering_artifact_root", plan_path.parent / "artifacts"))).expanduser().resolve(strict=False)
        artifact_path = artifact_root / plan.plan_id / f"{step.step_id}.json"
        try:
            result = dict(callback(plan, step, payload))
            status = str(result.get("status", "failed")).strip().casefold()
            if status not in {"completed", "blocked", "failed"}:
                raise ValueError(f"invalid engineering callback status: {status}")
            result.update({
                "schema_version": 1,
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "action_type": step.action_type,
            })
            _atomic_write_json(artifact_path, result)
            updated = store.update_step(
                step_id,
                status,
                error="" if status == "completed" else str(result.get("message", status)),
                artifact_path=str(artifact_path),
            )
            progress_path = Path(str(payload.get(
                "engineering_progress_path",
                plan_path.with_name(f"{plan.plan_id}.progress.json"),
            ))).expanduser().resolve(strict=False)
            EngineeringProgressTracker(progress_path).observe(updated)
            if status == "blocked" and bool(payload.get("adaptive_replanning", False)):
                updated = EngineeringAdaptiveReplanner().replan(
                    store,
                    step_id,
                    reason=str(result.get("message", "blocked")),
                )
                EngineeringProgressTracker(progress_path).observe(updated)
            jobs: tuple[object, ...] = ()
            if status == "completed" or (status == "blocked" and bool(payload.get("adaptive_replanning", False))):
                jobs = self.bridge.enqueue_ready_work(
                    updated,
                    self.scheduler,
                    base_payload={key: value for key, value in payload.items() if key not in {
                        "engineering_step_id", "engineering_action_type", "step_title", "subsystem",
                        "evidence_requirements", "affected_files", "success_criteria", "risk", "dedupe_key",
                    }},
                    plan_path=plan_path,
                )
            return EngineeringStepResult(status, str(result.get("message", status)), str(artifact_path), str(plan_path), step_id, len(jobs))
        except Exception as exc:
            store.update_step(step_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise
