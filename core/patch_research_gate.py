from __future__ import annotations

from dataclasses import dataclass

from artmach_assistant.core.code_research_contracts import CodeTarget
from artmach_assistant.core.code_research_session import CodeResearchSession


@dataclass(frozen=True, slots=True)
class PatchResearchGateResult:
    allowed: bool
    reason: str


def validate_patch_research_gate(session: CodeResearchSession) -> PatchResearchGateResult:
    if not session.target.resolved:
        return PatchResearchGateResult(False, "Patch blocked: source target is unresolved.")
    if not session.source_seen:
        return PatchResearchGateResult(False, "Patch blocked: real source has not been reviewed.")
    if not session.tests_seen:
        return PatchResearchGateResult(False, "Patch blocked: behavioral test contract has not been reviewed.")
    if not session.can_generate_patch:
        return PatchResearchGateResult(False, "Patch blocked: root-cause evidence is insufficient.")
    return PatchResearchGateResult(True, "Patch research gate passed.")


def require_patch_research_gate(session: CodeResearchSession) -> None:
    result = validate_patch_research_gate(session)
    if not result.allowed:
        raise RuntimeError(result.reason)
