from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

from artmach_assistant.core.acceptance_trace import trace_event


class OperationCancelled(RuntimeError):
    """Raised when the user cancels the active operation."""


@dataclass(frozen=True, slots=True)
class OperationSnapshot:
    name: str
    phase: str
    current: int
    total: int
    detail: str
    started_at: str
    active: bool
    cancelled: bool

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return max(0, min(100, int((self.current / self.total) * 100)))

    def report(self) -> str:
        if not self.active:
            return "Şu anda çalışan uzun bir işlem yok."
        suffix = f" %{self.percent}" if self.total > 0 else ""
        progress = f" ({self.current}/{self.total})" if self.total > 0 else ""
        detail = f" Son adım: {self.detail}." if self.detail else ""
        state = "İptal ediliyor" if self.cancelled else "Devam ediyor"
        return f"{self.name}: {self.phase}{progress}{suffix}. {state}.{detail}"


class OperationController:
    """Thread-safe progress and cancellation state for one active operation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._name = ""
        self._phase = ""
        self._current = 0
        self._total = 0
        self._detail = ""
        self._started_at = ""
        self._active = False
        self._cancelled = False

    def start(self, name: str, *, phase: str = "Hazırlanıyor", total: int = 0) -> None:
        with self._lock:
            self._name = str(name).strip() or "İşlem"
            self._phase = str(phase).strip() or "Hazırlanıyor"
            self._current = 0
            self._total = max(0, int(total))
            self._detail = ""
            self._started_at = datetime.now().astimezone().isoformat()
            self._active = True
            self._cancelled = False
            trace_event(
                "OPERATION_STARTED",
                operation_name=self._name,
                phase=self._phase,
                total=self._total,
            )

    def update(
        self,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            if not self._active:
                return
            if phase is not None:
                self._phase = str(phase).strip() or self._phase
            if current is not None:
                self._current = max(0, int(current))
            if total is not None:
                self._total = max(0, int(total))
            if detail is not None:
                self._detail = str(detail).strip()
            trace_event(
                "OPERATION_UPDATED",
                operation_name=self._name,
                phase=self._phase,
                current=self._current,
                total=self._total,
                detail=self._detail[:500],
            )

    def cancel(self) -> bool:
        with self._lock:
            if not self._active:
                return False
            self._cancelled = True
            trace_event(
                "OPERATION_CANCEL_REQUESTED",
                operation_name=self._name,
                phase=self._phase,
            )
            return True

    def checkpoint(self) -> None:
        with self._lock:
            if self._cancelled:
                raise OperationCancelled("İşlem kullanıcı tarafından iptal edildi.")

    def finish(self, *, detail: str = "") -> None:
        with self._lock:
            if detail:
                self._detail = str(detail).strip()
            self._active = False
            trace_event(
                "OPERATION_FINISHED",
                operation_name=self._name,
                phase=self._phase,
                current=self._current,
                total=self._total,
                detail=self._detail[:500],
                cancelled=self._cancelled,
            )

    def snapshot(self) -> OperationSnapshot:
        with self._lock:
            return OperationSnapshot(
                name=self._name,
                phase=self._phase,
                current=self._current,
                total=self._total,
                detail=self._detail,
                started_at=self._started_at,
                active=self._active,
                cancelled=self._cancelled,
            )
