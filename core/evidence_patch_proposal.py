from __future__ import annotations

import hashlib
from dataclasses import dataclass

from artmach_assistant.core.evidence_conclusion import (
    CONFIDENCE_HIGH,
    EvidenceConclusion,
)
from artmach_assistant.core.evidence_engineering_plan import (
    PLAN_PATCH_PROPOSAL,
    EvidenceEngineeringPlan,
)
from artmach_assistant.core.code_research_contracts import CodeTarget
from artmach_assistant.core.code_research_session import (
    CodeResearchEvent,
    CodeResearchSession,
)
from artmach_assistant.core.patch_research_gate import validate_patch_research_gate


PROPOSAL_BLOCKED = "BLOCKED"
PROPOSAL_READY_FOR_REVIEW = "READY_FOR_REVIEW"


@dataclass(frozen=True, slots=True)
class EvidencePatchProposal:
    proposal_id: str
    status: str
    target_path: str
    target_symbol: str
    objective: str
    rationale: str
    change_scope: tuple[str, ...]
    validation_steps: tuple[str, ...]
    safety_constraints: tuple[str, ...]
    user_approval_required: bool
    apply_allowed: bool

    def report(self) -> str:
        rows = [
            "YAPILANDIRILMIS PATCH TASLAGI",
            f"Taslak kimligi: {self.proposal_id}",
            f"Durum: {self.status}",
            f"Hedef: {self.target_path}",
            f"Sembol: {self.target_symbol or '(belirtilmedi)'}",
            f"Amac: {self.objective}",
            f"Gerekce: {self.rationale}",
        ]
        if self.change_scope:
            rows.append("Degisiklik kapsami:\n- " + "\n- ".join(self.change_scope))
        if self.validation_steps:
            rows.append("Dogrulama adimlari:\n- " + "\n- ".join(self.validation_steps))
        if self.safety_constraints:
            rows.append("Guvenlik sinirlari:\n- " + "\n- ".join(self.safety_constraints))
        rows.extend(
            (
                "Kullanici onayi gerekli: "
                + ("evet" if self.user_approval_required else "hayir"),
                "Uygulama izni: " + ("evet" if self.apply_allowed else "hayir"),
                "Kaynak kodu degistirilmedi.",
            )
        )
        return "\n\n".join(rows)


def _proposal_id(path: str, symbol: str, objective: str) -> str:
    payload = "|".join((path, symbol, objective))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
    return f"PP-{digest.upper()}"


def build_evidence_patch_proposal(
    plan: EvidenceEngineeringPlan,
    conclusion: EvidenceConclusion,
    *,
    path: str,
    symbol: str,
) -> EvidencePatchProposal:
    target_path = str(path or "").strip()
    target_symbol = str(symbol or "").strip()
    ready = (
        plan.status == PLAN_PATCH_PROPOSAL
        and conclusion.confidence_level == CONFIDENCE_HIGH
        and bool(plan.patch_allowed)
        and bool(conclusion.patch_ready)
    )
    status = PROPOSAL_READY_FOR_REVIEW if ready else PROPOSAL_BLOCKED
    if ready:
        rationale = (
            "Kanit guveni yuksek ve muhendislik plani patch taslagi asamasinda. "
            "Taslak yalnizca inceleme icin hazirdir; otomatik uygulama kapali kalir."
        )
        scope = tuple(plan.steps)
    else:
        rationale = (
            "Muhendislik plani veya kanit guveni patch taslagi icin yeterli degil. "
            "Once plandaki yerel dogrulama ve kanit boslugu adimlari tamamlanmali."
        )
        scope = ()
    return EvidencePatchProposal(
        proposal_id=_proposal_id(target_path, target_symbol, plan.objective),
        status=status,
        target_path=target_path,
        target_symbol=target_symbol,
        objective=plan.objective,
        rationale=rationale,
        change_scope=scope,
        validation_steps=tuple(plan.acceptance_criteria),
        safety_constraints=tuple(plan.safety_constraints),
        user_approval_required=True,
        apply_allowed=False,
    )


def build_guarded_evidence_patch_proposal(
    plan: EvidenceEngineeringPlan,
    conclusion: EvidenceConclusion,
    *,
    path: str,
    symbol: str,
) -> EvidencePatchProposal:
    """Build a proposal only after the general code-research gate passes.

    PLAN_PATCH_PROPOSAL is treated as proof that the existing local validation
    stage completed; external conclusion evidence is still recorded separately.
    """
    session = CodeResearchSession(CodeTarget(str(path or "").strip(), str(symbol or "").strip()))
    if session.target.resolved:
        session.record(CodeResearchEvent.SOURCE_REVIEWED)
    if plan.status == PLAN_PATCH_PROPOSAL:
        session.record(CodeResearchEvent.TESTS_REVIEWED)
        session.record(CodeResearchEvent.LOCAL_ROOT_CAUSE)
    if conclusion.confidence_level == CONFIDENCE_HIGH:
        session.record(CodeResearchEvent.EXTERNAL_EVIDENCE)

    gate = validate_patch_research_gate(session)
    if not gate.allowed:
        proposal = build_evidence_patch_proposal(
            plan, conclusion, path=path, symbol=symbol
        )
        return EvidencePatchProposal(
            proposal_id=proposal.proposal_id,
            status=PROPOSAL_BLOCKED,
            target_path=proposal.target_path,
            target_symbol=proposal.target_symbol,
            objective=proposal.objective,
            rationale=gate.reason,
            change_scope=(),
            validation_steps=proposal.validation_steps,
            safety_constraints=proposal.safety_constraints,
            user_approval_required=True,
            apply_allowed=False,
        )

    return build_evidence_patch_proposal(
        plan, conclusion, path=path, symbol=symbol
    )
