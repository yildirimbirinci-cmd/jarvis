from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

_SCHEMA_VERSION = 1
_DEFAULT_KEEP = 1200
_MAX_KEEP = 5000
_MAX_STORE_BYTES = 8 * 1024 * 1024
_ALLOWED_STATUSES = {"completed", "failed", "cancelled", "warning"}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}")
_NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redact(value: object, *, limit: int) -> str:
    text = _WHITESPACE_PATTERN.sub(" ", str(value or "")).strip()
    text = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _BEARER_PATTERN.sub("Bearer <redacted>", text)
    return text[:limit]


def _finite_number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _clean_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    result: dict[str, object] = {}
    if not isinstance(metadata, Mapping):
        return result
    for raw_key, raw_value in list(metadata.items())[:32]:
        key = _redact(raw_key, limit=80)
        if not key:
            continue
        secret_key = key.casefold().replace("-", "_")
        if any(
            marker in secret_key
            for marker in ("password", "passwd", "token", "secret", "api_key", "authorization")
        ):
            result[key] = "<redacted>"
        elif isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
            result[key] = max(-(2**63), min(int(raw_value), 2**63 - 1))
        elif isinstance(raw_value, float):
            number = _finite_number(raw_value)
            result[key] = number
        elif raw_value is None:
            result[key] = ""
        else:
            result[key] = _redact(raw_value, limit=500)
    return result


def _event_fingerprint(
    component: str,
    action: str,
    status: str,
    error_type: str,
    source_path: str,
    symbol: str,
    message: str,
) -> str:
    normalized_message = _NUMBER_PATTERN.sub("#", message.casefold())
    digest = hashlib.sha256()
    for value in (
        component.casefold(),
        action.casefold(),
        status.casefold(),
        error_type.casefold(),
        source_path.casefold(),
        symbol.casefold(),
        normalized_message[:800],
    ):
        digest.update(value.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: str
    created_at: str
    component: str
    action: str
    status: str
    duration_ms: float = 0.0
    workspace: str = ""
    scope: str = "runtime"
    source_path: str = ""
    symbol: str = ""
    message: str = ""
    error_type: str = ""
    fingerprint: str = ""
    correlation_id: str = ""
    metadata: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata or {})
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeEvidence:
    event_id: str
    created_at: str
    detail: str
    duration_ms: float = 0.0
    source_path: str = ""
    symbol: str = ""
    action_duration_ms: float = 0.0
    wrapper_overhead_ms: float = 0.0
    action_started: bool = False
    action_completed: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeFinding:
    finding_id: str
    severity: str
    category: str
    title: str
    explanation: str
    confidence: float
    occurrence_count: int
    last_seen: str
    workspace: str
    scope: str
    affected_paths: tuple[str, ...]
    affected_symbols: tuple[str, ...]
    evidence: tuple[RuntimeEvidence, ...]
    recommendation: str
    acceptance_criteria: tuple[str, ...]
    research_query: str

    def to_improvement_finding(self):
        from artmach_assistant.core.project_improvement_service import (
            ImprovementEvidence,
            ImprovementFinding,
        )

        evidence = tuple(
            ImprovementEvidence(
                source="runtime_observability",
                path=item.source_path,
                line=0,
                detail=item.detail,
                metric=(
                    f"duration_ms={item.duration_ms:.2f}"
                    if item.duration_ms > 0 else self.category
                ),
            )
            for item in self.evidence
        )
        return ImprovementFinding(
            finding_id=self.finding_id,
            severity=self.severity,
            category=self.category,
            title=self.title,
            explanation=self.explanation,
            confidence=self.confidence,
            evidence=evidence,
            affected_paths=self.affected_paths,
            recommendation=self.recommendation,
            acceptance_criteria=self.acceptance_criteria,
            research_query=self.research_query,
        )


