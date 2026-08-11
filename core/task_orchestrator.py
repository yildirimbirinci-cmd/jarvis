from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from artmach_assistant.config import DATA_DIR

TASK_HISTORY_FILE = DATA_DIR / "task_history.json"
ACTIVE_TASK_FILE = DATA_DIR / "active_task.json"
PENDING_TASKS_FILE = DATA_DIR / "pending_tasks.json"
_MAX_HISTORY = 200


class ParentCancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def add_cancel_callback(self, callback: Callable[[str], None]) -> None: ...


@dataclass
class TaskRecord:
    task_id: str
    name: str
    source: str
    state: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str = ""
    progress: int = 0
    status_message: str = ""
    heartbeat_at: float | None = None
    timeout_seconds: float = 0.0
    turn_id: str = ""


class CancellationToken:
    """Cooperative task token with one durable cancellation reason."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._reason = ""
        self._callbacks: list[Callable[[str], None]] = []

    def cancel(self, reason: str = "kullanıcı iptali") -> bool:
        clean_reason = str(reason).strip() or "kullanıcı iptali"
        callbacks: list[Callable[[str], None]]
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = clean_reason
            self._event.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback(clean_reason)
            except Exception:
                pass
        return True

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def add_cancel_callback(self, callback: Callable[[str], None]) -> None:
        run_now = False
        reason = ""
        with self._lock:
            if self._event.is_set():
                run_now = True
                reason = self._reason
            else:
                self._callbacks.append(callback)
        if run_now:
            try:
                callback(reason)
            except Exception:
                pass

    def link_parent(self, parent: ParentCancellationToken | None) -> None:
        if parent is None:
            return
        parent.add_cancel_callback(lambda reason: self.cancel(reason or "üst konuşma turu iptal edildi"))
        if parent.cancelled:
            self.cancel("üst konuşma turu iptal edildi")

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            reason = self.reason or "kullanıcı iptali"
            raise InterruptedError(f"Görev kullanıcı tarafından iptal edildi: {reason}.")


class TaskOrchestrator:
    """Tek aktif ağır görev, iptal işareti ve kalıcı görev geçmişi yönetir."""

    def __init__(
        self,
        history_file: Path = TASK_HISTORY_FILE,
        active_file: Path = ACTIVE_TASK_FILE,
        pending_file: Path | None = None,
    ) -> None:
        self.history_file = history_file
        self.active_file = active_file
        self.pending_file = pending_file or history_file.with_name(PENDING_TASKS_FILE.name)
        self._lock = threading.RLock()
        self._active: TaskRecord | None = None
        self._token: CancellationToken | None = None
        self._history: list[TaskRecord] = self._load_history()
        self._pending: list[TaskRecord] = self._load_pending()
        self._recovered_task: TaskRecord | None = self._recover_interrupted_task()
        if self._recovered_task is not None:
            recovered_id = self._recovered_task.task_id
            before = len(self._pending)
            self._pending = [row for row in self._pending if row.task_id != recovered_id]
            if len(self._pending) != before:
                try:
                    self._save_pending()
                except Exception:
                    pass

    @staticmethod
    def _record_from_mapping(row: dict[str, Any]) -> TaskRecord:
        fields = TaskRecord.__dataclass_fields__
        clean = {name: row[name] for name in fields if name in row}
        return TaskRecord(**clean)

    @staticmethod
    def _quarantine_corrupt(path: Path) -> None:
        if not path.exists():
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = path.with_name(f"{path.stem}.corrupt_{stamp}{path.suffix}")
        try:
            os.replace(path, target)
        except OSError:
            pass

    def _load_history(self) -> list[TaskRecord]:
        if not self.history_file.exists():
            return []
        try:
            raw = json.loads(self.history_file.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Görev geçmişi liste biçiminde değil.")
            records: list[TaskRecord] = []
            for row in raw[-_MAX_HISTORY:]:
                if not isinstance(row, dict):
                    continue
                try:
                    records.append(self._record_from_mapping(row))
                except (TypeError, ValueError):
                    continue
            return records
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            self._quarantine_corrupt(self.history_file)
            return []

    def _load_pending(self) -> list[TaskRecord]:
        if not self.pending_file.exists():
            return []
        try:
            raw = json.loads(self.pending_file.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Pending task record is not a list.")
            records: list[TaskRecord] = []
            seen: set[str] = set()
            for row in raw:
                if not isinstance(row, dict):
                    continue
                try:
                    record = self._record_from_mapping(row)
                except (TypeError, ValueError):
                    continue
                if record.state != "queued" or not record.task_id or record.task_id in seen:
                    continue
                record.started_at = None
                record.finished_at = None
                record.progress = 0
                seen.add(record.task_id)
                records.append(record)
            return records
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            self._quarantine_corrupt(self.pending_file)
            return []

    def _save_pending(self) -> None:
        if not self._pending:
            try:
                self.pending_file.unlink()
            except FileNotFoundError:
                pass
            return
        self._write_json_atomic(self.pending_file, [asdict(item) for item in self._pending])

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def _save_active(self) -> None:
        if self._active is None:
            try:
                self.active_file.unlink()
            except FileNotFoundError:
                pass
            return
        self._write_json_atomic(self.active_file, asdict(self._active))

    def _recover_interrupted_task(self) -> TaskRecord | None:
        if not self.active_file.exists():
            return None
        try:
            raw = json.loads(self.active_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Aktif görev kaydı nesne biçiminde değil.")
            record = self._record_from_mapping(raw)
            record.state = "interrupted"
            record.finished_at = time.time()
            record.error = "Uygulama önceki görev tamamlanmadan kapandı."
            record.status_message = "Önceki oturumdan kalan görev güvenli biçimde kurtarıldı."
            self._history.append(record)
            self._history = self._history[-_MAX_HISTORY:]
            try:
                self._save()
            finally:
                try:
                    self.active_file.unlink()
                except FileNotFoundError:
                    pass
            return TaskRecord(**asdict(record))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            self._quarantine_corrupt(self.active_file)
            return None

    @property
    def recovered_task(self) -> TaskRecord | None:
        return None if self._recovered_task is None else TaskRecord(**asdict(self._recovered_task))

    def _save(self) -> None:
        payload = [asdict(item) for item in self._history[-_MAX_HISTORY:]]
        self._write_json_atomic(self.history_file, payload)

    @property
    def pending(self) -> list[TaskRecord]:
        with self._lock:
            return [TaskRecord(**asdict(row)) for row in self._pending]

    @property
    def active(self) -> TaskRecord | None:
        with self._lock:
            return None if self._active is None else TaskRecord(**asdict(self._active))

    @property
    def token(self) -> CancellationToken | None:
        with self._lock:
            return self._token

    def start(
        self,
        name: str,
        source: str = "ui",
        timeout_seconds: float = 900.0,
        *,
        parent_token: ParentCancellationToken | None = None,
        turn_id: str = "",
    ) -> tuple[TaskRecord, CancellationToken]:
        clean_name = str(name).strip() or "Jarvis görevi"
        with self._lock:
            if self._active is not None and self._active.state in {"running", "cancelling"}:
                raise RuntimeError(f"Jarvis halen '{self._active.name}' görevini işliyor.")
            record = TaskRecord(
                task_id=uuid.uuid4().hex,
                name=clean_name,
                source=str(source).strip() or "ui",
                state="running",
                created_at=time.time(),
                started_at=time.time(),
                heartbeat_at=time.time(),
                timeout_seconds=max(0.0, float(timeout_seconds)),
                turn_id=str(turn_id).strip(),
            )
            token = CancellationToken()
            self._active = record
            self._token = token
            token.add_cancel_callback(
                lambda reason, task_id=record.task_id: self._mark_cancelling_from_token(
                    task_id, reason
                )
            )
            token.link_parent(parent_token)
            self._save_active()
            return TaskRecord(**asdict(self._active)), token

    def enqueue(
        self,
        name: str,
        source: str = "ui",
        timeout_seconds: float = 900.0,
        *,
        turn_id: str = "",
    ) -> TaskRecord:
        clean_name = str(name).strip() or "Jarvis task"
        with self._lock:
            record = TaskRecord(
                task_id=uuid.uuid4().hex,
                name=clean_name,
                source=str(source).strip() or "ui",
                state="queued",
                created_at=time.time(),
                timeout_seconds=max(0.0, float(timeout_seconds)),
                turn_id=str(turn_id).strip(),
                status_message="Task is waiting in the FIFO queue.",
            )
            self._pending.append(record)
            try:
                self._save_pending()
            except Exception:
                self._pending.pop()
                raise
            return TaskRecord(**asdict(record))

    def start_next(
        self,
        expected_task_id: str = "",
        *,
        parent_token: ParentCancellationToken | None = None,
    ) -> tuple[TaskRecord, CancellationToken] | None:
        with self._lock:
            if self._active is not None and self._active.state in {"running", "cancelling"}:
                return None
            if not self._pending:
                return None
            record = self._pending[0]
            expected = str(expected_task_id).strip()
            if expected and record.task_id != expected:
                return None
            now = time.time()
            record.state = "running"
            record.started_at = now
            record.heartbeat_at = now
            record.status_message = "Task started from FIFO queue."
            token = CancellationToken()
            self._active = record
            self._token = token
            token.add_cancel_callback(
                lambda reason, task_id=record.task_id: self._mark_cancelling_from_token(task_id, reason)
            )
            token.link_parent(parent_token)
            self._save_active()
            self._pending.pop(0)
            try:
                self._save_pending()
            except Exception:
                pass
            return TaskRecord(**asdict(record)), token

    def cancel_pending(self, task_id: str, reason: str = "user cancellation") -> TaskRecord | None:
        clean_id = str(task_id).strip()
        clean_reason = str(reason).strip() or "user cancellation"
        with self._lock:
            index = next((idx for idx, row in enumerate(self._pending) if row.task_id == clean_id), None)
            if index is None:
                return None
            record = self._pending.pop(index)
            record.state = "cancelled"
            record.finished_at = time.time()
            record.error = clean_reason
            record.status_message = "Pending task was cancelled before execution."
            self._save_pending()
            self._history.append(record)
            self._history = self._history[-_MAX_HISTORY:]
            try:
                self._save()
            except Exception:
                pass
            return TaskRecord(**asdict(record))

    def link_active_to(self, parent_token: ParentCancellationToken | None) -> bool:
        with self._lock:
            token = self._token
            if token is None or self._active is None:
                return False
        token.link_parent(parent_token)
        return True

    def _mark_cancelling_from_token(self, task_id: str, reason: str) -> bool:
        """Reflect a linked conversation cancellation in the durable task row."""
        with self._lock:
            if self._active is None or self._active.task_id != task_id:
                return False
            if self._active.state not in {"running", "cancelling"}:
                return False
            self._active.state = "cancelling"
            clean_reason = str(reason).strip()
            self._active.status_message = (
                f"Görev iptal ediliyor: {clean_reason[:200]}"
                if clean_reason else "Görev iptal ediliyor"
            )
            self._active.heartbeat_at = time.time()
            try:
                self._save_active()
            except Exception:
                # Cancellation must remain effective even if the audit row
                # cannot be persisted at this instant.
                pass
            return True

    def update_progress(self, task_id: str, progress: int, message: str = "") -> TaskRecord | None:
        with self._lock:
            if self._active is None or self._active.task_id != task_id:
                return None
            self._active.progress = max(0, min(100, int(progress)))
            self._active.status_message = str(message).strip()
            self._active.heartbeat_at = time.time()
            self._save_active()
            return TaskRecord(**asdict(self._active))

    def heartbeat(self, task_id: str, message: str = "") -> TaskRecord | None:
        with self._lock:
            if self._active is None or self._active.task_id != task_id:
                return None
            self._active.heartbeat_at = time.time()
            if message:
                self._active.status_message = str(message).strip()
            self._save_active()
            return TaskRecord(**asdict(self._active))

    def timed_out(self, now: float | None = None) -> TaskRecord | None:
        with self._lock:
            if self._active is None or self._active.state not in {"running", "cancelling"}:
                return None
            timeout = float(self._active.timeout_seconds or 0.0)
            if timeout <= 0:
                return None
            current = time.time() if now is None else float(now)
            heartbeat = self._active.heartbeat_at or self._active.started_at or self._active.created_at
            if current - heartbeat < timeout:
                return None
            return TaskRecord(**asdict(self._active))

    def fail_timed_out(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            if self._active is None or self._active.task_id != task_id:
                return None
            if self._token is not None:
                self._token.cancel("görev zaman aşımı")
            self._active.state = "failed"
            self._active.error = "Görev zaman aşımına uğradı."
            self._active.status_message = "Görev yanıt vermedi ve güvenli biçimde durduruldu."
            self._active.finished_at = time.time()
            finished = TaskRecord(**asdict(self._active))
            self._history.append(finished)
            self._history = self._history[-_MAX_HISTORY:]
            self._active = None
            self._token = None
            self._save_active()
            try:
                self._save()
            except Exception:
                pass
            return finished

    def cancel_active(self, reason: str = "kullanıcı iptali") -> bool:
        with self._lock:
            if self._active is None or self._active.state not in {"running", "cancelling"}:
                return False
            task_id = self._active.task_id
            token = self._token
            was_cancelling = self._active.state == "cancelling"
        token_changed = token.cancel(reason) if token is not None else False
        if token is None:
            self._mark_cancelling_from_token(task_id, reason)
        return bool(token_changed or not was_cancelling)

    def finish(self, task_id: str, error: str = "", cancelled: bool = False) -> TaskRecord | None:
        with self._lock:
            if self._active is None or self._active.task_id != task_id:
                return None
            self._active.finished_at = time.time()
            self._active.progress = 100
            self._active.error = str(error).strip()
            token = self._token
            if cancelled or (token is not None and token.cancelled):
                self._active.state = "cancelled"
                if not self._active.error and token is not None:
                    self._active.error = token.reason
            elif self._active.error:
                self._active.state = "failed"
            else:
                self._active.state = "completed"
            finished = TaskRecord(**asdict(self._active))
            self._history.append(finished)
            self._history = self._history[-_MAX_HISTORY:]
            self._active = None
            self._token = None
            self._save_active()
            try:
                self._save()
            except Exception:
                pass
            return finished

    def recent(self, limit: int = 20) -> list[TaskRecord]:
        with self._lock:
            return [TaskRecord(**asdict(row)) for row in self._history[-max(1, int(limit)):]]

    def wrap(self, task_id: str, token: CancellationToken, action: Callable[[], Any]) -> Callable[[], Any]:
        def execute() -> Any:
            token.raise_if_cancelled()
            result = action()
            token.raise_if_cancelled()
            return result
        return execute
