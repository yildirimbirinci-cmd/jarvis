from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_research import (
    EXTERNAL_APPROVAL_REQUIRED,
    EvidenceResearchPlan,
    build_evidence_research_plan,
)
from artmach_assistant.core.evidence_research_session import (
    PENDING,
    EvidenceResearchApprovalSession,
    EvidenceResearchApprovalStore,
    render_pending_session,
)
from artmach_assistant.core.evidence_retest import RetestItem
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_BLOCKED,
    RETEST_FAILED,
    RETEST_PASSED,
    RetestExecutionResult,
)


NO_RESEARCH = "NO_RESEARCH"
RESEARCH_BLOCKED = "RESEARCH_BLOCKED"
RESEARCH_APPROVAL_PENDING = "RESEARCH_APPROVAL_PENDING"


@dataclass(frozen=True, slots=True)
class ResearchHandoffResult:
    status: str
    report: str
    plan: EvidenceResearchPlan
    approval_session: (
        EvidenceResearchApprovalSession | None
    ) = None


def _classification_for(item: RetestItem) -> str:
    title = str(item.title or "").casefold()

    if "hata" in title or "failure" in title:
        return "A"

    return "B"


def _finding_from_retest(
    item: RetestItem,
) -> EvidenceMaintenanceFinding:
    return EvidenceMaintenanceFinding(
        classification=_classification_for(item),
        score=90,
        source="runtime",
        title=item.title,
        path=item.path,
        symbol=item.symbol,
        evidence=(
            "Primary yeniden test sonucu ile "
            "arastirma karari verildi."
        ),
        repair_candidate=False,
        lifecycle="NEEDS_RETEST",
    )


def _render_no_research(
    plan: EvidenceResearchPlan,
) -> str:
    return (
        "ARASTIRMA KARARI\n"
        f"Durum: {plan.status}\n"
        f"Bulgu: {plan.title}\n"
        f"Konum: {plan.path}"
        + (
            f" - {plan.symbol}"
            if plan.symbol
            else ""
        )
        + "\n"
        f"Sonuc: {plan.reason}\n"
        "Internet arastirmasi baslatilmadi. "
        "Kaynak kodu degistirilmedi."
    )


class EvidenceResearchHandoff:
    def __init__(
        self,
        *,
        store: EvidenceResearchApprovalStore,
    ) -> None:
        self.store = store

    def _load_pending(
        self,
    ) -> EvidenceResearchApprovalSession | None:
        try:
            session = self.store.load()
        except Exception:
            try:
                self.store.clear()
            except OSError:
                pass
            return None

        if session is None or session.status != PENDING:
            return None

        return session

    def handle_retest_result(
        self,
        item: RetestItem,
        result: RetestExecutionResult,
    ) -> ResearchHandoffResult:
        finding = _finding_from_retest(item)
        plan = build_evidence_research_plan(
            finding,
            retest_result=result,
        )

        if result.status == RETEST_PASSED:
            return ResearchHandoffResult(
                status=NO_RESEARCH,
                report=_render_no_research(plan),
                plan=plan,
            )

        if result.status == RETEST_BLOCKED:
            return ResearchHandoffResult(
                status=RESEARCH_BLOCKED,
                report=_render_no_research(plan),
                plan=plan,
            )

        if (
            result.status != RETEST_FAILED
            or plan.status
            != EXTERNAL_APPROVAL_REQUIRED
        ):
            return ResearchHandoffResult(
                status=NO_RESEARCH,
                report=_render_no_research(plan),
                plan=plan,
            )

        created = EvidenceResearchApprovalSession.create(
            plan
        )
        existing = self._load_pending()

        if existing is not None:
            if existing.approval_id == created.approval_id:
                return ResearchHandoffResult(
                    status=RESEARCH_APPROVAL_PENDING,
                    report=render_pending_session(existing),
                    plan=plan,
                    approval_session=existing,
                )

            return ResearchHandoffResult(
                status=RESEARCH_APPROVAL_PENDING,
                report=(
                    "DIS ARASTIRMA ONAYI BEKLIYOR\n"
                    f"Mevcut onay kimligi: "
                    f"{existing.approval_id}\n"
                    f"Mevcut bulgu: {existing.title}\n"
                    "Yeni arastirma oturumu acilmadi. "
                    "Once mevcut oturumu onayla veya iptal et. "
                    "Internet arastirmasi baslatilmadi."
                ),
                plan=plan,
                approval_session=existing,
            )

        self.store.save(created)

        return ResearchHandoffResult(
            status=RESEARCH_APPROVAL_PENDING,
            report=render_pending_session(created),
            plan=plan,
            approval_session=created,
        )