@dataclass(frozen=True, slots=True)
class RuntimeHealthReport:
    generated_at: str
    workspace: str
    lookback_hours: int
    event_count: int
    completed_count: int
    failed_count: int
    cancelled_count: int
    warning_count: int
    findings: tuple[RuntimeFinding, ...]

    @property
    def healthy(self) -> bool:
        return not any(item.severity in {"critical", "high"} for item in self.findings)

    def finding(self, finding_id: str) -> RuntimeFinding | None:
        key = str(finding_id or "").strip().upper()
        return next((item for item in self.findings if item.finding_id.upper() == key), None)

    def report(self, *, limit: int = 12) -> str:
        output_limit = max(1, min(int(limit), 30))
        lines = [
            "CALISMA ZAMANI SAGLIK RAPORU",
            f"Calisma alani: {self.workspace or 'tum yerel olaylar'}",
            (
                f"Son {self.lookback_hours} saat: {self.event_count} olay, "
                f"{self.failed_count} hata, {self.cancelled_count} iptal, "
                f"{self.warning_count} uyari"
            ),
        ]
        if not self.findings:
            lines.append(
                "Tekrarlanan hata, iptal veya yavaslik esigini asan kanitlanmis bir bulgu yok."
            )
        for finding in self.findings[:output_limit]:
            location = ", ".join(finding.affected_paths[:3]) or "dosya baglantisi henuz yok"
            lines.append(
                f"[{finding.finding_id}] {finding.severity.upper()} - {finding.title}\n"
                f"Kanit: {finding.occurrence_count} tekrar; {location}\n"
                f"Neden: {finding.explanation}\n"
                f"Oneri: {finding.recommendation}"
            )
        hidden = len(self.findings) - output_limit
        if hidden > 0:
            lines.append(f"... {hidden} ek calisma zamani bulgusu gosterilmedi.")
        lines.append(
            "Bu rapor dosya degistirmez. Bir RUN kimligini duzeltmek icin ayrica acik onayli taslak gerekir."
        )
        return "\n\n".join(lines)


