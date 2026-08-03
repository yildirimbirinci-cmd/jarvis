from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


_VOICE_SUBSYSTEMS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("audio_input", ("mikrofon", "giris", "input", "record", "capture"), ("core/voice_service.py", "core/audio_device_resilience.py", "config.py")),
    ("audio_output", ("hoparlor", "cikis", "output", "playback", "sample rate"), ("core/voice_service.py", "core/audio_device_resilience.py", "config.py")),
    ("wake_word", ("wake", "uyandirma", "jarvis", "cervis"), ("app.py", "core/voice_service.py")),
    ("speech_to_text", ("whisper", "stt", "yanlis alg", "transcri"), ("core/voice_service.py", "core/voice_acceptance_service.py")),
    ("text_to_speech", ("piper", "tts", "seslend", "konusam"), ("core/voice_service.py", "app.py")),
    ("owner_verification", ("owner", "sahip", "ses profili", "dogrul"), ("core/voice_service.py", "core/voice_acceptance_service.py")),
    ("barge_in", ("barge", "araya gir", "dur", "sustur", "kes"), ("app.py", "core/voice_turn_coordinator.py")),
    ("latency", ("gecik", "latency", "yavas", "beklet"), ("core/voice_service.py", "core/voice_turn_coordinator.py", "core/runtime_instrumentation.py")),
)

_ERROR_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("invalid_sample_rate", r"invalid sample rate|[- ]9997", 95),
    ("unsupported_audio_api", r"blocking api not supported|[- ]9999|wdm-ks", 92),
    ("missing_piper_model", r"piper.*(?:model|onnx).*(?:missing|not found|bulunamad)", 88),
    ("audio_device_missing", r"(?:input|output|microphone|speaker|aygit).*(?:not found|unavailable|bulunamad)", 85),
    ("owner_rejected", r"owner.*(?:reject|failed)|sahip.*(?:redd|dogrulanamad)", 75),
    ("whisper_failure", r"whisper.*(?:error|failed|timeout|hata)", 80),
    ("tts_failure", r"tts.*(?:error|failed|timeout|hata)|seslendirilemedi", 80),
)


def _normalise(value: object) -> str:
    text = str(value or "").casefold()
    return " ".join(text.translate(str.maketrans("çğıöşüâîû", "cgiosuaiu")).split())


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
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target


