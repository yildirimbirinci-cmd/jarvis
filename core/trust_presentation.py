from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

_SCHEMA_VERSION = 1
_MAX_REPORT_BYTES = 4 * 1024 * 1024


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > _MAX_REPORT_BYTES:
        raise ValueError("trust report exceeds size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("trust report is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("trust report must be an object")
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


def _bounded_int(value: object, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, min(100, number))


def _string_list(value: object, *, limit: int = 12) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    rows = tuple(str(item).strip() for item in value if str(item).strip())
    return rows[:limit]


@dataclass(frozen=True, slots=True)
class TrustPresentation:
    schema_version: int
    recommendation: str
    recommendation_label: str
    headline: str
    short_summary: str
    voice_summary: str
    decision_guidance: str
    score_lines: tuple[str, ...]
    evidence_lines: tuple[str, ...]
    change_lines: tuple[str, ...]
    validation_lines: tuple[str, ...]
    warning_lines: tuple[str, ...]
    approval_checklist: tuple[str, ...]
    source_report_path: str
    presentation_path: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in (
            "score_lines",
            "evidence_lines",
            "change_lines",
            "validation_lines",
            "warning_lines",
            "approval_checklist",
        ):
            payload[key] = list(payload[key])
        return payload


class ApprovalTrustPresenter:
    """Convert a machine trust report into concise Turkish owner guidance.

    The presenter never upgrades the underlying recommendation. A ``hold``
    report cannot produce approval language and a ``review`` report always
    asks for manual inspection before a token can be used.
    """

    _LABELS = {
        "approve": "Onay öneriliyor",
        "review": "Manuel inceleme gerekli",
        "hold": "Onaylama önerilmiyor",
    }

    def __init__(self, trust_report_path: str | Path) -> None:
        self.report_path = Path(trust_report_path).expanduser().resolve()
        self.report = _read_json(self.report_path)

    def build(self, *, output_path: str | Path | None = None) -> TrustPresentation:
        report = self.report
        recommendation = str(report.get("recommendation", "hold")).strip().lower()
        if recommendation not in self._LABELS:
            recommendation = "hold"

        scorecard = report.get("scorecard")
        scores = scorecard if isinstance(scorecard, Mapping) else {}
        overall = _bounded_int(scores.get("overall_score"))
        evidence = _bounded_int(scores.get("evidence_score"))
        test = _bounded_int(scores.get("test_score"))
        risk = _bounded_int(scores.get("risk_score"), 100)
        rollback = _bounded_int(scores.get("rollback_score"))
        scope = _bounded_int(scores.get("scope_score"))

        changed_files = _string_list(report.get("changed_files"), limit=20)
        evidence_ids = _string_list(report.get("evidence_ids"), limit=10)
        alternatives = _string_list(report.get("alternatives_considered"), limit=5)
        warnings = _string_list(report.get("warnings"), limit=10)
        checklist = _string_list(report.get("approval_checklist"), limit=10)

        root_cause = str(report.get("root_cause_summary", "Kök neden özeti yok.")).strip()
        reason = str(report.get("recommendation_reason", "")).strip()
        focused = str(report.get("focused_tests", "Focused test kaydı yok.")).strip()
        full = str(report.get("full_tests", "Tam regresyon kaydı yok.")).strip()
        impact = str(report.get("impact_summary", "Etki özeti yok.")).strip()
        rollback_summary = str(report.get("rollback_summary", "Rollback özeti yok.")).strip()

        if recommendation == "approve":
            headline = f"Onay öneriliyor — güven puanı {overall}/100"
            decision_guidance = (
                "Kök neden, testler, değişiklik kapsamı ve rollback kaydı beklentinle uyuşuyorsa "
                "tek kullanımlık commit onayını verebilirsin."
            )
            voice_action = "Onay vermeden önce değişen dosyaları ve beklenen kullanıcı davranışını kontrol et."
        elif recommendation == "review":
            headline = f"Manuel inceleme gerekli — güven puanı {overall}/100"
            decision_guidance = (
                "Doğrudan onay verme. Uyarıları, alternatif hipotezleri ve değişiklik kapsamını incele; "
                "gerekirse ek tanılama iste."
            )
            voice_action = "Bu değişiklik için otomatik onay önermiyorum; önce ayrıntılı inceleme gerekiyor."
        else:
            headline = f"Onaylama önerilmiyor — güven puanı {overall}/100"
            decision_guidance = (
                "Commit onayı verme. Eksik doğrulama, rollback veya kanıt problemi giderildikten sonra "
                "yeni bir öneri hazırlanmalı."
            )
            voice_action = "Bu değişikliği şu anda onaylama; güvenlik koşulları tamamlanmamış."

        short_summary = (
            f"{headline}. Risk {risk}/100, test güveni {test}/100, kanıt gücü {evidence}/100. "
            f"{len(changed_files)} dosya değişiyor. {reason}"
        ).strip()
        voice_summary = (
            f"{self._LABELS[recommendation]}. Genel güven puanı yüzde {overall}. "
            f"Kanıt puanı yüzde {evidence}, test puanı yüzde {test}, risk puanı yüzde {risk}. "
            f"Kök neden özeti: {root_cause} {voice_action}"
        )[:1400]

        evidence_lines = [f"Kök neden: {root_cause}"]
        if evidence_ids:
            evidence_lines.append("Kanıtlar: " + ", ".join(evidence_ids))
        if alternatives:
            evidence_lines.append("Alternatifler: " + "; ".join(alternatives))

        change_lines = list(changed_files) if changed_files else [impact]
        validation_lines = (
            f"Focused test: {focused}",
            f"Tam regresyon: {full}",
            rollback_summary,
        )
        score_lines = (
            f"Genel güven: {overall}/100",
            f"Kanıt gücü: {evidence}/100",
            f"Test güveni: {test}/100",
            f"Risk: {risk}/100",
            f"Rollback: {rollback}/100",
            f"Kapsam güveni: {scope}/100",
        )

        presentation_path = (
            Path(output_path).expanduser().resolve()
            if output_path is not None
            else self.report_path.with_name("approval_trust_presentation.json")
        )
        presentation = TrustPresentation(
            schema_version=_SCHEMA_VERSION,
            recommendation=recommendation,
            recommendation_label=self._LABELS[recommendation],
            headline=headline,
            short_summary=short_summary,
            voice_summary=voice_summary,
            decision_guidance=decision_guidance,
            score_lines=score_lines,
            evidence_lines=tuple(evidence_lines),
            change_lines=tuple(change_lines),
            validation_lines=validation_lines,
            warning_lines=warnings,
            approval_checklist=checklist,
            source_report_path=str(self.report_path),
            presentation_path=str(presentation_path),
        )
        _atomic_write_json(presentation_path, presentation.to_dict())
        return presentation
