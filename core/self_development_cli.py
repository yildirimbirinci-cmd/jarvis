"""Headless entrypoint for Jarvis' guarded self-development loop.

The CLI deliberately separates planning, proposal generation and application.
No source file is changed unless the caller explicitly selects the ``apply``
stage.  The existing AssistantEngine safety checks, approval binding, tests and
rollback remain the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from artmach_assistant.core.self_improvement_runtime_cli import (
    run_self_improvement_runtime,
)


class SelfDevelopmentEngine(Protocol):
    editor: object

    def prepare_own_code_plan(self, instruction: str) -> str: ...
    def _handle_own_code_plan_follow_up(self, text: str) -> str | None: ...
    def apply_pending_own_code_proposal(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SelfDevelopmentResult:
    stage: str
    exit_code: int
    output: str


def run_self_development(
    instruction: str,
    *,
    stage: str = "plan",
    engine_factory: Callable[[], SelfDevelopmentEngine],
) -> SelfDevelopmentResult:
    """Run one explicit stage of the guarded self-development workflow."""
    target = str(instruction or "").strip()
    if not target:
        return SelfDevelopmentResult("invalid", 2, "Geliştirme hedefi boş olamaz.")
    if stage not in {"plan", "propose", "apply"}:
        return SelfDevelopmentResult("invalid", 2, f"Bilinmeyen aşama: {stage}")

    engine = engine_factory()

    read_only_handler = getattr(engine, "_own_code_read_only_request", None)
    if callable(read_only_handler):
        read_only_output = read_only_handler(target)
        if read_only_output is not None:
            return SelfDevelopmentResult(
                "read_only",
                0,
                str(read_only_output),
            )

    plan_output = str(engine.prepare_own_code_plan(target))
    if stage == "plan":
        return SelfDevelopmentResult("plan", 0, plan_output)

    proposal_output = engine._handle_own_code_plan_follow_up("planı onayla")
    proposal_text = str(proposal_output or "Plan onayı işlenemedi.")
    pending = getattr(getattr(engine, "editor", None), "pending", None)
    if pending is None:
        return SelfDevelopmentResult(
            "proposal_failed",
            1,
            plan_output + "\n\n" + proposal_text,
        )
    if stage == "propose":
        return SelfDevelopmentResult(
            "proposal",
            0,
            plan_output + "\n\n" + proposal_text,
        )

    apply_output = str(engine.apply_pending_own_code_proposal())
    failed = any(
        marker in apply_output.casefold()
        for marker in (
            "geri alındı",
            "geri alindi",
            "başarısız",
            "basarisiz",
            "uygulanamadı",
            "uygulanamadi",
        )
    )
    return SelfDevelopmentResult(
        "apply_failed" if failed else "applied",
        1 if failed else 0,
        plan_output + "\n\n" + proposal_text + "\n\n" + apply_output,
    )



_LEGACY_STAGES = frozenset({"plan", "propose", "apply"})
_IMPROVEMENT_STAGE_MAP = {
    "improvement_status": "status",
    "improvement_run": "run",
    "improvement_prepare": "prepare",
    "improvement_complete": "complete",
}


def run_self_development_command(
    instruction: str = "",
    *,
    stage: str = "plan",
    engine_factory: Callable[[], SelfDevelopmentEngine] | None = None,
    project_root: str | Path | None = None,
    journal_path: str | Path | None = None,
    runtime_root: str | Path | None = None,
    candidate_id: str | None = None,
    experiment_result_paths: Sequence[str | Path] = (),
    trigger_id: str = "manual-self-improvement",
) -> SelfDevelopmentResult:
    """Dispatch legacy code editing and autonomous improvement commands.

    Existing ``plan``, ``propose`` and ``apply`` behavior remains unchanged.
    Autonomous stages use the separate guarded runtime and never invoke the
    legacy proposal/application path.
    """

    selected_stage = str(stage or "").strip().casefold()

    if selected_stage in _LEGACY_STAGES:
        factory = engine_factory or build_engine
        return run_self_development(
            instruction,
            stage=selected_stage,
            engine_factory=factory,
        )

    runtime_command = _IMPROVEMENT_STAGE_MAP.get(selected_stage)

    if runtime_command is None:
        return SelfDevelopmentResult(
            "invalid",
            2,
            (
                "Bilinmeyen a?ama: "
                f"{selected_stage or '<bo?>'}. "
                "Ge?erli a?amalar: plan, propose, apply, "
                "improvement_status, improvement_run, "
                "improvement_prepare, improvement_complete."
            ),
        )

    missing = [
        name
        for name, value in (
            ("project_root", project_root),
            ("journal_path", journal_path),
            ("runtime_root", runtime_root),
        )
        if value is None or not str(value).strip()
    ]

    if missing:
        return SelfDevelopmentResult(
            "invalid",
            2,
            (
                "Autonomous improvement i?in eksik yol: "
                + ", ".join(missing)
            ),
        )

    runtime_result = run_self_improvement_runtime(
        runtime_command,
        project_root=project_root,
        journal_path=journal_path,
        runtime_root=runtime_root,
        candidate_id=candidate_id,
        experiment_result_paths=experiment_result_paths,
        trigger_id=trigger_id,
    )

    return SelfDevelopmentResult(
        stage=f"improvement_{runtime_result.status}",
        exit_code=runtime_result.exit_code,
        output=runtime_result.output,
    )

def build_engine() -> SelfDevelopmentEngine:
    """Create the real engine without opening the desktop GUI."""
    from artmach_assistant.config import AppConfig
    from artmach_assistant.core.assistant import AssistantEngine
    from artmach_assistant.core.constitution import ConstitutionRegistry

    ConstitutionRegistry.initialize()
    return AssistantEngine(AppConfig.load())
