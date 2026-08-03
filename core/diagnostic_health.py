from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, Sequence


class EvidenceLike(Protocol):
    evidence_id: str
    confidence: int
    subsystem: str


@dataclass(frozen=True, slots=True)
class DiagnosticSubsystemHealth:
    subsystem: str
    score: int | None
    status: str
    evidence_ids: tuple[str, ...]
    evidence_count: int
    reason: str


@dataclass(frozen=True, slots=True)
class DiagnosticHealthSummary:
    domain: str
    score: int | None
    status: str
    subsystems: tuple[DiagnosticSubsystemHealth, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "score": self.score,
            "status": self.status,
            "subsystems": [
                {
                    "subsystem": item.subsystem,
                    "score": item.score,
                    "status": item.status,
                    "evidence_ids": list(item.evidence_ids),
                    "evidence_count": item.evidence_count,
                    "reason": item.reason,
                }
                for item in self.subsystems
            ],
        }


def _status_for_score(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score <= 30:
        return "critical"
    if score <= 60:
        return "degraded"
    if score <= 80:
        return "warning"
    return "healthy"


def _score_evidence(items: Sequence[EvidenceLike]) -> int:
    # Confidence is treated as severity evidence. Repeated independent evidence
    # increases the penalty, but the cap prevents one noisy subsystem from
    # producing negative or unbounded health values.
    strongest = max(max(0, min(100, int(item.confidence))) for item in items)
    repetition_penalty = min(20, max(0, len(items) - 1) * 5)
    penalty = min(100, round(strongest * 0.72) + repetition_penalty)
    return max(0, 100 - penalty)


def build_health_summary(
    domain: str,
    subsystem_names: Iterable[str],
    evidence: Iterable[EvidenceLike],
) -> DiagnosticHealthSummary:
    evidence_by_subsystem: dict[str, list[EvidenceLike]] = {}
    for item in evidence:
        name = str(item.subsystem or "").strip()
        if not name:
            continue
        evidence_by_subsystem.setdefault(name, []).append(item)

    ordered_names: list[str] = []
    for name in subsystem_names:
        value = str(name).strip()
        if value and value not in ordered_names:
            ordered_names.append(value)
    for name in evidence_by_subsystem:
        if name not in ordered_names:
            ordered_names.append(name)

    subsystem_health: list[DiagnosticSubsystemHealth] = []
    measured_scores: list[int] = []
    for name in ordered_names:
        items = evidence_by_subsystem.get(name, [])
        if not items:
            subsystem_health.append(
                DiagnosticSubsystemHealth(
                    subsystem=name,
                    score=None,
                    status="unknown",
                    evidence_ids=(),
                    evidence_count=0,
                    reason="Bu alt sistem için ölçülmüş tanılama kanıtı yok.",
                )
            )
            continue
        score = _score_evidence(items)
        measured_scores.append(score)
        subsystem_health.append(
            DiagnosticSubsystemHealth(
                subsystem=name,
                score=score,
                status=_status_for_score(score),
                evidence_ids=tuple(item.evidence_id for item in items),
                evidence_count=len(items),
                reason=(
                    f"{len(items)} kanıt değerlendirildi; en yüksek kanıt güveni "
                    f"{max(int(item.confidence) for item in items)}."
                ),
            )
        )

    domain_score = min(measured_scores) if measured_scores else None
    return DiagnosticHealthSummary(
        domain=domain,
        score=domain_score,
        status=_status_for_score(domain_score),
        subsystems=tuple(subsystem_health),
    )
