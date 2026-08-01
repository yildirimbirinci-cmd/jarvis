from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from typing import Callable

from artmach_assistant.core.project_index import IGNORED_DIRS
from artmach_assistant.core.service_status import service_status_registry


@dataclass(frozen=True)
class WorkspaceChange:
    kind: str
    path: Path
    previous_path: Path | None = None


FileState = tuple[int, int]
ChangeCallback = Callable[[list[WorkspaceChange]], None]


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


def _status_call(method: str, *args: object, **kwargs: object) -> None:
    try:
        getattr(service_status_registry, method)(*args, **kwargs)
    except Exception:
        pass


def workspace_change_key(path: Path) -> str:
    try:
        text = str(path)
    except Exception:
        text = ""
    return text.replace("\x00", "").replace("\\", "/").casefold()[:32768]


def merge_workspace_changes(
    previous: WorkspaceChange | None,
    current: WorkspaceChange,
) -> WorkspaceChange | None:
    if previous is None:
        return current
    transition = (previous.kind, current.kind)
    if transition == ("created", "modified"):
        return previous
    if transition == ("created", "deleted"):
        return None
    if transition == ("deleted", "created"):
        return WorkspaceChange("modified", current.path, previous.path)
    if current.kind == "deleted":
        return current
    if previous.kind == "deleted":
        return previous
    if previous.kind == "created":
        return WorkspaceChange("created", current.path, previous.previous_path)
    return current


class WorkspaceWatchService:
    """Low-cost polling workspace watcher with debounce and fault isolation."""

    def __init__(
        self,
        callback: ChangeCallback,
        *,
        poll_interval: float = 0.75,
        debounce_seconds: float = 0.60,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback
        self._poll_interval = _bounded_seconds(
            poll_interval, default=0.75, minimum=0.20, maximum=3600.0
        )
        self._debounce_seconds = _bounded_seconds(
            debounce_seconds, default=0.60, minimum=0.10, maximum=3600.0
        )
        self._root: Path | None = None
        self._snapshot: dict[Path, FileState] = {}
        self._pending: dict[str, WorkspaceChange] = {}
        self._last_change_at = 0.0
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        _status_call("ensure", "workspace_watch")

    @property
    def is_running(self) -> bool:
        with self._lock:
            thread = self._thread
            return bool(thread and thread.is_alive())

    @property
    def root(self) -> Path | None:
        with self._lock:
            return self._root

    def snapshot(self) -> dict[Path, FileState]:
        with self._lock:
            return dict(self._snapshot)

    def start(self, root: Path) -> None:
        resolved = Path(root).resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"İzlenecek çalışma alanı geçersiz: {resolved}")
        with self._lock:
            if (
                self._thread
                and self._thread.is_alive()
                and self._root == resolved
                and not self._stop_event.is_set()
            ):
                return
        self.stop()
        with self._lock:
            if self._thread and self._thread.is_alive():
                _status_call(
                    "set_state", "workspace_watch", "stopping",
                    "Önceki çalışma alanı izleyicisinin güvenli biçimde durması bekleniyor.",
                )
                return
            self._root = resolved
            self._snapshot = self._scan(resolved)
            self._pending.clear()
            self._last_change_at = 0.0
            self._stop_event.clear()
            self._thread = Thread(target=self._run, name="JarvisWorkspaceWatch", daemon=True)
            self._thread.start()
        _status_call("set_state", "workspace_watch", "idle", "Çalışma alanı izleyicisi hazır.")

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=max(2.0, min(self._poll_interval * 3.0, 5.0)))
        with self._lock:
            current = self._thread
            if current is None or not current.is_alive():
                self._thread = None
                self._root = None
                self._snapshot.clear()
                self._pending.clear()
                self._last_change_at = 0.0
                self._stop_event.clear()
                state, message = "stopped", "Çalışma alanı izleyicisi durduruldu."
            else:
                state, message = "stopping", "Çalışma alanı izleyicisinin durması bekleniyor."
        _status_call("set_state", "workspace_watch", state, message)

    def _run(self) -> None:
        unexpected = False
        try:
            while not self._stop_event.wait(self._poll_interval):
                root = self.root
                if root is None:
                    return
                current = self._scan(root)
                with self._lock:
                    previous = self._snapshot
                    self._snapshot = current
                changes = self._diff(previous, current)
                if changes:
                    now = monotonic()
                    with self._lock:
                        for change in changes:
                            key = workspace_change_key(change.path)
                            if not key:
                                continue
                            merged = merge_workspace_changes(self._pending.get(key), change)
                            if merged is None:
                                self._pending.pop(key, None)
                            else:
                                self._pending[key] = merged
                        self._last_change_at = now
                    _status_call(
                        "set_state",
                        "workspace_watch",
                        "running",
                        f"{len(changes)} dosya değişikliği algılandı.",
                    )
                self._flush_if_ready()
            self._flush(force=True)
        except BaseException as exc:
            unexpected = not self._stop_event.is_set()
            _status_call("failed", "workspace_watch", exc, 0)
        finally:
            with self._lock:
                if self._thread is current_thread():
                    self._thread = None
            if unexpected:
                _status_call(
                    "set_state",
                    "workspace_watch",
                    "error",
                    "Çalışma alanı izleyicisi beklenmedik biçimde durdu.",
                )

    def _flush_if_ready(self) -> None:
        with self._lock:
            ready = bool(self._pending) and monotonic() - self._last_change_at >= self._debounce_seconds
        if ready:
            self._flush()

    def _flush(self, force: bool = False) -> None:
        with self._lock:
            if not self._pending:
                return
            if not force and monotonic() - self._last_change_at < self._debounce_seconds:
                return
            changes = sorted(self._pending.values(), key=lambda item: workspace_change_key(item.path))
            self._pending.clear()
        try:
            self._callback(changes)
            _status_call("completed", "workspace_watch", 0, f"{len(changes)} değişiklik kuyruğa aktarıldı.")
        except Exception as exc:
            with self._lock:
                for change in changes:
                    key = workspace_change_key(change.path)
                    if not key:
                        continue
                    merged = merge_workspace_changes(self._pending.get(key), change)
                    if merged is None:
                        self._pending.pop(key, None)
                    else:
                        self._pending[key] = merged
                self._last_change_at = monotonic()
            _status_call("failed", "workspace_watch", exc, 0)
        except BaseException as exc:
            _status_call("failed", "workspace_watch", exc, 0)

    @staticmethod
    def _scan(root: Path) -> dict[Path, FileState]:
        snapshot: dict[Path, FileState] = {}
        try:
            for path in root.rglob("*"):
                try:
                    relative = path.relative_to(root)
                    if any(part in IGNORED_DIRS for part in relative.parts):
                        continue
                    if path.is_symlink() or not path.is_file():
                        continue
                    stat = path.stat()
                    snapshot[relative] = (stat.st_mtime_ns, stat.st_size)
                except (OSError, ValueError, RuntimeError):
                    continue
        except (OSError, RuntimeError):
            return snapshot
        return snapshot

    @staticmethod
    def _diff(previous: dict[Path, FileState], current: dict[Path, FileState]) -> list[WorkspaceChange]:
        changes: list[WorkspaceChange] = []
        previous_paths = set(previous)
        current_paths = set(current)
        changes.extend(WorkspaceChange("created", path) for path in current_paths - previous_paths)
        changes.extend(WorkspaceChange("deleted", path) for path in previous_paths - current_paths)
        changes.extend(
            WorkspaceChange("modified", path)
            for path in previous_paths & current_paths
            if previous[path] != current[path]
        )
        return changes