class DiagnosticEngine:
    """Turn broad repair requests into evidence-bound planner tasks.

    The engine never claims a root cause from keywords alone. A code-changing
    planner task is emitted only when concrete log/runtime evidence exists.
    """

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).expanduser().resolve()

    @staticmethod
    def recognises_request(text: object) -> bool:
        key = _normalise(text)
        repair = any(token in key for token in ("duzelt", "gider", "coz", "incele", "analiz"))
        domain = any(token in key for token in ("ses", "mikrofon", "hoparlor", "whisper", "piper", "tts", "wake", "uyandirma"))
        return repair and domain

    def _requested_subsystems(self, request: str) -> tuple[str, ...]:
        key = _normalise(request)
        explicit = [name for name, markers, _ in _VOICE_SUBSYSTEMS if any(marker in key for marker in markers)]
        if explicit:
            return tuple(dict.fromkeys(explicit))
        return tuple(name for name, _, _ in _VOICE_SUBSYSTEMS)

    def _read_logs(self, log_paths: Iterable[str | Path]) -> tuple[DiagnosticEvidence, ...]:
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
            for label, pattern, confidence in _ERROR_PATTERNS:
                matches = re.findall(pattern, text, flags=re.IGNORECASE)
                if not matches:
                    continue
                source = resolved.relative_to(self.project_root).as_posix()
                digest = hashlib.sha256(f"{source}:{label}".encode("utf-8")).hexdigest()[:12]
                evidence.append(DiagnosticEvidence(
                    evidence_id=f"diag-{digest}",
                    kind=label,
                    source=source,
                    summary=f"{label}: {len(matches)} eşleşme bulundu.",
                    confidence=confidence,
                ))
        return tuple(evidence)

    def _affected_files(self, subsystems: Sequence[str]) -> tuple[str, ...]:
        wanted: list[str] = []
        selected = set(subsystems)
        for name, _, files in _VOICE_SUBSYSTEMS:
            if name not in selected:
                continue
            for value in files:
                relative = _safe_relative(self.project_root, value)
                if relative and (self.project_root / relative).is_file() and relative not in wanted:
                    wanted.append(relative)
        return tuple(wanted)

    def diagnose(
        self,
        request: str,
        *,
        log_paths: Iterable[str | Path] = (),
        runtime_evidence: Iterable[Mapping[str, object]] = (),
    ) -> DiagnosticReport:
        if not self.recognises_request(request):
            return DiagnosticReport(1, request, "unknown", "unsupported", (), (), (), None)

        subsystems = self._requested_subsystems(request)
        evidence = list(self._read_logs(log_paths))
        for item in runtime_evidence:
            summary = " ".join(str(item.get("summary", "")).split())[:500]
            if not summary:
                continue
            raw_confidence = item.get("confidence", 70)
            confidence = max(0, min(100, int(raw_confidence)))
            evidence_id = str(item.get("evidence_id") or hashlib.sha256(summary.encode("utf-8")).hexdigest()[:12])
            evidence.append(DiagnosticEvidence(evidence_id, "runtime", str(item.get("source", "runtime")), summary, confidence))

        affected = self._affected_files(subsystems)
        if not evidence:
            finding = DiagnosticFinding(
                finding_id="voice-measurement-required",
                subsystem="voice",
                title="Ses sistemi için ölçüm gerekiyor",
                explanation="İstek alanı belirlendi ancak kök nedeni kanıtlayan log veya runtime bulgusu bulunamadı.",
                confidence=35,
                priority=90,
                affected_files=affected,
                evidence_ids=(),
                proposed_action="Önce ses tanılama çalıştır; cihaz, sample-rate, STT, TTS ve gecikme sonuçlarını kaydet.",
                requires_measurement=True,
            )
            return DiagnosticReport(1, request, "voice", "needs_evidence", subsystems, (), (finding,), None)

        best = max(evidence, key=lambda item: item.confidence)
        kind_to_subsystem = {
            "invalid_sample_rate": "audio_output",
            "unsupported_audio_api": "audio_input",
            "missing_piper_model": "text_to_speech",
            "audio_device_missing": "audio_input",
            "owner_rejected": "owner_verification",
            "whisper_failure": "speech_to_text",
            "tts_failure": "text_to_speech",
            "runtime": subsystems[0],
        }
        subsystem = kind_to_subsystem.get(best.kind, subsystems[0])
        finding = DiagnosticFinding(
            finding_id=f"voice-{best.kind}",
            subsystem=subsystem,
            title=f"Ses sistemi kök neden adayı: {best.kind}",
            explanation=best.summary,
            confidence=best.confidence,
            priority=min(100, 50 + best.confidence // 2),
            affected_files=affected,
            evidence_ids=tuple(item.evidence_id for item in evidence),
            proposed_action="Kanıtın işaret ettiği alt sistemi sınırlı değişiklik ve odaklı testlerle düzelt.",
        )
        planner_task: Mapping[str, object] = {
            "task_id": f"diagnostic-{hashlib.sha256(request.encode('utf-8')).hexdigest()[:12]}",
            "state": "solution_found",
            "title": finding.title,
            "problem": finding.explanation,
            "solution": finding.proposed_action,
            "rationale": "Tanılama görevi yalnız somut log/runtime kanıtına dayanır.",
            "affected_files": list(finding.affected_files),
            "test_plan": ["Ses alt sistemi için ilgili odaklı testleri çalıştır.", "Tam regresyonu çalıştır."],
            "evidence_ids": list(finding.evidence_ids),
            "risk": "medium",
            "impact_score": finding.priority,
            "confidence_score": finding.confidence,
            "requires_experiment": True,
            "diagnostic_domain": "voice",
            "diagnostic_subsystem": finding.subsystem,
        }
        return DiagnosticReport(1, request, "voice", "actionable", subsystems, tuple(evidence), (finding,), planner_task)
