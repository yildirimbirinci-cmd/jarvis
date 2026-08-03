from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .diagnostic_health import DiagnosticHealthSummary, build_health_summary
from .diagnostic_investigation import DiagnosticInvestigation, build_investigation
from .diagnostic_registry import (
    DiagnosticDomainRegistry,
    DiagnosticDomainSpec,
    DiagnosticSubsystemSpec,
    builtin_diagnostic_registry,
    normalise,
)


_REPAIR_MARKERS = ("duzelt", "gider", "coz", "incele", "analiz", "iyilestir", "optimize", "neden")


def _safe_relative(root: Path, value: str) -> str | None:
    candidate = Path(str(value).replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (root / candidate).resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        return None
    return candidate.as_posix()


@dataclass(frozen=True, slots=True)
class DiagnosticEvidence:
    evidence_id: str
    kind: str
    source: str
    summary: str
    confidence: int
    domain: str = ""
    subsystem: str = ""


@dataclass(frozen=True, slots=True)
class DiagnosticFinding:
    finding_id: str
    subsystem: str
    title: str
    explanation: str
    confidence: int
    priority: int
    affected_files: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    proposed_action: str
    requires_measurement: bool = False


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    schema_version: int
    request: str
    domain: str
    status: str
    subsystems: tuple[str, ...]
    evidence: tuple[DiagnosticEvidence, ...]
    findings: tuple[DiagnosticFinding, ...]
    planner_task: Mapping[str, object] | None
    health: DiagnosticHealthSummary | None = None
    investigation: DiagnosticInvestigation | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request": self.request,
            "domain": self.domain,
            "status": self.status,
            "subsystems": list(self.subsystems),
            "evidence": [asdict(item) for item in self.evidence],
            "findings": [asdict(item) for item in self.findings],
            "planner_task": dict(self.planner_task) if self.planner_task else None,
            "health": self.health.to_dict() if self.health else None,
            "investigation": self.investigation.to_dict() if self.investigation else None,
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target


class DiagnosticEngine:
    """Evidence-bound, pluggable problem understanding engine."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        registry: DiagnosticDomainRegistry | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.registry = registry or builtin_diagnostic_registry()

    def recognises_request(self, text: object) -> bool:
        key = normalise(text)
        return any(marker in key for marker in _REPAIR_MARKERS) and self.registry.detect(key) is not None

    def _affected_files(self, subsystems: Sequence[DiagnosticSubsystemSpec]) -> tuple[str, ...]:
        wanted: list[str] = []
        for subsystem in subsystems:
            for value in subsystem.affected_files:
                relative = _safe_relative(self.project_root, value)
                if relative and (self.project_root / relative).is_file() and relative not in wanted:
                    wanted.append(relative)
        return tuple(wanted)

    def _read_logs(
        self,
        domain: DiagnosticDomainSpec,
        log_paths: Iterable[str | Path],
    ) -> tuple[DiagnosticEvidence, ...]:
        evidence: list[DiagnosticEvidence] = []
        for raw_path in log_paths:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = self.project_root / path
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved != self.project_root and self.project_root not in resolved.parents:
                continue
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")[-200_000:]
            except OSError:
                continue
            source = resolved.relative_to(self.project_root).as_posix()
            for pattern in domain.patterns:
                matches = re.findall(pattern.pattern, text, flags=re.IGNORECASE)
                if not matches:
                    continue
                digest = hashlib.sha256(f"{domain.name}:{source}:{pattern.kind}".encode("utf-8")).hexdigest()[:12]
                evidence.append(DiagnosticEvidence(
                    evidence_id=f"diag-{digest}",
                    kind=pattern.kind,
                    source=source,
                    summary=f"{pattern.kind}: {len(matches)} eşleşme bulundu.",
                    confidence=pattern.confidence,
                    domain=domain.name,
                    subsystem=pattern.subsystem,
                ))
        return tuple(evidence)

    def _runtime_evidence(
        self,
        domain: DiagnosticDomainSpec,
        subsystems: Sequence[DiagnosticSubsystemSpec],
        raw_items: Iterable[Mapping[str, object]],
    ) -> tuple[DiagnosticEvidence, ...]:
        allowed_subsystems = {item.name for item in domain.subsystems}
        result: list[DiagnosticEvidence] = []
        for item in raw_items:
            item_domain = str(item.get("domain", domain.name))
            if item_domain not in ("", domain.name):
                continue
            summary = " ".join(str(item.get("summary", "")).split())[:500]
            if not summary:
                continue
            subsystem = str(item.get("subsystem", subsystems[0].name))
            if subsystem not in allowed_subsystems:
                subsystem = subsystems[0].name
            confidence = max(0, min(100, int(item.get("confidence", 70))))
            evidence_id = str(item.get("evidence_id") or hashlib.sha256(summary.encode("utf-8")).hexdigest()[:12])
            result.append(DiagnosticEvidence(
                evidence_id,
                "runtime",
                str(item.get("source", "runtime")),
                summary,
                confidence,
                domain.name,
                subsystem,
            ))
        return tuple(result)

    def diagnose(
        self,
        request: str,
        *,
        log_paths: Iterable[str | Path] = (),
        runtime_evidence: Iterable[Mapping[str, object]] = (),
    ) -> DiagnosticReport:
        if not self.recognises_request(request):
            return DiagnosticReport(1, request, "unknown", "unsupported", (), (), (), None, None, None)

        domain = self.registry.detect(request)
        if domain is None:
            return DiagnosticReport(1, request, "unknown", "unsupported", (), (), (), None, None, None)
        subsystem_specs = self.registry.requested_subsystems(domain, request)
        subsystem_names = tuple(item.name for item in subsystem_specs)
        evidence = list(self._read_logs(domain, log_paths))
        evidence.extend(self._runtime_evidence(domain, subsystem_specs, runtime_evidence))
        affected_specs = list(subsystem_specs)
        evidence_subsystems = {item.subsystem for item in evidence if item.subsystem}
        for candidate in domain.subsystems:
            if candidate.name in evidence_subsystems and candidate not in affected_specs:
                affected_specs.append(candidate)
        affected = self._affected_files(affected_specs)

        if not evidence:
            finding = DiagnosticFinding(
                finding_id=f"{domain.name}-measurement-required",
                subsystem=domain.name,
                title=f"{domain.name} alanı için ölçüm gerekiyor",
                explanation="İstek alanı belirlendi ancak kök nedeni kanıtlayan log veya runtime bulgusu bulunamadı.",
                confidence=35,
                priority=90,
                affected_files=affected,
                evidence_ids=(),
                proposed_action=domain.measurement_action,
                requires_measurement=True,
            )
            health = build_health_summary(domain.name, subsystem_names, ())
            investigation = build_investigation(
                domain.name,
                (),
                health=health.to_dict(),
                measurement_action=domain.measurement_action,
            )
            return DiagnosticReport(1, request, domain.name, "needs_evidence", subsystem_names, (), (finding,), None, health, investigation)

        health = build_health_summary(domain.name, subsystem_names, evidence)
        investigation = build_investigation(
            domain.name,
            evidence,
            health=health.to_dict(),
            measurement_action=domain.measurement_action,
        )
        selected = investigation.root_cause or investigation.hypotheses[0]
        selected_evidence = [
            item for item in evidence if item.evidence_id in selected.evidence_ids
        ]
        best = max(selected_evidence, key=lambda item: item.confidence)
        finding = DiagnosticFinding(
            finding_id=f"{domain.name}-{selected.cause}",
            subsystem=selected.subsystem,
            title=f"{domain.name} kök neden adayı: {selected.cause}",
            explanation=selected.explanation,
            confidence=selected.confidence,
            priority=min(100, 50 + selected.rank_score // 2),
            affected_files=affected,
            evidence_ids=selected.evidence_ids,
            proposed_action=selected.next_action,
            requires_measurement=investigation.root_cause is None,
        )
        planner_task: Mapping[str, object] | None = None
        if investigation.root_cause is not None:
            planner_task = {
                "task_id": f"diagnostic-{hashlib.sha256(request.encode('utf-8')).hexdigest()[:12]}",
                "state": "solution_found",
                "title": finding.title,
                "problem": finding.explanation,
                "solution": finding.proposed_action,
                "rationale": "Tanılama görevi yalnız somut log/runtime kanıtına dayanır.",
                "affected_files": list(finding.affected_files),
                "test_plan": list(domain.test_plan),
                "evidence_ids": list(finding.evidence_ids),
                "risk": "medium",
                "impact_score": finding.priority,
                "confidence_score": finding.confidence,
                "requires_experiment": True,
                "diagnostic_domain": domain.name,
                "diagnostic_subsystem": finding.subsystem,
            }
        if planner_task is not None:
            planner_task = dict(planner_task)
            planner_task["diagnostic_health"] = health.to_dict()
            planner_task["diagnostic_investigation"] = investigation.to_dict()
            status = "actionable"
        else:
            status = "investigating"
        return DiagnosticReport(
            1, request, domain.name, status, subsystem_names, tuple(evidence),
            (finding,), planner_task, health, investigation,
        )
