from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any


def _non_negative_count(value: object) -> int:
    if type(value) is not int:
        return 0
    return max(0, value)


def _safe_text(value: object, *, fallback: str = "", limit: int = 4096) -> str:
    try:
        text = str(value)
    except Exception:
        text = fallback
    if len(text) > limit:
        return text[:limit]
    return text


def _safe_name(value: object) -> str:
    name = _safe_text(value, fallback="unknown_service", limit=256).strip()
    return name or "unknown_service"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class ServiceStatus:
    name: str
    state: str = "stopped"
    queued: int = 0
    processed: int = 0
    failed: int = 0
    last_started_at: str = ""
    last_finished_at: str = ""
    last_error: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class ServiceStatusRegistry:
    _TERMINAL_STATES = frozenset({"idle", "stopped"})
    _TRANSIENT_DETAIL_KEYS = frozenset(
        {"recovering_service", "retry_delay_seconds", "retry_at", "error_type"}
    )

    def __init__(self) -> None:
        self._lock = RLock()
        self._services: dict[str, ServiceStatus] = {}

    def ensure(self, name: str) -> None:
        clean_name = _safe_name(name)
        with self._lock:
            self._services.setdefault(clean_name, ServiceStatus(name=clean_name))

    def set_state(self, name: str, state: str, message: str = "", **details: Any) -> None:
        clean_name = _safe_name(name)
        clean_state = _safe_text(state, fallback="error", limit=64).strip().casefold() or "error"
        clean_message = _safe_text(message)
        with self._lock:
            item = self._services.setdefault(clean_name, ServiceStatus(name=clean_name))
            item.state = clean_state
            item.message = clean_message
            if details:
                item.details.update(details)
            if clean_state in self._TERMINAL_STATES:
                item.last_error = ""
                for key in self._TRANSIENT_DETAIL_KEYS:
                    item.details.pop(key, None)
            if clean_state == "running":
                item.last_started_at = _now_iso()
            elif clean_state in {"idle", "stopped", "error"}:
                item.last_finished_at = _now_iso()

    def queued(self, name: str, amount: int = 1) -> None:
        clean_name = _safe_name(name)
        with self._lock:
            item = self._services.setdefault(clean_name, ServiceStatus(name=clean_name))
            item.queued += _non_negative_count(amount)

    @staticmethod
    def _queue_state(item: ServiceStatus) -> str:
        return "running" if item.queued > 0 else "idle"

    def discarded(self, name: str, amount: int = 1, message: str = "") -> None:
        """Remove abandoned queued work without counting it as success or failure."""
        clean_name = _safe_name(name)
        with self._lock:
            item = self._services.setdefault(clean_name, ServiceStatus(name=clean_name))
            count = _non_negative_count(amount)
            item.queued = max(0, item.queued - count)
            item.state = self._queue_state(item)
            item.last_finished_at = _now_iso()
            if message:
                item.message = _safe_text(message)

    def completed(self, name: str, amount: int = 1, message: str = "") -> None:
        clean_name = _safe_name(name)
        with self._lock:
            item = self._services.setdefault(clean_name, ServiceStatus(name=clean_name))
            count = _non_negative_count(amount)
            item.queued = max(0, item.queued - count)
            item.processed += count
            item.last_error = ""
            item.last_finished_at = _now_iso()
            item.state = self._queue_state(item)
            for key in self._TRANSIENT_DETAIL_KEYS:
                item.details.pop(key, None)
            if message:
                item.message = _safe_text(message)

    def recovered(self, name: str, message: str = "") -> None:
        clean_name = _safe_name(name)
        with self._lock:
            item = self._services.setdefault(clean_name, ServiceStatus(name=clean_name))
            item.state = self._queue_state(item)
            item.last_error = ""
            item.last_finished_at = _now_iso()
            for key in self._TRANSIENT_DETAIL_KEYS:
                item.details.pop(key, None)
            previous = item.details.get("recovery_count", 0)
            item.details["recovery_count"] = _non_negative_count(previous) + 1
            if message:
                item.message = _safe_text(message)

    def failed(self, name: str, error: BaseException | str, amount: int = 1) -> None:
        clean_name = _safe_name(name)
        error_text = _safe_text(error, fallback=type(error).__name__)
        with self._lock:
            item = self._services.setdefault(clean_name, ServiceStatus(name=clean_name))
            count = _non_negative_count(amount)
            item.queued = max(0, item.queued - count)
            item.failed += count
            item.last_error = error_text
            item.last_finished_at = _now_iso()
            item.state = "error"
            item.message = error_text
            item.details["error_type"] = (
                type(error).__name__ if isinstance(error, BaseException) else "str"
            )

    def snapshot(self, name: str | None = None) -> dict[str, Any]:
        with self._lock:
            if name is not None:
                clean_name = _safe_name(name)
                item = self._services.setdefault(clean_name, ServiceStatus(name=clean_name))
                return asdict(item)
            return {key: asdict(value) for key, value in sorted(self._services.items())}


service_status_registry = ServiceStatusRegistry()
