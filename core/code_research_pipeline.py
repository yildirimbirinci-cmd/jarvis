from __future__ import annotations

from artmach_assistant.core.code_research_contracts import (
    CodeEvidenceState,
    CodeResearchAction,
    CodeResearchDecision,
    CodeTarget,
)


def decide_code_research(
    target: CodeTarget,
    evidence: CodeEvidenceState,
) -> CodeResearchDecision:
    """Choose the next evidence step without issue-name or file-name heuristics."""
    if not target.resolved:
        return CodeResearchDecision(
            CodeResearchAction.BLOCKED,
            "A concrete source path is required before code research or patch planning.",
            target,
        )

    if not evidence.local_review_complete:
        return CodeResearchDecision(
            CodeResearchAction.LOCAL_REVIEW,
            "Read the real source and its behavioral test contract first.",
            target,
        )

    if evidence.enough_for_plan:
        return CodeResearchDecision(
            CodeResearchAction.READY_FOR_PLAN,
            "The evidence set is sufficient for root-cause planning.",
            target,
        )

    if evidence.external_research_requested:
        if evidence.external_evidence_seen:
            return CodeResearchDecision(
                CodeResearchAction.READY_FOR_PLAN,
                "External evidence is available and must be validated against local code.",
                target,
            )
        return CodeResearchDecision(
            CodeResearchAction.EXTERNAL_RESEARCH,
            "Local evidence is insufficient and external research is explicitly permitted.",
            target,
        )

    return CodeResearchDecision(
        CodeResearchAction.EXTERNAL_RESEARCH,
        "Local evidence is insufficient; external research is the next evidence source.",
        target,
    )


def patch_may_be_generated(
    target: CodeTarget,
    evidence: CodeEvidenceState,
) -> bool:
    """Patch generation is forbidden until target and root-cause evidence are resolved."""
    return target.resolved and evidence.enough_for_plan
