from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class SelfImprovementRuntimeStatus:
    schema_version: int
    status: str
    supervisor_running: bool
    watcher_running: bool
    pending_cycles: int
    pending_promotions: int
    waiting_approvals: int
    failed_jobs: int
    journal_path: str
    runtime_root: str
    message: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SelfImprovementApplicationLifecycle:
    """Own the long-running self-improvement services for the desktop app.

    Startup failures are intentionally contained: the Jarvis desktop and voice
    runtime remain usable while self-improvement reports a degraded status.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        journal_path: str | Path,
        runtime_root: str | Path,
        model_config: object | None = None,
        supervisor_factory: Callable[..., Any] | None = None,
        watcher_factory: Callable[..., Any] | None = None,
        handler_registry_factory: Callable[..., Any] | None = None,
        idle_seconds: float = 2.0,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.journal_path = Path(journal_path).expanduser().resolve(strict=False)
        self.runtime_root = Path(runtime_root).expanduser().resolve(strict=False)
        self.status_path = self.runtime_root / "application_lifecycle_status.json"
        self.model_config = model_config
        self._supervisor_factory = supervisor_factory
        self._watcher_factory = watcher_factory
        self._handler_registry_factory = handler_registry_factory
        self._idle_seconds = float(idle_seconds)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._supervisor: Any | None = None
        self._watcher: Any | None = None
        self._status = SelfImprovementRuntimeStatus(
            _SCHEMA_VERSION, "stopped", False, False, 0, 0, 0, 0,
            str(self.journal_path), str(self.runtime_root), "not started", _utc_now(),
        )

    @classmethod
    def create_default(
        cls,
        *,
        project_root: str | Path,
        data_root: str | Path,
        model_config: object | None = None,
    ) -> "SelfImprovementApplicationLifecycle":
        data = Path(data_root).expanduser().resolve(strict=False)
        journal_override = os.environ.get("ARTMACH_SELF_IMPROVEMENT_JOURNAL", "").strip()
        journal = Path(journal_override) if journal_override else data / "self_improvement" / "journal" / "research.json"
        return cls(
            project_root=project_root,
            journal_path=journal,
            runtime_root=data / "self_improvement" / "runtime",
            model_config=model_config,
        )

    @property
    def status(self) -> SelfImprovementRuntimeStatus:
        with self._lock:
            return self._status

    def _set_status(self, status: str, message: str) -> SelfImprovementRuntimeStatus:
        supervisor = self._supervisor
        watcher = self._watcher
        jobs = supervisor.scheduler.jobs() if supervisor is not None else ()
        snapshot = SelfImprovementRuntimeStatus(
            _SCHEMA_VERSION,
            status,
            bool(supervisor and supervisor.is_running),
            bool(watcher and watcher.is_running),
            sum(job.kind == "cycle" and job.status in {"pending", "running"} for job in jobs),
            sum(job.kind == "promotion" and job.status in {"pending", "running"} for job in jobs),
            sum(job.kind == "approval" and job.status == "waiting_approval" for job in jobs),
            sum(job.status == "failed" for job in jobs),
            str(self.journal_path),
            str(self.runtime_root),
            str(message)[:2000],
            _utc_now(),
        )
        with self._lock:
            self._status = snapshot
            _atomic_write(self.status_path, snapshot.to_dict())
        return snapshot

    def start(self) -> SelfImprovementRuntimeStatus:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._set_status("running", "self-improvement lifecycle already running")
        if not self.project_root.is_dir():
            return self._set_status("degraded", "self-improvement project root is unavailable")
        if not self.journal_path.is_file():
            return self._set_status("degraded", f"research journal is not available: {self.journal_path}")
        try:
            if self._supervisor_factory is None:
                from .self_improvement_supervisor import SelfImprovementSupervisor
                self._supervisor_factory = SelfImprovementSupervisor
            if self._watcher_factory is None:
                from .self_improvement_repository_watcher import (
                    RepositorySelfImprovementWatcher,
                )
                self._watcher_factory = RepositorySelfImprovementWatcher
            if self._handler_registry_factory is None:
                from .self_improvement_handlers import RuntimeHandlerRegistry
                self._handler_registry_factory = RuntimeHandlerRegistry
            registry = self._handler_registry_factory(model_config=self.model_config)
            handlers = registry.handlers()
            supervisor = self._supervisor_factory(
                self.runtime_root,
                cycle_handler=handlers["cycle"],
                promotion_handler=handlers["promotion"],
                approval_handler=handlers["approval"],
                idle_seconds=self._idle_seconds,
            )
            watcher = self._watcher_factory(
                project_root=self.project_root,
                journal_path=self.journal_path,
                runtime_root=self.runtime_root,
                supervisor=supervisor,
            )
            thread = threading.Thread(
                target=self._run_supervisor,
                args=(supervisor,),
                name="jarvis-self-improvement-supervisor",
                daemon=True,
            )
            with self._lock:
                self._supervisor = supervisor
                self._watcher = watcher
                self._thread = thread
            thread.start()
            watcher.start()
            return self._set_status("running", "supervisor and repository watcher started")
        except Exception as exc:
            self.stop()
            return self._set_status("degraded", f"self-improvement startup failed: {type(exc).__name__}: {exc}")

    def _run_supervisor(self, supervisor: Any) -> None:
        try:
            supervisor.run_forever()
        except Exception as exc:
            self._set_status("degraded", f"supervisor stopped unexpectedly: {type(exc).__name__}: {exc}")

    def refresh_status(self) -> SelfImprovementRuntimeStatus:
        current = self.status.status
        if self._supervisor is not None and self._supervisor.is_running:
            current = "running"
        return self._set_status(current, self.status.message)

    def stop(self, *, join_timeout: float = 5.0) -> SelfImprovementRuntimeStatus:
        with self._lock:
            watcher = self._watcher
            supervisor = self._supervisor
            thread = self._thread
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass
        if supervisor is not None:
            supervisor.stop()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(max(0.0, float(join_timeout)))
        return self._set_status("stopped", "self-improvement lifecycle stopped")

    @staticmethod
    def read_status(path: str | Path) -> dict[str, object] | None:
        target = Path(path).expanduser().resolve(strict=False)
        if not target.is_file():
            return None
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
        return dict(payload) if isinstance(payload, Mapping) else None
