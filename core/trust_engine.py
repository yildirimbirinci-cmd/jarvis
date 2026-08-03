from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

_SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 4 * 1024 * 1024
_PASS_RE = re.compile(r"(?P<count>\d+)\s+passed\b", re.IGNORECASE)
_SKIP_RE = re.compile(r"(?P<count>\d+)\s+skipped\b", re.IGNORECASE)
_FAIL_RE = re.compile(r"(?P<count>\d+)\s+failed\b", re.IGNORECASE)


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError(f"{label} exceeds size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _atomic_write_json(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
        + b"\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _bounded_score(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def _command(promotion: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    raw = promotion.get("commands")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    for row in raw:
        if isinstance(row, Mapping) and row.get("name") == name:
            return row
    return None


def _test_counts(command: Mapping[str, object] | None) -> tuple[int, int, int, bool]:
    if command is None:
        return 0, 0, 0, False
    output = str(command.get("output", ""))
    passed = sum(int(match.group("count")) for match in _PASS_RE.finditer(output))
    skipped = sum(int(match.group("count")) for match in _SKIP_RE.finditer(output))
    failed = sum(int(match.group("count")) for match in _FAIL_RE.finditer(output))
    try:
        exit_code = int(command.get("exit_code", -1))
    except (TypeError, ValueError):
        exit_code = -1
    return passed, skipped, failed, exit_code == 0 and failed == 0


def _diagnostic_root(diagnostic: Mapping[str, object] | None) -> tuple[int, str, tuple[str, ...], tuple[str, ...]]:
    if diagnostic is None:
        return 0, "Tanılama raporu bağlanmamış.", (), ()
    investigation = diagnostic.get("investigation")
    if not isinstance(investigation, Mapping):
        return 0, "Tanılama raporunda investigation yok.", (), ()
    root_id = investigation.get("root_cause_hypothesis_id")
    hypotheses = investigation.get("hypotheses")
    if not isinstance(hypotheses, Sequence) or isinstance(hypotheses, (str, bytes)):
        return 0, "Kök neden hipotezi kaydedilmemiş.", (), ()
    alternatives: list[str] = []
    selected: Mapping[str, object] | None = None
    for row in hypotheses:
        if not isinstance(row, Mapping):
            continue
        label = f"{row.get('subsystem', 'unknown')}: {row.get('cause', 'unknown')}"
        if row.get("hypothesis_id") == root_id:
            selected = row
        else:
            alternatives.append(label)
    if selected is None:
        return 35, "Kök neden henüz kesinleştirilmemiş.", (), tuple(alternatives[:5])
    confidence = _bounded_score(selected.get("confidence"), 0)
    evidence_ids = selected.get("evidence_ids")
    evidence = (
        tuple(str(item) for item in evidence_ids if str(item).strip())
        if isinstance(evidence_ids, Sequence) and not isinstance(evidence_ids, (str, bytes))
        else ()
    )
    explanation = str(selected.get("explanation", "Kök neden hipotezi doğrulandı.")).strip()
    evidence_bonus = min(15, max(0, len(evidence) - 1) * 5)
    return min(100, confidence + evidence_bonus), explanation, evidence, tuple(alternatives[:5])


@dataclass(frozen=True, slots=True)
class TrustScorecard:
    evidence_score: int
    test_score: int
    risk_score: int
    rollback_score: int
    scope_score: int
    overall_score: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ApprovalTrustReport:
    schema_version: int
    promotion_id: str
    experiment_id: str
    candidate_id: str
    recommendation: str
    recommendation_reason: str
    scorecard: TrustScorecard
    root_cause_summary: str
    evidence_ids: tuple[str, ...]
    alternatives_considered: tuple[str, ...]
    changed_files: tuple[str, ...]
    focused_tests: str
    full_tests: str
    impact_summary: str
    rollback_summary: str
    approval_checklist: tuple[str, ...]
    warnings: tuple[str, ...]
    report_path: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["scorecard"] = self.scorecard.to_dict()
        for key in (
            "evidence_ids",
            "alternatives_considered",
            "changed_files",
            "approval_checklist",
            "warnings",
        ):
            payload[key] = list(payload[key])
        return payload


class ApprovalTrustEngine:
    """Build an explainable, conservative recommendation for owner approval."""

    def __init__(
        self,
        promotion_result_path: str | Path,
        *,
        diagnostic_report_path: str | Path | None = None,
    ) -> None:
        self.promotion_path = Path(promotion_result_path).expanduser().resolve()
        self.promotion = _read_json(self.promotion_path, label="promotion result")
        self.diagnostic_path = (
            Path(diagnostic_report_path).expanduser().resolve()
            if diagnostic_report_path is not None
            else None
        )
        self.diagnostic = (
            _read_json(self.diagnostic_path, label="diagnostic report")
            if self.diagnostic_path is not None
            else None
        )

    def build(self, *, output_path: str | Path | None = None) -> ApprovalTrustReport:
        promotion = self.promotion
        files_raw = promotion.get("files")
        files = [row for row in files_raw if isinstance(row, Mapping)] if isinstance(files_raw, list) else []
        changed_files = tuple(str(row.get("relative_path", "")).replace("\\", "/") for row in files)
        changed_files = tuple(path for path in changed_files if path)

        evidence_score, root_summary, evidence_ids, alternatives = _diagnostic_root(self.diagnostic)
        focused = _command(promotion, "focused_tests")
        full = _command(promotion, "full_tests")
        focused_passed, focused_skipped, focused_failed, focused_ok = _test_counts(focused)
        full_passed, full_skipped, full_failed, full_ok = _test_counts(full)
        test_score = 100 if focused_ok and full_ok and focused_passed > 0 and full_passed > 0 else 0

        risk_label = str(promotion.get("risk", "medium")).lower().strip()
        base_risk = {"low": 15, "medium": 45, "high": 80, "critical": 100}.get(risk_label, 50)
        scope_penalty = min(35, max(0, len(changed_files) - 1) * 8)
        risk_score = min(100, base_risk + scope_penalty)
        scope_score = max(0, 100 - scope_penalty)

        checkpoint_root = str(promotion.get("checkpoint_root", "")).strip()
        checkpoint_rows = [str(row.get("checkpoint_path", "")).strip() for row in files]
        rollback_ready = bool(checkpoint_root) and bool(files) and all(checkpoint_rows)
        rollback_score = 100 if rollback_ready and not bool(promotion.get("rolled_back")) else 0

        overall = round(
            evidence_score * 0.30
            + test_score * 0.35
            + (100 - risk_score) * 0.20
            + rollback_score * 0.10
            + scope_score * 0.05
        )
        scorecard = TrustScorecard(
            evidence_score=evidence_score,
            test_score=test_score,
            risk_score=risk_score,
            rollback_score=rollback_score,
            scope_score=scope_score,
            overall_score=max(0, min(100, overall)),
        )

        warnings: list[str] = []
        if promotion.get("status") != "promoted" or bool(promotion.get("rolled_back")):
            warnings.append("Promotion sonucu onaylanabilir durumda değil.")
        if not focused_ok or focused_passed <= 0:
            warnings.append("Focused test doğrulaması eksik veya başarısız.")
        if not full_ok or full_passed <= 0:
            warnings.append("Tam regresyon doğrulaması eksik veya başarısız.")
        if evidence_score < 70:
            warnings.append("Kök neden kanıtı onay için yeterince güçlü değil.")
        if risk_score >= 60:
            warnings.append("Değişiklik riski yüksek.")
        if not rollback_ready:
            warnings.append("Doğrulanmış rollback checkpoint'i eksik.")

        if warnings and (test_score == 0 or rollback_score == 0 or promotion.get("status") != "promoted"):
            recommendation = "hold"
            reason = "Onaylama: doğrulama veya geri alma güvenliği eksik."
        elif warnings or scorecard.overall_score < 80:
            recommendation = "review"
            reason = "Manuel inceleme önerilir; kanıt veya risk eşiği tam karşılanmıyor."
        else:
            recommendation = "approve"
            reason = "Onay önerilir: kanıt, test, kapsam ve rollback eşikleri karşılandı."

        report_path = (
            Path(output_path).expanduser().resolve()
            if output_path is not None
            else self.promotion_path.parent / "approval_trust_report.json"
        )
        report = ApprovalTrustReport(
            schema_version=_SCHEMA_VERSION,
            promotion_id=str(promotion.get("promotion_id", "")),
            experiment_id=str(promotion.get("experiment_id", "")),
            candidate_id=str(promotion.get("candidate_id", "")),
            recommendation=recommendation,
            recommendation_reason=reason,
            scorecard=scorecard,
            root_cause_summary=root_summary,
            evidence_ids=evidence_ids,
            alternatives_considered=alternatives,
            changed_files=changed_files,
            focused_tests=(
                f"passed={focused_passed}; failed={focused_failed}; skipped={focused_skipped}; "
                f"exit={focused.get('exit_code') if focused else 'missing'}"
            ),
            full_tests=(
                f"passed={full_passed}; failed={full_failed}; skipped={full_skipped}; "
                f"exit={full.get('exit_code') if full else 'missing'}"
            ),
            impact_summary=(
                f"{len(changed_files)} dosya değişti: " + ", ".join(changed_files[:8])
                if changed_files
                else "Değişen dosya kaydı yok."
            ),
            rollback_summary=(
                f"Rollback hazır: {checkpoint_root}" if rollback_ready else "Rollback doğrulanamadı."
            ),
            approval_checklist=(
                "Kök neden ve kanıtları incele.",
                "Değişen dosya kapsamını doğrula.",
                "Focused ve tam regresyon sonuçlarını doğrula.",
                "Rollback checkpoint yolunun erişilebilir olduğunu doğrula.",
                "Beklenen kullanıcı davranışını manuel olarak kontrol et.",
            ),
            warnings=tuple(warnings),
            report_path=str(report_path),
        )
        _atomic_write_json(report_path, report.to_dict())
        return report
