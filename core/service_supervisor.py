from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import sys
from threading import Event, RLock, Thread, current_thread
from time import monotonic
from typing import Callable

from artmach_assistant.core.service_status import service_status_registry


_MAX_SERVICE_NAME = 256


def _safe_text(value: object, *, default: str = "", limit: int = _MAX_SERVICE_NAME) -> str:
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        text = default
    text = text.replace("\x00", "").strip()
    return text[:limit]


def _status_call(method: str, *args: object, **kwargs: object) -> None:
    try:
        callback = getattr(service_status_registry, method)
        callback(*args, **kwargs)
    except Exception:
        pass


def _bounded_seconds(value: object, *, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not isfinite(number):
        return default
    return min(maximum, max(minimum, number))


@dataclass
class SupervisedService:
    name: str
    is_running: Callable[[], bool]
    restart: Callable[[], None]
    enabled: Callable[[], bool]
    failures: int = 0
    next_retry_at: float = 0.0


class ServiceSupervisor:
    """Restarts unexpectedly stopped background services with bounded backoff."""

    def __init__(self, *, check_interval: float = 1.0, max_backoff: float = 30.0) -> None:
        self._check_interval = _bounded_seconds(
            check_interval, default=1.0, minimum=0.25, maximum=3600.0
        )
        self._max_backoff = _bounded_seconds(
            max_backoff, default=30.0, minimum=2.0, maximum=3600.0
        )
        self._services: list[SupervisedService] = []
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._lock = RLock()
        _status_call("ensure", "service_supervisor")

    @property
    def is_running(self) -> bool:
        with self._lock:
            thread = self._thread
            return bool(thread and thread.is_alive())

    def register(
        self,
        name: str,
        *,
        is_running: Callable[[], bool],
        restart: Callable[[], None],
        enabled: Callable[[], bool],
    ) -> None:
        clean_name = _safe_text(name)
        if not clean_name:
            raise ValueError("Servis adı boş olamaz.")
        if not callable(is_running) or not callable(restart) or not callable(enabled):
            raise TypeError("Servis callback değerlerinin tamamı çağrılabilir olmalıdır.")
        _status_call("ensure", clean_name)
        with self._lock:
            for index, current in enumerate(self._services):
                if current.name.casefold() == clean_name.casefold():
                    self._services[index] = SupervisedService(
                        clean_name, is_running, restart, enabled
                    )
                    return
            self._services.append(SupervisedService(clean_name, is_running, restart, enabled))

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run, name="JarvisServiceSupervisor", daemon=True
            )
            self._thread.start()
        _status_call("set_state", "service_supervisor", "idle", "Servis gözetmeni hazır.")

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=max(2.0, min(self._check_interval * 3.0, 5.0)))
        with self._lock:
            if thread and thread.is_alive():
                _status_call(
                    "set_state",
                    "service_supervisor",
                    "stopping",
                    "Servis gözetmeni çalışan kontrolün tamamlanmasını bekliyor.",
                )
                return
            if self._thread is thread:
                self._thread = None
            for service in self._services:
                service.failures = 0
                service.next_retry_at = 0.0
            self._stop_event.clear()
        _status_call(
            "set_state", "service_supervisor", "stopped", "Servis gözetmeni durduruldu."
        )

    def _services_snapshot(self) -> tuple[SupervisedService, ...]:
        with self._lock:
            return tuple(self._services)

    def _mark_healthy(self, service: SupervisedService) -> bool:
        with self._lock:
            recovered = service.failures > 0
            service.failures = 0
            service.next_retry_at = 0.0
            return recovered

    # Kept as the stable lifecycle name used by the runtime integration tests
    # and external supervisor adapters.
    def _mark_running(self, service: SupervisedService) -> bool:
        return self._mark_healthy(service)

    def _all_services_healthy(self) -> bool:
        with self._lock:
            return all(service.failures == 0 for service in self._services)

    def _restore_supervisor_idle_if_healthy(self) -> None:
        if self._all_services_healthy():
            try:
                active_module = sys.modules.get(__name__)
                registry = getattr(
                    active_module, "service_status_registry", service_status_registry
                )
                registry.set_state(
                    "service_supervisor", "idle", "Servisler sağlıklı."
                )
            except Exception:
                pass

    def _mark_failed(self, service: SupervisedService) -> float:
        with self._lock:
            service.failures += 1
            delay = min(self._max_backoff, float(2 ** min(service.failures, 5)))
            service.next_retry_at = monotonic() + delay
            return delay

    def _record_failure(self, service: SupervisedService, exc: BaseException, action: str) -> None:
        delay = self._mark_failed(service)
        _status_call("failed", service.name, exc, 0)
        _status_call(
            "set_state",
            "service_supervisor",
            "error",
            f"{service.name} {action}; {delay:.0f} saniye sonra tekrar denenecek.",
            recovering_service=service.name,
            retry_delay_seconds=delay,
        )

    def _run(self) -> None:
        worker = current_thread()
        stopped = False
        try:
            while not self._stop_event.wait(self._check_interval):
                now = monotonic()
                for service in self._services_snapshot():
                    if self._stop_event.is_set():
                        break
                    with self._lock:
                        retry_at = service.next_retry_at
                    if now < retry_at:
                        continue
                    try:
                        is_enabled = bool(service.enabled())
                    except BaseException as exc:
                        self._record_failure(service, exc, "etkinlik kontrolü başarısız")
                        continue
                    if not is_enabled:
                        recovered = self._mark_healthy(service)
                        if recovered:
                            _status_call(
                                "recovered",
                                service.name,
                                f"{service.name} devre dışı bırakıldı; yeniden başlatma beklenmiyor.",
                            )
                        _status_call("set_state", service.name, "stopped", f"{service.name} devre dışı.")
                        self._restore_supervisor_idle_if_healthy()
                        continue
                    try:
                        running = bool(service.is_running())
                    except BaseException as exc:
                        self._record_failure(service, exc, "durum kontrolü başarısız")
                        continue
                    if running:
                        if self._mark_healthy(service):
                            _status_call(
                                "recovered", service.name, f"{service.name} otomatik olarak yeniden çalıştırıldı."
                            )
                            self._restore_supervisor_idle_if_healthy()
                        continue
                    try:
                        _status_call(
                            "set_state",
                            "service_supervisor",
                            "running",
                            f"{service.name} yeniden başlatılıyor.",
                            recovering_service=service.name,
                        )
                        service.restart()
                        if not bool(service.is_running()):
                            raise RuntimeError("Yeniden başlatma sonrasında servis çalışır duruma geçmedi.")
                        self._mark_healthy(service)
                        _status_call(
                            "recovered", service.name, f"{service.name} otomatik olarak yeniden çalıştırıldı."
                        )
                        self._restore_supervisor_idle_if_healthy()
                    except BaseException as exc:
                        self._record_failure(service, exc, "yeniden başlatılamadı")
        finally:
            stopped = self._stop_event.is_set()
            with self._lock:
                if self._thread is worker:
                    self._thread = None
                if stopped:
                    for service in self._services:
                        service.failures = 0
                        service.next_retry_at = 0.0
                    self._stop_event.clear()
            if stopped:
                _status_call(
                    "set_state", "service_supervisor", "stopped", "Servis gözetmeni durduruldu."
                )
