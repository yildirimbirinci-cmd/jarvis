from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Callable, Mapping

from .approval_gate import PromotionCommitApprovalGate
from .delegated_approval import DelegatedApprovalRuntime
from .self_improvement_runtime_cli import run_self_improvement_runtime
from .verified_experiment_promotion import VerifiedExperimentPromotionRuntime


def _as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    data = getattr(value, "to_dict", None)
    if callable(data):
        payload = data()
        if isinstance(payload, Mapping):
            return dict(payload)
    raw = getattr(value, "__dict__", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _required_path(payload: Mapping[str, object], name: str) -> Path:
    raw = payload.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"handler payload {name} is missing")
    return Path(raw).expanduser().resolve(strict=False)


def _latest_experiment_result(runtime_root: Path) -> Path:
    candidates = tuple(runtime_root.glob("experiments/*/experiment_result.json"))
    if not candidates:
        raise FileNotFoundError("completed cycle did not produce experiment_result.json")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


@dataclass(frozen=True, slots=True)
class HandlerResult:
    status: str
    message: str
    artifact_path: str = ""
    candidate_id: str = ""
    operation_id: str = ""
    receipt_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RuntimeHandlerRegistry:
    """Real handlers used by :class:`SelfImprovementSupervisor`.

    The registry owns runtime-only dependencies such as model configuration.
    Durable queue payloads therefore contain only JSON-safe paths and scalar
    options. Approval jobs deliberately stop before commit preparation; an
    owner-facing command can later create and approve the one-time token.
    """

    def __init__(
        self,
        *,
        model_config: object | None = None,
        changeset_timeout_seconds: float = 180.0,
        runtime_command: Callable[..., object] = run_self_improvement_runtime,
        promotion_factory: Callable[[str | Path], object] = VerifiedExperimentPromotionRuntime,
        approval_factory: Callable[[str | Path], object] = PromotionCommitApprovalGate,
        delegated_approval_factory: Callable[[str | Path], object] = DelegatedApprovalRuntime,
    ) -> None:
        if changeset_timeout_seconds <= 0:
            raise ValueError("changeset_timeout_seconds must be positive")
        self.model_config = model_config
        self.changeset_timeout_seconds = float(changeset_timeout_seconds)
        self.runtime_command = runtime_command
        self.promotion_factory = promotion_factory
        self.approval_factory = approval_factory
        self.delegated_approval_factory = delegated_approval_factory

    def cycle(self, payload: Mapping[str, object]) -> HandlerResult:
        project_root = _required_path(payload, "project_root")
        journal_path = _required_path(payload, "journal_path")
        runtime_root = _required_path(payload, "runtime_root")
        command = str(payload.get("command", "prepare")).strip().casefold() or "prepare"
        trigger_id = str(payload.get("trigger_id", "supervisor-cycle")).strip() or "supervisor-cycle"
        before = {
            path.resolve(): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in runtime_root.glob("experiments/*/experiment_result.json")
        }
        result = self.runtime_command(
            command,
            project_root=project_root,
            journal_path=journal_path,
            runtime_root=runtime_root,
            candidate_id=(str(payload["candidate_id"]) if payload.get("candidate_id") else None),
            trigger_id=trigger_id,
            model_config=self.model_config,
            changeset_timeout_seconds=self.changeset_timeout_seconds,
        )
        status = str(getattr(result, "status", "failed")).strip().casefold()
        if status != "completed":
            return HandlerResult(status, str(getattr(result, "output", status)))
        after = tuple(runtime_root.glob("experiments/*/experiment_result.json"))
        changed = [
            path for path in after
            if before.get(path.resolve()) != (path.stat().st_mtime_ns, path.stat().st_size)
        ]
        experiment_result = (
            max(changed, key=lambda path: path.stat().st_mtime_ns)
            if changed else _latest_experiment_result(runtime_root)
        )
        experiment = _as_mapping(__import__("json").loads(experiment_result.read_text(encoding="utf-8-sig")))
        return HandlerResult(
            "completed",
            "self-improvement cycle completed",
            str(experiment_result),
            str(experiment.get("candidate_id", "")),
        )

    def promotion(self, payload: Mapping[str, object]) -> HandlerResult:
        result_path = _required_path(payload, "experiment_result_path")
        runtime = self.promotion_factory(result_path)
        promoted = runtime.promote()
        data = _as_mapping(promoted)
        return HandlerResult(
            str(data.get("status", "failed")).strip().casefold(),
            str(data.get("message", "promotion finished")),
            str(data.get("result_path", "")),
            str(data.get("candidate_id", "")),
        )

    def approval(self, payload: Mapping[str, object]) -> HandlerResult:
        promotion_path = _required_path(payload, "promotion_result_path")
        delegated_policy = payload.get("delegated_policy_path")
        if isinstance(delegated_policy, str) and delegated_policy.strip():
            domain = str(payload.get("domain", "")).strip().casefold()
            if not domain:
                return HandlerResult(
                    "waiting_approval",
                    "delegated approval requires an explicit diagnostic domain",
                    str(promotion_path),
                    str(payload.get("candidate_id", "")),
                )
            runtime = self.delegated_approval_factory(delegated_policy)
            decision = runtime.execute(
                promotion_path,
                domain=domain,
                message=str(payload.get("commit_message", "Jarvis delegated overnight improvement")),
                diagnostic_report_path=(
                    str(payload["diagnostic_report_path"])
                    if payload.get("diagnostic_report_path")
                    else None
                ),
            )
            data = _as_mapping(decision)
            return HandlerResult(
                str(data.get("status", "waiting_owner")).strip().casefold(),
                str(data.get("message", "delegated approval finished")),
                str(promotion_path),
                str(payload.get("candidate_id", "")),
                str(data.get("operation_id", "")),
                str(data.get("audit_path", "")),
            )
        # Validate that the result is eligible and the repository still exists,
        # but do not create a token or commit without an explicit owner action.
        self.approval_factory(promotion_path)
        return HandlerResult(
            "waiting_approval",
            "verified promotion is waiting for explicit owner commit approval",
            str(promotion_path),
            str(payload.get("candidate_id", "")),
        )

    def handlers(self) -> dict[str, Callable[[Mapping[str, object]], object]]:
        return {
            "cycle": self.cycle,
            "promotion": self.promotion,
            "approval": self.approval,
        }
