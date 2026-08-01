from __future__ import annotations

import math
from collections.abc import Callable
from threading import Event, Lock, Thread, current_thread

from artmach_assistant.core.service_status import service_status_registry


class IndexConsistencyService:
    """Periodically verifies that the live project index matches the watcher snapshot."""

    DEFAULT_INTERVAL_SECONDS = 15.0
    MIN_INTERVAL_SECONDS = 1.0
    MAX_INTERVAL_SECONDS = 24.0 * 60.0 * 60.0

    def __init__(self, reconcile: Callable[[], int], *, interval_seconds: float = 15.0) -> None:
        if not callable(reconcile):
            raise TypeError("reconcile must be callable")
        self._reconcile = reconcile
        self._interval_seconds = self._normalize_interval(interval_seconds)
        self._stop_event = Event()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None
        self._status_call("ensure", "index_consistency")

    @staticmethod
    def _status_call(method: str, *args: object, **kwargs: object) -> None:
        try:
            callback = getattr(service_status_registry, method)
            callback(*args, **kwargs)
        except Exception:
            pass

    @classmethod
    def _normalize_interval(cls, value: object) -> float:
        if isinstance(value, bool):
            return cls.DEFAULT_INTERVAL_SECONDS
        try:
            interval = float(value)
        except (TypeError, ValueError, OverflowError):
            return cls.DEFAULT_INTERVAL_SECONDS
        if not math.isfinite(interval):
            return cls.DEFAULT_INTERVAL_SECONDS
        return min(cls.MAX_INTERVAL_SECONDS, max(cls.MIN_INTERVAL_SECONDS, interval))

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread and thread.is_alive():
                return
            self._thread = None
            self._stop_event.clear()
            thread = Thread(target=self._run, name="JarvisIndexConsistency", daemon=True)
            self._thread = thread
            thread.start()
        self._status_call("set_state", "index_consistency", "idle", "İndeks bütünlük denetimi hazır.")

    def stop(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            self._stop_event.set()
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=max(2.0, min(self._interval_seconds + 0.5, 5.0)))
        with self._lifecycle_lock:
            current = self._thread
            if current is None or not current.is_alive():
                self._thread = None
                self._stop_event.clear()
                state = "stopped"
                message = "İndeks bütünlük denetimi durduruldu."
            else:
                state = "stopping"
                message = "İndeks bütünlük denetiminin durması bekleniyor."
        self._status_call("set_state", "index_consistency", state, message)

    def run_once(self) -> int:
        self._status_call("set_state", "index_consistency", "running", "İndeks bütünlüğü denetleniyor.")
        try:
            raw = self._reconcile()
            if isinstance(raw, bool):
                raise TypeError("reconcile result must be an integer count")
            repaired = max(0, int(raw))
        except Exception as exc:
            self._status_call("failed", "index_consistency", exc, 0)
            return 0
        message = "İndeks güncel." if repaired == 0 else f"İndekste {repaired} fark otomatik düzeltildi."
        self._status_call("completed", "index_consistency", 0, message)
        self._status_call("set_state", "index_consistency", "idle", message, last_repaired_count=repaired)
        return repaired

    def _run(self) -> None:
        try:
            while not self._stop_event.wait(self._interval_seconds):
                self.run_once()
        finally:
            with self._lifecycle_lock:
                if self._thread is current_thread():
                    stopped_by_request = self._stop_event.is_set()
                    self._thread = None
                else:
                    stopped_by_request = False
            if stopped_by_request:
                self._status_call("set_state", "index_consistency", "stopped", "İndeks bütünlük denetimi durduruldu.")
