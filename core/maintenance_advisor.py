from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from artmach_assistant.core.notification_store import NotificationStore
from artmach_assistant.core.project_improvement_service import ProjectImprovementAssessment
from artmach_assistant.core.runtime_observability import RuntimeFinding, RuntimeHealthReport
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

_SCHEMA_VERSION = 1
_MAX_BYTES = 2 * 1024 * 1024
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bucket(count: int) -> int:
    value = max(0, int(count))
    for boundary in (2, 3, 5, 10, 20, 50, 100, 250, 500, 1000):
        if value <= boundary:
            return boundary
    return 5000


def _digest(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class MaintenanceAlert:
    finding_id: str
    source: str
    severity: str
    title: str
    explanation: str
    evidence_summary: str
    recommendation: str
    workspace: str
    signature: str


@dataclass(frozen=True, slots=True)
class MaintenanceReview:
    generated_at: str
    runtime_report: RuntimeHealthReport
    active_alerts: tuple[MaintenanceAlert, ...]
    new_alerts: tuple[MaintenanceAlert, ...]

    def report(self, *, limit: int = 15) -> str:
        output_limit = max(1, min(int(limit), 40))
        lines = [
            "JARVIS BAKIM DEGERLENDIRMESI",
            (
                f"Calisma zamani olayi: {self.runtime_report.event_count}; "
                f"aktif bakim adayi: {len(self.active_alerts)}; "
                f"yeni uyari: {len(self.new_alerts)}"
            ),
        ]
        if not self.active_alerts:
            lines.append(
                "Mevcut kanit esiklerine gore kullaniciya bildirilmesi gereken aktif bakim adayi yok."
            )
        for alert in self.active_alerts[:output_limit]:
            lines.append(
                f"[{alert.finding_id}] {alert.severity.upper()} - {alert.title}\n"
                f"Kanit: {alert.evidence_summary}\n"
                f"Neden: {alert.explanation}\n"
                f"Oneri: {alert.recommendation}"
            )
        hidden = len(self.active_alerts) - output_limit
        if hidden > 0:
            lines.append(f"... {hidden} ek bakim adayi gosterilmedi.")
        lines.append(
            "Uyari, otomatik kod degisikligi degildir. Duzeltme icin bulgu kimligiyle "
            "taslak hazirlanmali ve kullanici ayrica onay vermelidir."
        )
        return "\n\n".join(lines)


class MaintenanceAdvisor:
    """Deduplicate evidence-backed maintenance alerts and notify without acting."""

    def __init__(
        self,
        path: str | Path,
        notifications: NotificationStore | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.notifications = notifications
        self._lock = threading.RLock()
        self.last_review: MaintenanceReview | None = None

    @staticmethod
    def _quarantine(path: Path) -> None:
        if not path.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = path.with_name(f"{path.stem}.corrupt_{stamp}{path.suffix}")
        try:
            os.replace(path, target)
        except OSError:
            pass

    def _load_state(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            payload = read_json_object(self.path, max_bytes=_MAX_BYTES)
            if payload.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("Desteklenmeyen bakim durumu surumu.")
            raw = payload.get("findings")
            if not isinstance(raw, Mapping):
                raise ValueError("Bakim bulgulari nesne olmali.")
            result: dict[str, dict[str, str]] = {}
            for raw_id, raw_row in raw.items():
                finding_id = str(raw_id or "").strip().upper()[:64]
                if not finding_id or not isinstance(raw_row, Mapping):
                    continue
                result[finding_id] = {
                    "signature": str(raw_row.get("signature", ""))[:64],
                    "last_notified": str(raw_row.get("last_notified", ""))[:64],
                    "acknowledged_signature": str(
                        raw_row.get("acknowledged_signature", "")
                    )[:64],
                }
            return result
        except (OSError, UnicodeError, ValueError, TypeError):
            self._quarantine(self.path)
            return {}

    def _save_state(self, state: dict[str, dict[str, str]]) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "findings": dict(sorted(state.items())),
        }
        atomic_write_json(self.path, payload, max_bytes=_MAX_BYTES)

    @staticmethod
    def _runtime_alert(finding: RuntimeFinding) -> MaintenanceAlert | None:
        # Cancellations and warning fallbacks are diagnostic signals, not safe
        # autonomous repair targets. They remain visible in an explicit health
        # report, but must not nag the user with a misleading "fix this RUN"
        # prompt. Slow-operation alerts require more evidence than three samples.
        if finding.category in {"repeated_cancellation", "repeated_runtime_warning"}:
            return None
        if finding.category == "repeated_slow_operation" and finding.occurrence_count < 5:
            return None
        if finding.severity not in {"critical", "high", "medium"}:
            return None
        if finding.severity == "medium" and finding.occurrence_count < 3:
            return None
        evidence = f"{finding.occurrence_count} tekrar; son olay {finding.last_seen}"
        signature = _digest(
            finding.finding_id,
            finding.severity,
            _bucket(finding.occurrence_count),
            finding.affected_paths,
            finding.affected_symbols,
        )
        return MaintenanceAlert(
            finding_id=finding.finding_id,
            source="runtime",
            severity=finding.severity,
            title=finding.title,
            explanation=finding.explanation,
            evidence_summary=evidence,
            recommendation=finding.recommendation,
            workspace=finding.workspace,
            signature=signature,
        )

    @staticmethod
    def _architecture_alert(assessment: ProjectImprovementAssessment, index: int) -> MaintenanceAlert | None:
        finding = assessment.findings[index]
        if finding.severity not in {"critical", "high"}:
            return None
        evidence_rows = tuple(
            f"{item.location}: {item.detail}" for item in finding.evidence[:3]
        )
        evidence = "; ".join(evidence_rows) or "yerel statik mimari kaniti"
        signature = _digest(
            finding.finding_id,
            finding.severity,
            finding.category,
            finding.affected_paths,
            evidence_rows,
        )
        return MaintenanceAlert(
            finding_id=finding.finding_id,
            source="architecture",
            severity=finding.severity,
            title=finding.title,
            explanation=finding.explanation,
            evidence_summary=evidence,
            recommendation=finding.recommendation,
            workspace=assessment.root,
            signature=signature,
        )

    def evaluate(
        self,
        runtime_report: RuntimeHealthReport,
        *,
        architecture_assessment: ProjectImprovementAssessment | None = None,
        notify: bool = True,
    ) -> MaintenanceReview:
        alerts: list[MaintenanceAlert] = []
        for finding in runtime_report.findings:
            alert = self._runtime_alert(finding)
            if alert is not None:
                alerts.append(alert)
        if architecture_assessment is not None:
            for index in range(len(architecture_assessment.findings)):
                alert = self._architecture_alert(architecture_assessment, index)
                if alert is not None:
                    alerts.append(alert)
        alerts.sort(
            key=lambda item: (
                _SEVERITY_ORDER.get(item.severity, 9),
                item.source,
                item.title.casefold(),
                item.finding_id,
            )
        )

        with self._lock:
            state = self._load_state()
            new_alerts: list[MaintenanceAlert] = []
            now = _now_iso()
            for alert in alerts:
                row = state.get(alert.finding_id, {})
                previous_signature = str(row.get("signature", ""))
                acknowledged = str(row.get("acknowledged_signature", ""))
                if alert.signature != previous_signature and alert.signature != acknowledged:
                    new_alerts.append(alert)
                    if notify and self.notifications is not None:
                        try:
                            level = "error" if alert.severity in {"critical", "high"} else "warning"
                            self.notifications.append(
                                f"[{alert.finding_id}] {alert.title}. {alert.evidence_summary}. "
                                "Kod degisikligi icin ayrica onay gerekir.",
                                level=level,
                            )
                        except Exception:
                            pass
                    row["last_notified"] = now
                row["signature"] = alert.signature
                row.setdefault("acknowledged_signature", acknowledged)
                state[alert.finding_id] = row
            active_ids = {alert.finding_id for alert in alerts}
            for finding_id in list(state):
                if finding_id not in active_ids and not state[finding_id].get("acknowledged_signature"):
                    state.pop(finding_id, None)
            self._save_state(state)

        review = MaintenanceReview(
            generated_at=_now_iso(),
            runtime_report=runtime_report,
            active_alerts=tuple(alerts),
            new_alerts=tuple(new_alerts),
        )
        self.last_review = review
        return review

    def acknowledge(self, finding_id: str) -> bool:
        key = str(finding_id or "").strip().upper()
        if not key:
            return False
        with self._lock:
            state = self._load_state()
            row = state.get(key)
            if row is None or not row.get("signature"):
                return False
            row["acknowledged_signature"] = row["signature"]
            state[key] = row
            self._save_state(state)
            return True