class RuntimeEventStore:
    """Bounded, local and privacy-aware structured runtime event store."""

    def __init__(
        self,
        path: str | Path,
        *,
        keep: int = _DEFAULT_KEEP,
        max_bytes: int = _MAX_STORE_BYTES,
    ) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.keep = max(50, min(int(keep), _MAX_KEEP))
        self.max_bytes = max(64 * 1024, min(int(max_bytes), 32 * 1024 * 1024))
        self._lock = threading.RLock()

    def _quarantine_corrupt(self) -> None:
        if not self.path.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = self.path.with_name(f"{self.path.stem}.corrupt_{stamp}{self.path.suffix}")
        try:
            os.replace(self.path, target)
        except OSError:
            pass

    @staticmethod
    def _from_mapping(raw: Mapping[str, object]) -> RuntimeEvent:
        status = _redact(raw.get("status", ""), limit=24).casefold()
        if status not in _ALLOWED_STATUSES:
            raise ValueError("invalid runtime event status")
        event_id = _redact(raw.get("event_id", ""), limit=64)
        created_at = _redact(raw.get("created_at", ""), limit=64)
        component = _redact(raw.get("component", ""), limit=160)
        action = _redact(raw.get("action", ""), limit=160)
        if not event_id or not created_at or not component or not action:
            raise ValueError("runtime event is missing required fields")
        metadata_raw = raw.get("metadata", {})
        return RuntimeEvent(
            event_id=event_id,
            created_at=created_at,
            component=component,
            action=action,
            status=status,
            duration_ms=max(0.0, _finite_number(raw.get("duration_ms", 0.0))),
            workspace=_redact(raw.get("workspace", ""), limit=1000),
            scope=_redact(raw.get("scope", "runtime"), limit=80) or "runtime",
            source_path=_redact(raw.get("source_path", ""), limit=1000).replace("\\", "/"),
            symbol=_redact(raw.get("symbol", ""), limit=500),
            message=_redact(raw.get("message", ""), limit=2000),
            error_type=_redact(raw.get("error_type", ""), limit=300),
            fingerprint=_redact(raw.get("fingerprint", ""), limit=64),
            correlation_id=_redact(raw.get("correlation_id", ""), limit=64),
            metadata=_clean_metadata(metadata_raw if isinstance(metadata_raw, Mapping) else None),
        )

    def load(self) -> tuple[RuntimeEvent, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = read_json_object(self.path, max_bytes=self.max_bytes)
            if payload.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("unsupported runtime event schema")
            rows = payload.get("events")
            if not isinstance(rows, list):
                raise ValueError("runtime event rows must be a list")
            events: list[RuntimeEvent] = []
            for row in rows[-self.keep :]:
                if not isinstance(row, Mapping):
                    continue
                try:
                    events.append(self._from_mapping(row))
                except (TypeError, ValueError, OverflowError):
                    continue
            return tuple(events)
        except (OSError, UnicodeError, ValueError, TypeError):
            self._quarantine_corrupt()
            return ()

    def _write(self, events: list[RuntimeEvent]) -> None:
        rows = [event.to_dict() for event in events[-self.keep :]]
        while rows:
            payload = {"schema_version": _SCHEMA_VERSION, "events": rows}
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) <= self.max_bytes:
                atomic_write_json(self.path, payload, max_bytes=self.max_bytes)
                return
            remove_count = max(1, len(rows) // 10)
            rows = rows[remove_count:]
        atomic_write_json(
            self.path,
            {"schema_version": _SCHEMA_VERSION, "events": []},
            max_bytes=self.max_bytes,
        )

    def record(
        self,
        *,
        component: str,
        action: str,
        status: str,
        duration_ms: float = 0.0,
        workspace: str | Path = "",
        scope: str = "runtime",
        source_path: str = "",
        symbol: str = "",
        message: str = "",
        error: BaseException | None = None,
        error_type: str = "",
        correlation_id: str = "",
        metadata: Mapping[str, object] | None = None,
    ) -> RuntimeEvent:
        clean_status = _redact(status, limit=24).casefold()
        if clean_status not in _ALLOWED_STATUSES:
            raise ValueError(f"invalid runtime event status: {status!r}")
        clean_component = _redact(component, limit=160)
        clean_action = _redact(action, limit=160)
        if not clean_component or not clean_action:
            raise ValueError("component and action are required")
        clean_message = _redact(message or (str(error) if error is not None else ""), limit=2000)
        clean_error_type = _redact(
            error_type or (type(error).__name__ if error is not None else ""),
            limit=300,
        )
        clean_path = _redact(source_path, limit=1000).replace("\\", "/")
        clean_symbol = _redact(symbol, limit=500)
        event = RuntimeEvent(
            event_id=uuid.uuid4().hex,
            created_at=_now_iso(),
            component=clean_component,
            action=clean_action,
            status=clean_status,
            duration_ms=max(0.0, _finite_number(duration_ms)),
            workspace=_redact(workspace, limit=1000),
            scope=_redact(scope, limit=80) or "runtime",
            source_path=clean_path,
            symbol=clean_symbol,
            message=clean_message,
            error_type=clean_error_type,
            fingerprint=_event_fingerprint(
                clean_component,
                clean_action,
                clean_status,
                clean_error_type,
                clean_path,
                clean_symbol,
                clean_message,
            ),
            correlation_id=_redact(correlation_id, limit=64),
            metadata=_clean_metadata(metadata),
        )
        with self._lock:
            events = list(self.load())
            events.append(event)
            self._write(events)
        return event

    @contextmanager
    def observe(
        self,
        *,
        component: str,
        action: str,
        workspace: str | Path = "",
        scope: str = "runtime",
        source_path: str = "",
        symbol: str = "",
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[str]:
        correlation_id = uuid.uuid4().hex
        started = time.perf_counter()
        try:
            yield correlation_id
        except BaseException as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            try:
                self.record(
                    component=component,
                    action=action,
                    status="failed",
                    duration_ms=duration_ms,
                    workspace=workspace,
                    scope=scope,
                    source_path=source_path,
                    symbol=symbol,
                    error=exc,
                    correlation_id=correlation_id,
                    metadata=metadata,
                )
            except Exception:
                pass
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000.0
            try:
                self.record(
                    component=component,
                    action=action,
                    status="completed",
                    duration_ms=duration_ms,
                    workspace=workspace,
                    scope=scope,
                    source_path=source_path,
                    symbol=symbol,
                    correlation_id=correlation_id,
                    metadata=metadata,
                )
            except Exception:
                pass

    def recent(self, *, limit: int = 500, workspace: str | Path = "") -> tuple[RuntimeEvent, ...]:
        events = self.load()
        workspace_key = str(workspace or "").strip().casefold()
        if workspace_key:
            events = tuple(
                event for event in events
                if event.workspace.strip().casefold() == workspace_key
            )
        return tuple(events[-max(1, min(int(limit), self.keep)) :])


class RuntimeHealthAnalyzer:
    """Turn structured local runtime evidence into bounded maintenance findings."""

    def __init__(
        self,
        store: RuntimeEventStore,
        *,
        slow_threshold_ms: float = 5000.0,
        minimum_failure_count: int = 2,
        minimum_slow_count: int = 3,
        minimum_warning_count: int = 3,
    ) -> None:
        self.store = store
        self.slow_threshold_ms = max(100.0, _finite_number(slow_threshold_ms, default=5000.0))
        self.minimum_failure_count = max(2, int(minimum_failure_count))
        self.minimum_slow_count = max(2, int(minimum_slow_count))
        self.minimum_warning_count = max(2, int(minimum_warning_count))
        self.last_report: RuntimeHealthReport | None = None

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _finding_id(*parts: object) -> str:
        digest = hashlib.sha256()
        for part in parts:
            digest.update(str(part).encode("utf-8", errors="replace"))
            digest.update(b"\0")
        return "RUN-" + digest.hexdigest()[:10].upper()

    @staticmethod
    def _paths(events: list[RuntimeEvent]) -> tuple[str, ...]:
        values: list[str] = []
        for event in events:
            if event.source_path and event.source_path not in values:
                values.append(event.source_path)
            raw_paths = (event.metadata or {}).get("affected_paths", "")
            for raw in str(raw_paths).split(";"):
                path = raw.strip().replace("\\", "/")
                if path and path not in values:
                    values.append(path)
        return tuple(values[:12])

    @staticmethod
    def _symbols(events: list[RuntimeEvent]) -> tuple[str, ...]:
        values: list[str] = []
        for event in events:
            if event.symbol and event.symbol not in values:
                values.append(event.symbol)
        return tuple(values[:12])

    @staticmethod
    def _evidence(events: list[RuntimeEvent], *, limit: int = 8) -> tuple[RuntimeEvidence, ...]:
        rows: list[RuntimeEvidence] = []
        for event in events[-limit:]:
            detail = event.message or (
                f"{event.component}.{event.action} durumu: {event.status}"
            )
            rows.append(
                RuntimeEvidence(
                    event_id=event.event_id,
                    created_at=event.created_at,
                    detail=detail,
                    duration_ms=event.duration_ms,
                    source_path=event.source_path,
                    symbol=event.symbol,
                    action_duration_ms=_finite_number(
                        (event.metadata or {}).get("action_duration_ms", 0.0),
                        default=0.0,
                    ),
                    wrapper_overhead_ms=_finite_number(
                        (event.metadata or {}).get("wrapper_overhead_ms", 0.0),
                        default=0.0,
                    ),
                    action_started=bool(
                        (event.metadata or {}).get("action_started", False)
                    ),
                    action_completed=bool(
                        (event.metadata or {}).get("action_completed", False)
                    ),
                )
            )
        return tuple(rows)

    def _slow_threshold_for(self, event: RuntimeEvent) -> float:
        raw = (event.metadata or {}).get("slow_threshold_ms", self.slow_threshold_ms)
        threshold = _finite_number(raw, default=self.slow_threshold_ms)
        return max(100.0, min(threshold, 24 * 60 * 60 * 1000.0))

    @staticmethod
    def _expected_control_flow_event(event: RuntimeEvent) -> bool:
        metadata = event.metadata or {}
        if bool(metadata.get("health_excluded", False)):
            return True
        if event.component == "AssistantEngine" and event.action == "handle_command":
            return True

        message = event.message.casefold()
        if event.status == "cancelled":
            if bool(metadata.get("expected_cancellation", False)):
                return True
            if event.error_type == "InterruptedError":
                return True
            expected_markers = (
                "kullanıcı tarafından kesildi",
                "konuşma turu iptal edildi",
                "yeni konuşma turu",
                "eski konuşma turu",
                "seslendirme kullanıcı tarafından kesildi",
            )
            if any(marker in message for marker in expected_markers):
                return True
            if event.component == "VoiceService" and event.action in {
                "speech_turn", "speech_turn_fixed", "audio_capture",
                "audio_capture_fixed", "audio_output_playback", "tts_interrupt",
            }:
                # These methods are intentionally pre-empted by barge-in and
                # newer turns. A real device failure is recorded as failed,
                # not cancelled.
                return True

        if (
            event.status == "warning"
            and event.component == "LocalDialogueManager"
            and event.action == "intent_model"
            and (
                "kullanılabilir bir yanıt üretmedi" in message
                or "güvenli sohbet yoluna geçildi" in message
            )
        ):
            return True
        return False

    def analyze(
        self,
        *,
        workspace: str | Path = "",
        lookback_hours: int = 168,
        limit: int = 1200,
    ) -> RuntimeHealthReport:
        hours = max(1, min(int(lookback_hours), 24 * 90))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = [
            event for event in self.store.recent(limit=limit, workspace=workspace)
            if (self._parse_time(event.created_at) or cutoff) >= cutoff
            and not self._expected_control_flow_event(event)
        ]
        completed = [event for event in events if event.status == "completed"]
        failed = [event for event in events if event.status == "failed"]
        cancelled = [event for event in events if event.status == "cancelled"]
        warnings = [event for event in events if event.status == "warning"]
        findings: list[RuntimeFinding] = []

        failure_groups: dict[str, list[RuntimeEvent]] = {}
        for event in failed:
            failure_groups.setdefault(event.fingerprint, []).append(event)
        for fingerprint, rows in failure_groups.items():
            if len(rows) < self.minimum_failure_count:
                continue
            sample = rows[-1]
            paths = self._paths(rows)
            symbols = self._symbols(rows)
            severity = "high" if len(rows) >= 5 else "medium"
            error_label = sample.error_type or "calisma zamani hatasi"
            title = f"Tekrarlanan hata: {sample.component}.{sample.action}"
            findings.append(
                RuntimeFinding(
                    finding_id=self._finding_id("failure", fingerprint),
                    severity=severity,
                    category="repeated_runtime_failure",
                    title=title,
                    explanation=(
                        f"Ayni hata imzasi son {hours} saatte {len(rows)} kez olustu. "
                        f"Son hata turu: {error_label}."
                    ),
                    confidence=min(0.99, 0.76 + len(rows) * 0.04),
                    occurrence_count=len(rows),
                    last_seen=sample.created_at,
                    workspace=sample.workspace,
                    scope=sample.scope,
                    affected_paths=paths,
                    affected_symbols=symbols,
                    evidence=self._evidence(rows),
                    recommendation=(
                        "Once olay kaydindaki dosya ve sembol baglantisini dogrula; "
                        "ardindan en kucuk duzeltmeyi hazirla ve ayni senaryoyu test et."
                    ),
                    acceptance_criteria=(
                        "Ayni hata imzasi hedef senaryoda tekrar olusmamali.",
                        "Ilgili regresyon ve baslangic testleri gecmeli.",
                        "Duzeltme olay kaydinin isaret ettigi kapsamla sinirli kalmali.",
                    ),
                    research_query=(
                        f"{sample.component} {sample.action} {error_label} official documentation "
                        "diagnostics reliability testing"
                    ),
                )
            )

        warning_groups: dict[str, list[RuntimeEvent]] = {}
        for event in warnings:
            warning_groups.setdefault(event.fingerprint, []).append(event)
        for fingerprint, rows in warning_groups.items():
            if len(rows) < self.minimum_warning_count:
                continue
            sample = rows[-1]
            findings.append(
                RuntimeFinding(
                    finding_id=self._finding_id("warning", fingerprint),
                    severity="medium" if len(rows) >= 6 else "low",
                    category="repeated_runtime_warning",
                    title=f"Tekrarlanan uyarı: {sample.component}.{sample.action}",
                    explanation=(
                        f"Aynı çalışma zamanı uyarısı son {hours} saatte {len(rows)} kez oluştu. "
                        "İşlem tamamen çökmedi ancak beklenen ana yol yerine geri dönüş, "
                        "eksik sonuç veya azaltılmış işlev kullandı."
                    ),
                    confidence=min(0.94, 0.68 + len(rows) * 0.04),
                    occurrence_count=len(rows),
                    last_seen=sample.created_at,
                    workspace=sample.workspace,
                    scope=sample.scope,
                    affected_paths=self._paths(rows),
                    affected_symbols=self._symbols(rows),
                    evidence=self._evidence(rows),
                    recommendation=(
                        "Uyarının normal kullanıcı davranışı mı yoksa kalıcı geri dönüş yolu mı "
                        "olduğunu doğrula; tekrarlanan geri dönüşün kök nedenini hedefle."
                    ),
                    acceptance_criteria=(
                        "Ana çalışma yolu hedef senaryoda uyarı üretmeden tamamlanmalı.",
                        "Geri dönüş davranışı yalnızca gerçek hata durumunda kullanılmalı.",
                        "İlgili regresyon testleri ve çalışma zamanı ölçümü geçmeli.",
                    ),
                    research_query=(
                        f"{sample.component} {sample.action} warning fallback official "
                        "documentation diagnostics reliability testing"
                    ),
                )
            )

        slow_groups: dict[tuple[str, str, str], list[RuntimeEvent]] = {}
        slow_thresholds: dict[tuple[str, str, str], list[float]] = {}
        for event in completed:
            threshold = self._slow_threshold_for(event)
            if event.duration_ms < threshold:
                continue
            key = (event.component, event.action, event.scope)
            slow_groups.setdefault(key, []).append(event)
            slow_thresholds.setdefault(key, []).append(threshold)
        for key, rows in slow_groups.items():
            if len(rows) < self.minimum_slow_count:
                continue
            sample = rows[-1]
            durations = sorted(event.duration_ms for event in rows)
            median = durations[len(durations) // 2]
            thresholds = sorted(slow_thresholds.get(key, [self.slow_threshold_ms]))
            group_threshold = thresholds[len(thresholds) // 2]
            paths = self._paths(rows)
            symbols = self._symbols(rows)
            findings.append(
                RuntimeFinding(
                    finding_id=self._finding_id("slow", *key),
                    severity="medium" if median < group_threshold * 3 else "high",
                    category="repeated_slow_operation",
                    title=f"Tekrarlanan yavas islem: {sample.component}.{sample.action}",
                    explanation=(
                        f"Islem {len(rows)} kez kendi {group_threshold:.0f} ms esigini asti; "
                        f"ortanca sure {median:.0f} ms."
                    ),
                    confidence=min(0.96, 0.72 + len(rows) * 0.04),
                    occurrence_count=len(rows),
                    last_seen=sample.created_at,
                    workspace=sample.workspace,
                    scope=sample.scope,
                    affected_paths=paths,
                    affected_symbols=symbols,
                    evidence=self._evidence(rows),
                    recommendation=(
                        "Islemi asamalara ayir, sureyi hangi adimin tukettigini olc ve "
                        "yalnizca kanitlanan darbogazi optimize et."
                    ),
                    acceptance_criteria=(
                        "Ayni senaryonun ortanca suresi olculebilir bicimde dusmeli.",
                        "Cikti ve davranis korunmali.",
                        "Uzun islem iptal ve ilerleme bildirimini korumali.",
                    ),
                    research_query=(
                        f"{sample.component} {sample.action} performance profiling official "
                        "documentation latency optimization testing"
                    ),
                )
            )

        cancel_groups: dict[tuple[str, str, str], list[RuntimeEvent]] = {}
        for event in cancelled:
            cancel_groups.setdefault((event.component, event.action, event.scope), []).append(event)
        for key, rows in cancel_groups.items():
            if len(rows) < 3:
                continue
            sample = rows[-1]
            findings.append(
                RuntimeFinding(
                    finding_id=self._finding_id("cancelled", *key),
                    severity="medium",
                    category="repeated_cancellation",
                    title=f"Tekrarlanan iptal: {sample.component}.{sample.action}",
                    explanation=f"Ayni islem son {hours} saatte {len(rows)} kez iptal edildi.",
                    confidence=min(0.90, 0.68 + len(rows) * 0.04),
                    occurrence_count=len(rows),
                    last_seen=sample.created_at,
                    workspace=sample.workspace,
                    scope=sample.scope,
                    affected_paths=self._paths(rows),
                    affected_symbols=self._symbols(rows),
                    evidence=self._evidence(rows),
                    recommendation=(
                        "Iptalin kullanici tercihi mi, zaman asimi mi veya takilma mi oldugunu "
                        "olay metadatasiyla ayir; kanitlanan nedeni hedefle."
                    ),
                    acceptance_criteria=(
                        "Iptal nedeni ayirt edilebilir olmali.",
                        "Iptal sonrasi yarim durum birakilmamali.",
                        "Ayni beklenmeyen iptal senaryosu tekrar etmemeli.",
                    ),
                    research_query=(
                        f"{sample.component} {sample.action} cancellation timeout resilient "
                        "task orchestration official documentation"
                    ),
                )
            )

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings.sort(
            key=lambda item: (
                severity_order.get(item.severity, 9),
                -item.occurrence_count,
                item.title.casefold(),
                item.finding_id,
            )
        )
        report = RuntimeHealthReport(
            generated_at=_now_iso(),
            workspace=str(workspace or ""),
            lookback_hours=hours,
            event_count=len(events),
            completed_count=len(completed),
            failed_count=len(failed),
            cancelled_count=len(cancelled),
            warning_count=len(warnings),
            findings=tuple(findings[:120]),
        )
        self.last_report = report
        return report
