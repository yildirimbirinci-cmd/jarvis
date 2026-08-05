from __future__ import annotations

from dataclasses import dataclass, replace

from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_research import (
    EXTERNAL_APPROVAL_REQUIRED,
    LOCAL_REVIEW,
    EvidenceResearchPlan,
    build_evidence_research_plan,
)
from artmach_assistant.core.evidence_research_session import (
    PENDING,
    EvidenceResearchApprovalSession,
    EvidenceResearchApprovalStore,
    render_pending_session,
)


LOCAL_PLAN_READY = "LOCAL_PLAN_READY"
LOCAL_EVIDENCE_SUFFICIENT = "LOCAL_EVIDENCE_SUFFICIENT"
EXTERNAL_APPROVAL_PENDING = "EXTERNAL_APPROVAL_PENDING"


@dataclass(frozen=True, slots=True)
class EvidenceResearchCoordinationResult:
    status: str
    report: str
    plan: EvidenceResearchPlan
    approval_session: EvidenceResearchApprovalSession | None = None


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()

    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = cleaned.casefold()

        if not cleaned or key in seen:
            continue

        seen.add(key)
        rows.append(cleaned)

    return tuple(rows[:4])


def _external_queries(
    finding: EvidenceMaintenanceFinding,
) -> tuple[str, ...]:
    title = str(finding.title or "").strip()
    symbol = str(finding.symbol or "").strip()

    lowered = f"{title} {symbol}".casefold()

    if (
        "yavas" in lowered
        or "slow" in lowered
        or "performance" in lowered
    ):
        return _unique(
            (
                "Python cProfile performance profiling official documentation",
                "Python task execution latency profiling official documentation",
                "Python decorator wrapper performance profiling",
                "Python concurrent task orchestration performance diagnostics",
            )
        )

    return _unique(
        (
            "Python runtime diagnostics official documentation",
            "Python exception debugging official documentation",
            "Python regression testing official documentation",
            "Python application reliability diagnostics",
        )
    )


def _promote_to_external(
    finding: EvidenceMaintenanceFinding,
    plan: EvidenceResearchPlan,
) -> EvidenceResearchPlan:
    return replace(
        plan,
        status=EXTERNAL_APPROVAL_REQUIRED,
        reason=(
            "Yerel inceleme plani hazirlandi ancak mevcut yapilandirilmis "
            "kanit kok nedeni dogrulamak icin yeterli degil. Resmi dis "
            "kaynaklar kullanici onayindan sonra incelenmeli."
        ),
        external_queries=_external_queries(finding),
        preferred_sources=(
            "Resmi Python dokumantasyonu",
            "Kullanilan kutuphanenin resmi dokumantasyonu",
            "Resmi GitHub deposu ve issue kayitlari",
            "Surum notlari ve degisiklik gunlukleri",
        ),
    )


class EvidenceResearchCoordinator:
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

    def coordinate(
        self,
        finding: EvidenceMaintenanceFinding,
        *,
        local_review_complete: bool = False,
        local_evidence_sufficient: bool = False,
    ) -> EvidenceResearchCoordinationResult:
        plan = build_evidence_research_plan(finding)

        if plan.status != LOCAL_REVIEW:
            return EvidenceResearchCoordinationResult(
                status=LOCAL_PLAN_READY,
                report=plan.report(),
                plan=plan,
            )

        if not local_review_complete:
            return EvidenceResearchCoordinationResult(
                status=LOCAL_PLAN_READY,
                report=plan.report(),
                plan=plan,
            )

        if local_evidence_sufficient:
            return EvidenceResearchCoordinationResult(
                status=LOCAL_EVIDENCE_SUFFICIENT,
                report=(
                    plan.report()
                    + "\n\nYerel kanit kok nedeni aciklamak icin yeterli. "
                    + "Dis arastirma onayi olusturulmadi."
                ),
                plan=plan,
            )

        external_plan = _promote_to_external(
            finding,
            plan,
        )
        created = EvidenceResearchApprovalSession.create(
            external_plan
        )
        existing = self._load_pending()

        if existing is not None:
            if existing.approval_id == created.approval_id:
                return EvidenceResearchCoordinationResult(
                    status=EXTERNAL_APPROVAL_PENDING,
                    report=render_pending_session(existing),
                    plan=external_plan,
                    approval_session=existing,
                )

            return EvidenceResearchCoordinationResult(
                status=EXTERNAL_APPROVAL_PENDING,
                report=(
                    "DIS ARASTIRMA ONAYI BEKLIYOR\n"
                    f"Mevcut onay kimligi: {existing.approval_id}\n"
                    f"Mevcut bulgu: {existing.title}\n"
                    "Yeni arastirma oturumu acilmadi. Once mevcut "
                    "oturumu onayla veya iptal et. Internet "
                    "arastirmasi baslatilmadi."
                ),
                plan=external_plan,
                approval_session=existing,
            )

        self.store.save(created)

        return EvidenceResearchCoordinationResult(
            status=EXTERNAL_APPROVAL_PENDING,
            report=render_pending_session(created),
            plan=external_plan,
            approval_session=created,
        )
