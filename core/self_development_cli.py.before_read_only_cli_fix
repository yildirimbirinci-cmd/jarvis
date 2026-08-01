"""Headless entrypoint for Jarvis' guarded self-development loop.

The CLI deliberately separates planning, proposal generation and application.
No source file is changed unless the caller explicitly selects the ``apply``
stage.  The existing AssistantEngine safety checks, approval binding, tests and
rollback remain the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


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


def build_engine() -> SelfDevelopmentEngine:
    """Create the real engine without opening the desktop GUI."""
    from artmach_assistant.config import AppConfig
    from artmach_assistant.core.assistant import AssistantEngine
    from artmach_assistant.core.constitution import ConstitutionRegistry

    ConstitutionRegistry.initialize()
    return AssistantEngine(AppConfig.load())
