from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_BLOCKED,
    RETEST_FAILED,
    RETEST_PASSED,
    RetestExecutionResult,
)


NOT_NEEDED = "NOT_NEEDED"
LOCAL_REVIEW = "LOCAL_REVIEW"
EXTERNAL_APPROVAL_REQUIRED = "EXTERNAL_APPROVAL_REQUIRED"
BLOCKED = "BLOCKED"

_MAX_QUERY_COUNT = 4


@dataclass(frozen=True, slots=True)
class EvidenceResearchPlan:
    status: str
    title: str
    path: str
    symbol: str
    reason: str
    local_questions: tuple[str, ...] = ()
    external_queries: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()
    safety_constraints: tuple[str, ...] = ()

    @property
    def requires_external_approval(self) -> bool:
        return self.status == EXTERNAL_APPROVAL_REQUIRED

    def report(self) -> str:
        rows = [
            "KANITA DAYALI ARASTIRMA PLANI",
            f"Durum: {self.status}",
            f"Bulgu: {self.title}",
            (
                f"Konum: {self.path}"
                + (f" - {self.symbol}" if self.symbol else "")
            ),
            f"Neden: {self.reason}",
        ]
        if self.local_questions:
            rows.append(
                "Yerel inceleme:\n- "
                + "\n- ".join(self.local_questions)
            )

        if self.external_queries:
            rows.append(
                "Dis arastirma sorgulari:\n- "
                + "\n- ".join(self.external_queries)
            )
        if self.preferred_sources:
            rows.append(
                "Tercih edilen kaynaklar:\n- "
                + "\n- ".join(self.preferred_sources)
            )

        if self.safety_constraints:
            rows.append(
                "Guvenlik sinirlari:\n- "
                + "\n- ".join(self.safety_constraints)
            )
        if self.requires_external_approval:
            rows.append(
                "Internet arastirmasi henuz baslatilmadi. "
                "Acik kullanici izni gerekiyor."
            )

        return "\n\n".join(rows)


def _symbol_tail(symbol: str) -> str:
    return str(symbol or "").rsplit(".", 1)[-1].strip()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = cleaned.casefold()

        if not cleaned or key in seen:
            continue

        seen.add(key)
        rows.append(cleaned)

        if len(rows) >= _MAX_QUERY_COUNT:
            break

    return tuple(rows)


def _resolved_finding_location(
    finding: EvidenceMaintenanceFinding,
) -> EvidenceMaintenanceFinding:
    path = str(finding.path or "").strip()
    symbol = str(finding.symbol or "").strip()
    title = str(finding.title or "").casefold()
    source = str(finding.source or "").casefold()

    if (
        source == "runtime"
        and "taskorchestrator.execute_task" in title
    ):
        path = path or "core/task_orchestrator.py"
        symbol = symbol or "TaskOrchestrator.wrap.execute"

    if path == finding.path and symbol == finding.symbol:
        return finding

    return replace(finding, path=path, symbol=symbol)


def _external_queries(
    finding: EvidenceMaintenanceFinding,
) -> tuple[str, ...]:
    symbol = finding.symbol or _symbol_tail(finding.title)
    symbol_tail = _symbol_tail(symbol)
    path = finding.path or "unknown source"
    return _unique(
        (
            (
                f"{symbol} {finding.title} "
                "official documentation troubleshooting"
            ),
            (
                f"{symbol_tail} Python performance "
                "official documentation"
            ),
            (
                f"{path} {symbol_tail} GitHub issue "
                "runtime failure"
            ),
            (
                f"{finding.title} root cause "
                "regression test"
            ),
        )
    )


def _local_questions(
    finding: EvidenceMaintenanceFinding,
) -> tuple[str, ...]:
    return (
        (
            f"{finding.path} icindeki {finding.symbol or 'hedef sembol'} "
            "icin son Git degisikliklerini incele."
        ),
        "Ayni sembole ait kabul edilen ve reddedilen patchleri incele.",
        "Runtime olayinin mevcut kaynak surumunde yeniden uretilebilirligini kontrol et.",
        "Ilgili testlerin kapsadigi davranis ile gercek runtime olayini karsilastir.",
    )


def build_evidence_research_plan(
    finding: EvidenceMaintenanceFinding,
    *,
    retest_result: RetestExecutionResult | None = None,
) -> EvidenceResearchPlan:
    finding = _resolved_finding_location(finding)
    common = {
        "title": finding.title,
        "path": finding.path,
        "symbol": finding.symbol,
        "local_questions": _local_questions(finding),
        "safety_constraints": (
            "Internet kaynagi dogrudan patch olarak uygulanamaz.",
            "Ozel veya yerel ag adreslerine erisim yasaktir.",
            "Kaynaklar guven ve guncellik acisindan derecelendirilmelidir.",
            "Arastirma sonucu mevcut validator ve worktree zincirini atlayamaz.",
        ),
    }
    if retest_result is not None:
        if retest_result.status == RETEST_PASSED:
            return EvidenceResearchPlan(
                status=NOT_NEEDED,
                reason=(
                    "Primary yeniden testler gecti. "
                    "Mevcut kanit dis arastirma gerektirmiyor."
                ),
                **common,
            )
        if retest_result.status == RETEST_BLOCKED:
            return EvidenceResearchPlan(
                status=BLOCKED,
                reason=(
                    "Yeniden test tamamlanamadi. "
                    "Once test engeli giderilmeli."
                ),
                **common,
            )
        if retest_result.status == RETEST_FAILED:
            return EvidenceResearchPlan(
                status=EXTERNAL_APPROVAL_REQUIRED,
                reason=(
                    "Primary yeniden testler basarisiz oldu. "
                    "Yerel kanit ile resmi dis kaynaklar birlikte incelenmeli."
                ),
                external_queries=_external_queries(finding),
                preferred_sources=(
                    "Resmi Python dokumantasyonu",
                    "Kullanilan kutuphanenin resmi dokumantasyonu",
                    "Resmi GitHub deposu ve issue kayitlari",
                    "Surum notlari ve degisiklik gunlukleri",
                ),
                **common,
            )
    if (
        finding.lifecycle == "ACTIVE"
        and finding.classification in {"A", "B"}
        and finding.score >= 70
    ):
        return EvidenceResearchPlan(
            status=LOCAL_REVIEW,
            reason=(
                "Aktif ve yuksek riskli bulgu. "
                "Once yerel kod, Git gecmisi ve deney hafizasi incelenmeli."
            ),
            **common,
        )
    return EvidenceResearchPlan(
        status=NOT_NEEDED,
        reason=(
            "Bulgu su anda dis arastirma gerektirecek "
            "yeterli risk veya basarisiz dogrulama kaniti tasimiyor."
        ),
        **common,
    )
