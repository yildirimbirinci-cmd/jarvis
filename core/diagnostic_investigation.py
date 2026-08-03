from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, Sequence


class EvidenceLike(Protocol):
    evidence_id: str
    kind: str
    source: str
    summary: str
    confidence: int
    subsystem: str


@dataclass(frozen=True, slots=True)
class DiagnosticHypothesis:
    hypothesis_id: str
    subsystem: str
    cause: str
    confidence: int
    rank_score: int
    evidence_ids: tuple[str, ...]
    evidence_sources: tuple[str, ...]
    explanation: str
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "subsystem": self.subsystem,
            "cause": self.cause,
            "confidence": self.confidence,
            "rank_score": self.rank_score,
            "evidence_ids": list(self.evidence_ids),
            "evidence_sources": list(self.evidence_sources),
            "explanation": self.explanation,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticInvestigationStep:
    order: int
    hypothesis_id: str
    action: str
    reason: str
    completed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "order": self.order,
            "hypothesis_id": self.hypothesis_id,
            "action": self.action,
            "reason": self.reason,
            "completed": self.completed,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticInvestigation:
    domain: str
    status: str
    hypotheses: tuple[DiagnosticHypothesis, ...]
    steps: tuple[DiagnosticInvestigationStep, ...]
    root_cause_hypothesis_id: str | None
    confidence_margin: int | None

    @property
    def root_cause(self) -> DiagnosticHypothesis | None:
        if self.root_cause_hypothesis_id is None:
            return None
        return next(
            (item for item in self.hypotheses if item.hypothesis_id == self.root_cause_hypothesis_id),
            None,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "status": self.status,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "steps": [item.to_dict() for item in self.steps],
            "root_cause_hypothesis_id": self.root_cause_hypothesis_id,
            "confidence_margin": self.confidence_margin,
        }


def _health_penalty_by_subsystem(health: Mapping[str, object] | None) -> dict[str, int]:
    if not health:
        return {}
    result: dict[str, int] = {}
    raw_items = health.get("subsystems", ())
    if not isinstance(raw_items, Sequence):
        return result
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("subsystem", "")).strip()
        score = raw.get("score")
        if not name or not isinstance(score, int):
            continue
        result[name] = max(0, min(30, (100 - score) // 3))
    return result


def build_investigation(
    domain: str,
    evidence: Iterable[EvidenceLike],
    *,
    health: Mapping[str, object] | None = None,
    measurement_action: str = "Ek ölçüm ve doğrulama kanıtı topla.",
) -> DiagnosticInvestigation:
    grouped: dict[tuple[str, str], list[EvidenceLike]] = {}
    for item in evidence:
        subsystem = str(item.subsystem or domain).strip() or domain
        cause = str(item.kind or "unknown_cause").strip() or "unknown_cause"
        grouped.setdefault((subsystem, cause), []).append(item)

    if not grouped:
        return DiagnosticInvestigation(
            domain=domain,
            status="needs_evidence",
            hypotheses=(),
            steps=(
                DiagnosticInvestigationStep(
                    order=1,
                    hypothesis_id="measurement-required",
                    action=measurement_action,
                    reason="Kök neden hipotezi kurmak için doğrulanmış kanıt yok.",
                    completed=False,
                ),
            ),
            root_cause_hypothesis_id=None,
            confidence_margin=None,
        )

    health_penalties = _health_penalty_by_subsystem(health)
    hypotheses: list[DiagnosticHypothesis] = []
    for (subsystem, cause), items in grouped.items():
        strongest = max(max(0, min(100, int(item.confidence))) for item in items)
        repetition_bonus = min(12, max(0, len(items) - 1) * 4)
        source_bonus = min(8, max(0, len({item.source for item in items}) - 1) * 4)
        rank_score = min(100, strongest + repetition_bonus + source_bonus + health_penalties.get(subsystem, 0))
        digest = hashlib.sha256(f"{domain}:{subsystem}:{cause}".encode("utf-8")).hexdigest()[:12]
        evidence_ids = tuple(dict.fromkeys(str(item.evidence_id) for item in items))
        sources = tuple(dict.fromkeys(str(item.source) for item in items))
        hypotheses.append(
            DiagnosticHypothesis(
                hypothesis_id=f"hyp-{digest}",
                subsystem=subsystem,
                cause=cause,
                confidence=strongest,
                rank_score=rank_score,
                evidence_ids=evidence_ids,
                evidence_sources=sources,
                explanation=(
                    f"{subsystem} alt sisteminde {cause} hipotezi "
                    f"{len(items)} kanıt ve {len(sources)} bağımsız kaynakla destekleniyor."
                ),
                next_action=(
                    f"{subsystem} için {cause} hipotezini odaklı ölçüm ve testle doğrula; "
                    "yalnız doğrulanırsa sınırlı düzeltme planla."
                ),
            )
        )

    hypotheses.sort(key=lambda item: (-item.rank_score, -item.confidence, item.hypothesis_id))
    top = hypotheses[0]
    second_score = hypotheses[1].rank_score if len(hypotheses) > 1 else 0
    margin = top.rank_score - second_score if len(hypotheses) > 1 else top.rank_score
    root_cause_ready = top.confidence >= 75 and (len(hypotheses) == 1 or margin >= 5)
    status = "root_cause_identified" if root_cause_ready else "investigating"
    root_id = top.hypothesis_id if root_cause_ready else None

    steps: list[DiagnosticInvestigationStep] = []
    for index, hypothesis in enumerate(hypotheses[:5], start=1):
        steps.append(
            DiagnosticInvestigationStep(
                order=index,
                hypothesis_id=hypothesis.hypothesis_id,
                action=hypothesis.next_action,
                reason=(
                    f"Sıra puanı {hypothesis.rank_score}; güven {hypothesis.confidence}; "
                    f"kanıt sayısı {len(hypothesis.evidence_ids)}."
                ),
                completed=root_cause_ready and hypothesis.hypothesis_id == root_id,
            )
        )

    return DiagnosticInvestigation(
        domain=domain,
        status=status,
        hypotheses=tuple(hypotheses),
        steps=tuple(steps),
        root_cause_hypothesis_id=root_id,
        confidence_margin=margin,
    )
