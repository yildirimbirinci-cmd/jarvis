from __future__ import annotations

from collections.abc import Callable
import math
from queue import Empty, Queue
from threading import Event, RLock, Thread, current_thread
from time import monotonic

from artmach_assistant.core.service_status import service_status_registry


class BackgroundAnalysisQueue:
    """Runs expensive read-only jobs only after Jarvis has been idle."""

    SERVICE_NAME = "background_analysis"
    MAX_KEY_LENGTH = 512

    def __init__(self, *, idle_seconds: float = 90.0, check_interval: float = 1.0) -> None:
        self._idle_seconds = self._safe_interval(idle_seconds, default=90.0, minimum=0.0)
        self._check_interval = self._safe_interval(check_interval, default=1.0, minimum=0.01)
        self._queue: Queue[tuple[str, Callable[[], object]] | None] = Queue()
        self._pending_keys: set[str] = set()
        self._last_activity = monotonic()
        self._stop_event = Event()
        self._activity_event = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._safe_status("ensure", self.SERVICE_NAME)

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._thread = Thread(target=self._run, name="JarvisBackgroundAnalysis", daemon=True)
            self._thread.start()
        self._safe_status("set_state", self.SERVICE_NAME, "idle", "Arka plan analiz kuyruğu hazır.")

    def mark_activity(self) -> None:
        with self._lock:
            self._last_activity = monotonic()
            self._activity_event.set()

    def submit(self, key: str, callback: Callable[[], object]) -> bool:
        normalized = self._normalize_key(key)
        if not callable(callback):
            raise TypeError("Arka plan analiz callback'i çağrılabilir olmalıdır.")
        with self._lock:
            if self._stop_event.is_set() or normalized in self._pending_keys:
                return False
            self._pending_keys.add(normalized)
            self._safe_status("queued", self.SERVICE_NAME, 1)
            self._queue.put((normalized, callback))
            self.start()
        return True

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
            self._activity_event.set()
            self._queue.put(None)
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            discarded = self._drain()
            if thread is None or not thread.is_alive():
                self._thread = None
                self._stop_event.clear()
                state, message = "stopped", "Arka plan analiz kuyruğu durduruldu."
            else:
                state, message = "stopping", "Çalışan analiz tamamlanınca arka plan kuyruğu durdurulacak."
        if discarded:
            self._safe_status(
                "discarded", self.SERVICE_NAME, discarded,
                f"{discarded} bekleyen analiz işi iptal edildi.",
            )
        self._safe_status("set_state", self.SERVICE_NAME, state, message)

    def _idle_remaining(self) -> float:
        with self._lock:
            elapsed = monotonic() - self._last_activity
        return max(0.0, self._idle_seconds - elapsed)

    def _idle(self) -> bool:
        return self._idle_remaining() <= 0

    def _wait_until_idle(self) -> bool:
        while not self._stop_event.is_set():
            if self._idle():
                return True
            remaining = self._idle_remaining()
            self._activity_event.clear()
            self._activity_event.wait(min(remaining, self._check_interval))
        return False

    def _run(self) -> None:
        current: tuple[str, Callable[[], object]] | None = None
        try:
            while not self._stop_event.is_set():
                if current is None:
                    try:
                        current = self._queue.get(timeout=self._check_interval)
                    except Empty:
                        continue
                    if current is None:
                        self._queue.task_done()
                        return
                if not self._wait_until_idle():
                    break
                key, callback = current
                retry = False
                try:
                    self._safe_status("set_state", self.SERVICE_NAME, "running", f"{key} çalışıyor.", job_key=key)
                    callback()
                    self._safe_status("completed", self.SERVICE_NAME, 1, f"{key} tamamlandı.")
                except Exception as exc:
                    self._safe_status("failed", self.SERVICE_NAME, exc, 1)
                    retry = not self._stop_event.is_set()
                finally:
                    with self._lock:
                        if retry:
                            self._safe_status("queued", self.SERVICE_NAME, 1)
                            self._queue.put((key, callback))
                        else:
                            self._pending_keys.discard(key)
                    self._queue.task_done()
                    current = None
                    if retry:
                        self._stop_event.wait(self._check_interval)
        finally:
            if current is not None:
                with self._lock:
                    self._pending_keys.discard(current[0])
                self._safe_status(
                    "discarded", self.SERVICE_NAME, 1,
                    f"{current[0]} çalıştırılmadan iptal edildi.",
                )
                self._queue.task_done()

    @classmethod
    def _normalize_key(cls, key: object) -> str:
        try:
            normalized = str(key).strip()
        except Exception as exc:
            raise ValueError("Arka plan analiz anahtarı metne dönüştürülemedi.") from exc
        if not normalized:
            raise ValueError("Arka plan analiz anahtarı boş olamaz.")
        if len(normalized) > cls.MAX_KEY_LENGTH:
            raise ValueError("Arka plan analiz anahtarı çok uzun.")
        return normalized

    @staticmethod
    def _safe_interval(value: object, *, default: float, minimum: float) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            result = default
        if not math.isfinite(result):
            result = default
        return max(minimum, result)

    def _drain(self) -> int:
        discarded = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            if item is not None:
                discarded += 1
                with self._lock:
                    self._pending_keys.discard(item[0])
            self._queue.task_done()
        with self._lock:
            self._pending_keys.clear()
        return discarded

    @staticmethod
    def _safe_status(method: str, *args: object, **kwargs: object) -> None:
        try:
            callback = getattr(service_status_registry, method)
            callback(*args, **kwargs)
        except Exception:
            pass
